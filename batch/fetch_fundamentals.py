#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_fundamentals.py v3 — ファンダメンタル指標の自動取得バッチ
GitHub Actionsで毎日実行 → data/fundamentals.json

取得先:
  FRED API  : HYスプレッド(BAMLH0A0HYM2), イールドカーブ(T10Y2Y)
  multpl.com: シラーPER, S&P500 PER(TTM) — トップページから最新値を取得
  Yahoo Fin : MOVE(^MOVE), WTI(CL=F), 銅(HG=F), DXY(DX-Y.NYB) — yfinanceで252日Z
  ISM       : FRED廃止済みのため、investing.com経済カレンダーからスクレイピング
              失敗時は前回値を保持(手入力で上書き可)
"""
import os, json, sys, time, re
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "fundamentals.json")
FRED_KEY = os.environ.get("FRED_API_KEY", "")

def _get(url, headers=None):
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
         "Accept": "text/html,application/json,*/*", "Accept-Language": "en-US,en;q=0.9"}
    if headers: h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8")

# ── 前回データの読み込み (差分更新用) ──
def load_prev():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f).get("indicators", {})
    except: return {}

# ── FRED ──
def fred_latest(series_id):
    if not FRED_KEY:
        print(f"  ⚠ FRED_API_KEY未設定 → {series_id}スキップ"); return None
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={series_id}&api_key={FRED_KEY}&file_type=json"
           f"&sort_order=desc&limit=5")
    try:
        j = json.loads(_get(url))
        for obs in j.get("observations", []):
            if obs["value"] != ".":
                return {"value": float(obs["value"]), "date": obs["date"],
                        "source": f"FRED {series_id}"}
    except Exception as e:
        print(f"  ✖ FRED {series_id}: {e}")
    return None

# ── multpl.com: トップページ or テーブルページから最新値 ──
def multpl(path, name):
    """複数のパターンで試行"""
    for url in [f"https://www.multpl.com/{path}", f"https://www.multpl.com/{path}/table/by-month"]:
        try:
            html = _get(url, {"Referer": "https://www.multpl.com/"})
            # パターン1: "Current ... is XX.XX"
            m = re.search(r'(?:Current|current)[^0-9]*?([\d]{1,3}\.[\d]{1,2})', html)
            if m:
                return {"value": float(m.group(1)), "source": f"multpl.com/{path}"}
            # パターン2: テーブルの最初の数値セル
            m = re.search(r'<td[^>]*>\s*([\d]{1,3}\.\d{1,2})\s*</td>', html)
            if m:
                return {"value": float(m.group(1)), "source": f"multpl.com/{path}"}
            # パターン3: idやclass付きの要素
            m = re.search(r'id="current"[^>]*>([\d.]+)', html)
            if m:
                return {"value": float(m.group(1)), "source": f"multpl.com/{path}"}
        except Exception as e:
            print(f"  ✖ multpl {path} ({url}): {e}")
            continue
    return None

# ── ISM: investing.comの経済カレンダーページからスクレイピング ──
def fetch_ism():
    """ISMはFREDから廃止。investing.comのPMIデータを試行し、失敗なら前回値保持"""
    urls = [
        "https://www.investing.com/economic-calendar/ism-manufacturing-pmi-173",
    ]
    for url in urls:
        try:
            html = _get(url, {"Referer": "https://www.investing.com/"})
            # "Actual" 列の最新値を探す
            m = re.search(r'(?:Actual|actual|結果)[^0-9]*?([\d]{2}\.[\d])', html)
            if m:
                val = float(m.group(1))
                if 30 < val < 70:  # ISMの妥当範囲
                    return {"value": val, "source": "investing.com ISM PMI"}
        except Exception as e:
            print(f"  ✖ ISM ({url}): {e}")
    return None

# ── Yahoo Finance (yfinance) ──
def yahoo_z(symbol):
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        df = tk.history(period="2y")
        if df.empty or len(df) < 60: return None
        closes = df["Close"].dropna().values
        win = closes[-min(252, len(closes)):]
        mu, sd = float(win.mean()), float(win.std())
        z = (float(closes[-1]) - mu) / sd if sd > 0 else 0
        return {"value": round(float(closes[-1]), 4), "z": round(z, 3),
                "source": f"Yahoo {symbol}"}
    except Exception as e:
        print(f"  ✖ Yahoo {symbol}: {e}")
    return None

def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] ファンダメンタル指標取得バッチ v3")
    prev = load_prev()
    ind = {}

    # FRED: HYスプレッド, イールドカーブ
    for key, sid in [("hy_spread", "BAMLH0A0HYM2"), ("yield_curve", "T10Y2Y")]:
        print(f"  FRED {sid}...")
        r = fred_latest(sid)
        if r:
            ind[key] = r; print(f"    → {r['value']} ({r.get('date','')})")
        elif key in prev:
            ind[key] = prev[key]; ind[key]["stale"] = True
            print(f"    → 前回値を保持: {prev[key]['value']}")
        time.sleep(0.3)

    # multpl.com: シラーPER, PER(TTM)
    for key, path, name in [("shiller_pe", "shiller-pe", "シラーPER"),
                             ("pe_ttm", "s-p-500-pe-ratio", "PER(TTM)")]:
        print(f"  multpl {path}...")
        r = multpl(path, name)
        if r:
            ind[key] = r; print(f"    → {r['value']}")
        elif key in prev:
            ind[key] = prev[key]; ind[key]["stale"] = True
            print(f"    → 前回値を保持: {prev[key]['value']}")
        time.sleep(1.5)

    # ISM (FRED廃止のため代替ソース)
    print("  ISM (investing.com)...")
    r = fetch_ism()
    if r:
        # 前回値をism_prevに退避
        if "ism" in prev and prev["ism"]["value"] != r["value"]:
            ind["ism_prev"] = {"value": prev["ism"]["value"],
                               "date": prev["ism"].get("date", ""),
                               "source": "前回ISM値"}
        elif "ism_prev" in prev:
            ind["ism_prev"] = prev["ism_prev"]
        ind["ism"] = r
        print(f"    → {r['value']}")
    else:
        # 前回値保持
        for k in ["ism", "ism_prev"]:
            if k in prev:
                ind[k] = prev[k]; ind[k]["stale"] = True
                print(f"    → {k} 前回値を保持: {prev[k]['value']}")
        print("    ⚠ ISM取得失敗。手入力で更新してください。")
    time.sleep(0.5)

    # Yahoo (yfinance)
    for key, sym in [("move", "^MOVE"), ("wti", "CL=F"),
                      ("copper", "HG=F"), ("dxy", "DX-Y.NYB")]:
        print(f"  Yahoo {sym}...")
        r = yahoo_z(sym)
        if r:
            ind[key] = r; print(f"    → {r['value']} (Z={r['z']})")
        elif key in prev:
            ind[key] = prev[key]; ind[key]["stale"] = True
            print(f"    → 前回値を保持")
        time.sleep(0.3)

    result = {"updated_at": now.isoformat(), "indicators": ind}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    fresh = sum(1 for v in ind.values() if not v.get("stale"))
    total = len(ind)
    print(f"\n完了: {fresh}/{total}指標を新規取得 ({total-fresh}件は前回値保持)")
    if fresh < 4:
        print("⚠ 新規取得数が少ない。APIキー/ネットワークを確認。", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
