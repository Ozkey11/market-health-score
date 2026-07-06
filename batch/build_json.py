"""
build_json.py — 配信用JSON生成
SQLite（正本）→ data/*.json（配信用）の変換を担う。
設計書 4章「JSON設計」準拠:
  - version / updated_at / data_date / model_version を必ず含める
  - 取得できなかった項目は null とし、data_quality に理由を残す
  - スコア結果と特徴量は分けて持つ
"""
import json
import os
from datetime import datetime, timezone
from sources import SOURCES

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_VERSION = "mhs-core-1.0"
HISTORY_DAYS = 400  # JSONへは300-500営業日（設計書2.1）


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _version_tag():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")


def _write(name, obj):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    print(f"  wrote {name} ({os.path.getsize(path)} bytes)")


def _latest_price(conn, symbol):
    row = conn.execute(
        """SELECT date, close, volume FROM prices_daily
           WHERE symbol=? AND close IS NOT NULL ORDER BY date DESC LIMIT 2""",
        (symbol,),
    ).fetchall()
    if not row:
        return None
    d, c, v = row[0]
    prev_c = row[1][1] if len(row) > 1 and row[1][1] else None
    chg = ((c - prev_c) / prev_c * 100) if (prev_c and c) else None
    return {"date": d, "close": c, "volume": v, "change_pct": round(chg, 2) if chg is not None else None}


def _latest_macro(conn, series_id):
    row = conn.execute(
        "SELECT value FROM macro_series WHERE series_id=? AND value IS NOT NULL ORDER BY date DESC LIMIT 1",
        (series_id,),
    ).fetchone()
    return row[0] if row else None


def _latest_sentiment(conn, market, metric):
    row = conn.execute(
        """SELECT value, date, is_manual FROM sentiment_daily
           WHERE market=? AND metric_name=? AND value IS NOT NULL
           ORDER BY date DESC LIMIT 1""",
        (market, metric),
    ).fetchone()
    if not row:
        return None
    return {"value": row[0], "date": row[1], "is_manual": bool(row[2])}


def build_latest(conn, run_id, symbol="^GSPC"):
    price = _latest_price(conn, symbol)
    us10y = _latest_macro(conn, "DGS10")
    us2y = _latest_macro(conn, "DGS2")
    hy = _latest_macro(conn, "BAMLH0A0HYM2")
    per_row = conn.execute(
        "SELECT value, as_of_date, is_manual FROM fundamental_latest WHERE symbol=? AND metric_name='per_ttm'",
        (symbol,),
    ).fetchone()

    # data_quality 集約（本ラン分）
    ql = conn.execute(
        """SELECT item, status, message, source_link FROM data_quality_log
           WHERE run_id=? ORDER BY id""",
        (run_id,),
    ).fetchall()
    missing = [{"item": i, "message": m, "source_link": s} for (i, st, m, s) in ql if st == "failed"]
    warnings = [{"item": i, "message": m, "source_link": s} for (i, st, m, s) in ql if st in ("stale", "manual")]

    obj = {
        "version": _version_tag(),
        "model_version": MODEL_VERSION,
        "updated_at": _now(),
        "data_date": price["date"] if price else None,
        "symbol": symbol,
        "price_latest": {
            "close": price["close"] if price else None,
            "change_pct": price["change_pct"] if price else None,
        },
        "fundamental_latest": {
            "per_ttm": per_row[0] if per_row else None,
            "per_as_of": per_row[1] if per_row else None,
            "per_is_manual": bool(per_row[2]) if per_row else False,
        },
        "macro_latest": {
            "us10y": us10y,
            "us2y": us2y,
            "yield_curve_10y2y": round(us10y - us2y, 3) if (us10y is not None and us2y is not None) else None,
            "hy_spread": hy,
        },
        "sentiment_latest": {
            "vix": _latest_sentiment(conn, "US", "vix"),
            "nk_vi": _latest_sentiment(conn, "JP", "nk_vi"),
            "put_call": _latest_sentiment(conn, "US", "put_call"),
            "fear_greed": _latest_sentiment(conn, "US", "fear_greed"),
        },
        "data_quality": {
            "status": "ok" if not missing else ("partial" if price else "failed"),
            "missing_items": missing,
            "warnings": warnings,
        },
    }
    _write("latest.json", obj)
    return obj


def build_history(conn, symbols=("^GSPC", "SPY", "^N225", "QQQ", "IWM", "SOXL", "1306.T")):
    out = {"updated_at": _now(), "days": HISTORY_DAYS, "symbols": {}}
    for sym in symbols:
        rows = conn.execute(
            """SELECT date, open, high, low, close, volume FROM prices_daily
               WHERE symbol=? AND close IS NOT NULL
               ORDER BY date DESC LIMIT ?""",
            (sym, HISTORY_DAYS),
        ).fetchall()
        if not rows:
            continue
        rows.reverse()  # 古い順
        out["symbols"][sym] = {
            "prices": [
                {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}
                for (d, o, h, l, c, v) in rows
            ]
        }
        # スコア履歴があれば添付（placeholder計算分）
        sc = conn.execute(
            """SELECT date, regime_score, bottom_score, top_score, confirmation_score,
                      sentiment_score, overall_score, confidence
               FROM scores_daily WHERE symbol=? AND model_version=?
               ORDER BY date DESC LIMIT ?""",
            (sym, MODEL_VERSION, HISTORY_DAYS),
        ).fetchall()
        if sc:
            sc.reverse()
            out["symbols"][sym]["scores"] = [
                {"date": d, "regime": r, "bottom": b, "top": t,
                 "confirmation": cf, "sentiment": se, "overall": ov, "confidence": co}
                for (d, r, b, t, cf, se, ov, co) in sc
            ]
    _write("history_1y.json", out)
    return out


def build_data_quality(conn, run_id):
    ql = conn.execute(
        """SELECT item, status, message, source_link, occurred_at
           FROM data_quality_log WHERE run_id=? ORDER BY id""",
        (run_id,),
    ).fetchall()
    obj = {
        "updated_at": _now(),
        "run_id": run_id,
        "items": [
            {"item": i, "status": st, "message": m, "source_link": s, "occurred_at": t}
            for (i, st, m, s, t) in ql
        ],
    }
    _write("data_quality.json", obj)
    return obj


def build_sources_json():
    """取得先リンク集をUI用JSONへ変換。"""
    obj = {"updated_at": _now(), "sources": SOURCES}
    _write("sources.json", obj)
    return obj


def build_api_status(run_id, started_at, finished_at, counters):
    obj = {
        "updated_at": _now(),
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "counters": counters,
    }
    _write("api_status.json", obj)
    return obj
