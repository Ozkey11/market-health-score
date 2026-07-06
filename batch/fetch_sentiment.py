"""
fetch_sentiment.py — センチメント指標取得
VIX（yfinance）/ CNN Fear&Greed / CBOE Put/Call。
公式API扱いでないものは失敗を前提とし、失敗時はdata_qualityへ記録して
UI側で「手動入力」or「取得先リンク」を案内する。
"""
import json
import urllib.request
from datetime import date
from db import upsert_sentiment, log_quality
from sources import link_for

UA = {"User-Agent": "Mozilla/5.0 (MarketHealthBatch/1.0)"}


def _today():
    return date.today().isoformat()


def fetch_vix(conn, run_id):
    item = "sentiment:vix"
    try:
        import yfinance as yf
        df = yf.Ticker("^VIX").history(period="5d", interval="1d")
        if df is None or df.empty:
            raise ValueError("VIX空レスポンス")
        last = df.iloc[-1]
        d = df.index[-1].strftime("%Y-%m-%d")
        upsert_sentiment(conn, "US", d, "vix", float(last["Close"]), "yfinance")
        conn.commit()
        log_quality(conn, run_id, item, "ok", f"VIX={last['Close']:.2f} ({d})")
        return True
    except Exception as e:
        conn.rollback()
        log_quality(conn, run_id, item, "failed", f"{type(e).__name__}: {e}", link_for(item))
        return False


def fetch_nk_vi(conn, run_id):
    item = "sentiment:nk_vi"
    try:
        import yfinance as yf
        df = yf.Ticker("^NKVI.OS").history(period="5d", interval="1d")
        if df is None or df.empty:
            raise ValueError("日経VI空レスポンス")
        last = df.iloc[-1]
        d = df.index[-1].strftime("%Y-%m-%d")
        upsert_sentiment(conn, "JP", d, "nk_vi", float(last["Close"]), "yfinance")
        conn.commit()
        log_quality(conn, run_id, item, "ok", f"日経VI={last['Close']:.2f} ({d})")
        return True
    except Exception as e:
        conn.rollback()
        log_quality(conn, run_id, item, "failed", f"{type(e).__name__}: {e}", link_for(item))
        return False


def fetch_fear_greed(conn, run_id):
    """CNN Fear&Greed。非公式APIのため失敗前提で扱う。"""
    item = "sentiment:fear_greed"
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        score = data.get("fear_and_greed", {}).get("score")
        if score is None:
            raise ValueError("scoreフィールドなし")
        upsert_sentiment(conn, "US", _today(), "fear_greed", float(score), "CNN")
        conn.commit()
        log_quality(conn, run_id, item, "ok", f"F&G={score:.0f}")
        return True
    except Exception as e:
        conn.rollback()
        log_quality(conn, run_id, item, "failed",
                    f"{type(e).__name__}: {e}（非公式APIのため失敗時は手動入力可）",
                    link_for(item))
        return False


def fetch_put_call(conn, run_id):
    """CBOE equity Put/Call。取得形式が変わる可能性が高いので失敗前提。"""
    item = "sentiment:put_call"
    urls = [
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/CPCE_History.csv",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode()
            lines = [l for l in text.strip().splitlines() if l.strip()]
            if len(lines) < 2 or "<" in lines[0]:
                raise ValueError("CSV形式でないレスポンス")
            last = lines[-1].split(",")
            val = float(last[-1])
            if not (0 < val < 5):
                raise ValueError(f"P/C値が範囲外: {val}")
            upsert_sentiment(conn, "US", _today(), "put_call", val, "CBOE")
            conn.commit()
            log_quality(conn, run_id, item, "ok", f"P/C={val:.2f}")
            return True
        except Exception:
            continue
    log_quality(conn, run_id, item, "failed",
                "CBOE CSV取得失敗（形式変更の可能性。MacroMicroで確認し手動入力可）",
                link_for(item))
    return False


def fetch_all_sentiment(conn, run_id):
    results = [
        fetch_vix(conn, run_id),
        fetch_nk_vi(conn, run_id),
        fetch_fear_greed(conn, run_id),
        fetch_put_call(conn, run_id),
    ]
    ok = sum(1 for r in results if r)
    return ok, len(results) - ok
