#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_fundamentals.py — ファンダメンタル指標の自動取得バッチ
GitHub Actionsで毎日実行 → data/fundamentals.json に書き出す。
株価スコアアプリはこのJSONをGitHub Raw URLから読み、手入力不要でファンダ採点を行う。

取得先:
  FRED API  : HYスプレッド(BAMLH0A0HYM2), イールドカーブ(T10Y2Y), ISM(NAPM)
  multpl.com: シラーPER, S&P500 PER(TTM) — テーブルページをスクレイピング
  Yahoo Fin : MOVE(^MOVE), WTI(CL=F), 銅(HG=F), DXY(DX-Y.NYB) — yfinanceで取得しZスコア算出
"""
import os, json, sys, time, re, math
from datetime import datetime, timezone

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "fundamentals.json")
FRED_KEY = os.environ.get("FRED_API_KEY", "")

def _get(url, headers=None):
    from urllib.request import urlopen, Request
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
    if headers: h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8")

# ── FRED ──
def fred(series_id, n=5):
    if not FRED_KEY:
        print(f"  ⚠ FRED_API_KEY未設定 → {series_id}スキップ"); return None
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={series_id}&api_key={FRED_KEY}&file_type=json"
           f"&sort_order=desc&limit={n}")
    j = json.loads(_get(url))
    vals = [(o["date"], float(o["value"])) for o in j.get("observations",[]) if o["value"]!="."]
    return vals

def fred_latest(series_id):
    try:
        vals = fred(series_id)
        if vals: return {"value": vals[0][1], "date": vals[0][0], "source": f"FRED {series_id}"}
    except Exception as e:
        print(f"  ✖ FRED {series_id}: {e}")
    return None

# ── multpl.com ──
def multpl(path):
    try:
        url = f"https://www.multpl.com/{path}/table/by-month"
        html = _get(url, {"Accept": "text/html", "Accept-Language": "en-US,en;q=0.9",
                          "Referer": "https://www.multpl.com/"})
        m = re.search(r'<td[^>]*class="right"[^>]*>([\d.]+)', html)
        if m: return {"value": float(m.group(1)), "source": f"multpl.com/{path}"}
        # フォールバック: テーブル行パターン
        m = re.search(r'<tr[^>]*>\s*<td[^>]*>([^<]+)</td>\s*<td[^>]*>([\d.]+)', html)
        if m: return {"value": float(m.group(2)), "date": m.group(1).strip(), "source": f"multpl.com/{path}"}
    except Exception as e:
        print(f"  ✖ multpl {path}: {e}")
    return None

# ── Yahoo Finance (yfinance) ──
def yahoo_z(symbol, days=504):
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        df = tk.history(period="2y")
        if df.empty or len(df)<60: return None
        closes = df["Close"].dropna().values
        win = closes[-min(252,len(closes)):]
        mu, sd = win.mean(), win.std()
        z = (closes[-1]-mu)/sd if sd>0 else 0
        return {"value": round(float(closes[-1]),4), "z": round(float(z),3), "source": f"Yahoo {symbol}"}
    except Exception as e:
        print(f"  ✖ Yahoo {symbol}: {e}")
    return None

def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] ファンダメンタル指標取得バッチ開始")
    ind = {}

    # FRED: HYスプレッド, イールドカーブ, ISM(今回+前回)
    for key, sid in [("hy_spread","BAMLH0A0HYM2"),("yield_curve","T10Y2Y")]:
        print(f"  FRED {sid}...")
        r = fred_latest(sid)
        if r: ind[key]=r; print(f"    → {r['value']} ({r.get('date','')})")
        time.sleep(0.3)

    # ISM: 最新+前回
    print("  FRED NAPM (ISM)...")
    try:
        vals = fred("NAPM", 10)
        if vals and len(vals)>=1:
            ind["ism"] = {"value":vals[0][1], "date":vals[0][0], "source":"FRED NAPM"}
            print(f"    ISM最新: {vals[0][1]} ({vals[0][0]})")
        if vals and len(vals)>=2:
            ind["ism_prev"] = {"value":vals[1][1], "date":vals[1][0], "source":"FRED NAPM (前回)"}
            print(f"    ISM前回: {vals[1][1]} ({vals[1][0]})")
    except Exception as e:
        print(f"  ✖ ISM: {e}")
    time.sleep(0.3)

    # multpl.com
    for key, path in [("shiller_pe","shiller-pe"),("pe_ttm","s-p-500-pe-ratio")]:
        print(f"  multpl {path}...")
        r = multpl(path)
        if r: ind[key]=r; print(f"    → {r['value']}")
        time.sleep(1.5)

    # Yahoo (yfinance)
    for key, sym in [("move","^MOVE"),("wti","CL=F"),("copper","HG=F"),("dxy","DX-Y.NYB")]:
        print(f"  Yahoo {sym}...")
        r = yahoo_z(sym)
        if r: ind[key]=r; print(f"    → {r['value']} (Z={r['z']})")
        time.sleep(0.3)

    result = {"updated_at": now.isoformat(), "indicators": ind}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    n = len(ind)
    print(f"\n完了: {n}/9指標を {OUT} に書き出し")
    if n < 5:
        print("⚠ 取得数が少ない", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
