#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_fundamentals.py v4 — ファンダメンタル指標の自動取得バッチ
修正: multplのregex(1.5誤マッチ修正)、NAPM廃止対応、ISM手入力フォールバック

取得先:
  FRED API  : HYスプレッド(BAMLH0A0HYM2), イールドカーブ(T10Y2Y)
  multpl.com: シラーPER(41.60等), S&P500 PER(TTM)(32.15等) — "Current ... Ratio: XX.XX"
  Yahoo Fin : MOVE(^MOVE), WTI(CL=F), 銅(HG=F), DXY(DX-Y.NYB) — yfinanceで252日Z
  ISM       : FREDから廃止済。前回値保持+手入力で更新
"""
import os, json, sys, time, re
from datetime import datetime, timezone
from urllib.request import urlopen, Request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "fundamentals.json")
FRED_KEY = os.environ.get("FRED_API_KEY", "")

def _get(url, headers=None):
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
         "Accept": "text/html,application/json,*/*", "Accept-Language": "en-US,en;q=0.9"}
    if headers: h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8")

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

# ── multpl.com ──
def multpl(path):
    """'Current ... Ratio: 41.60' or 'Current ... is 41.60' パターンで取得"""
    url = f"https://www.multpl.com/{path}"
    try:
        html = _get(url, {"Referer": "https://www.multpl.com/"})
        # パターン1: "Current Shiller PE Ratio: 41.60" or "Current S&P 500 PE Ratio is 32.15"
        # 2桁以上の整数部を要求して、CSSのstroke-width等(1.5)を除外
        m = re.search(r'Current[^:]*?(?::|is)\s*([\d]{2,3}\.[\d]{1,2})', html)
        if m:
            val = float(m.group(1))
            print(f"    パターン1マッチ: {val}")
            return {"value": val, "source": f"multpl.com/{path}"}
        # パターン2: <td> 内の2桁以上の数値
        nums = re.findall(r'<td[^>]*>\s*([\d]{2,3}\.\d{1,2})\s*</td>', html)
        if nums:
            val = float(nums[0])
            print(f"    パターン2マッチ: {val}")
            return {"value": val, "source": f"multpl.com/{path}"}
        print(f"    ⚠ パターン不一致。HTML先頭500字: {html[:500]}")
    except Exception as e:
        print(f"  ✖ multpl {path}: {e}")
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
    print(f"[{now.isoformat()}] ファンダメンタル指標取得バッチ v4")
    prev = load_prev()
    ind = {}

    # FRED
    for key, sid in [("hy_spread", "BAMLH0A0HYM2"), ("yield_curve", "T10Y2Y")]:
        print(f"  FRED {sid}...")
        r = fred_latest(sid)
        if r:
            ind[key] = r; print(f"    → {r['value']} ({r.get('date','')})")
        elif key in prev:
            ind[key] = prev[key]; ind[key]["stale"] = True
            print(f"    → 前回値保持: {prev[key]['value']}")
        time.sleep(0.3)

    # multpl.com
    for key, path in [("shiller_pe", "shiller-pe"), ("pe_ttm", "s-p-500-pe-ratio")]:
        print(f"  multpl {path}...")
        r = multpl(path)
        if r:
            ind[key] = r; print(f"    → {r['value']}")
        elif key in prev:
            ind[key] = prev[key]; ind[key]["stale"] = True
            print(f"    → 前回値保持: {prev[key]['value']}")
        time.sleep(1.5)

    # ISM: FREDから廃止済み。前回値を保持し、手入力で更新する設計
    print("  ISM: FREDから廃止済み → 前回値保持 or 手入力")
    for k in ["ism", "ism_prev"]:
        if k in prev:
            ind[k] = prev[k]; ind[k]["stale"] = True
            print(f"    {k}: {prev[k]['value']} (前回値保持)")

    # Yahoo (yfinance)
    for key, sym in [("move", "^MOVE"), ("wti", "CL=F"),
                      ("copper", "HG=F"), ("dxy", "DX-Y.NYB")]:
        print(f"  Yahoo {sym}...")
        r = yahoo_z(sym)
        if r:
            ind[key] = r; print(f"    → {r['value']} (Z={r['z']})")
        elif key in prev:
            ind[key] = prev[key]; ind[key]["stale"] = True
            print(f"    → 前回値保持")
        time.sleep(0.3)

    result = {"updated_at": now.isoformat(), "indicators": ind}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    fresh = sum(1 for v in ind.values() if not v.get("stale"))
    total = len(ind)
    print(f"\n完了: {fresh}/{total}指標を新規取得 ({total-fresh}件は前回値保持)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
