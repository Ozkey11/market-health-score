#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bulk_score.py — 一括スコア計算バッチ
お気に入り銘柄リスト(data/watchlist.json)の全銘柄をyfinanceで取得し、
スコアを計算して data/scores.json に保存。
"""
import os, sys, json, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score_engine import compute_score

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "scores.json")
WATCHLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "watchlist.json")

def load_watchlist():
    try:
        with open(WATCHLIST, encoding="utf-8") as f:
            return json.load(f)
    except:
        return ["SPY","QQQ","AAPL","MSFT","NVDA","TSLA","AMZN","GOOGL","META","^GSPC","^N225"]

def load_fund():
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "fundamentals.json")
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
        ind = j.get("indicators", {})
        return {
            "hy_spread": ind.get("hy_spread", {}).get("value"),
            "shiller_pe": ind.get("shiller_pe", {}).get("value"),
            "pe_ttm": ind.get("pe_ttm", {}).get("value"),
            "yield_curve": ind.get("yield_curve", {}).get("value"),
            "move": ind.get("move", {}).get("value"),
            "copper_z": ind.get("copper", {}).get("z"),
            "wti_z": ind.get("wti", {}).get("z"),
            "dxy_z": ind.get("dxy", {}).get("z"),
        }
    except:
        return {}

def main():
    import yfinance as yf
    now = datetime.now(timezone.utc)
    symbols = load_watchlist()
    fund = load_fund()
    print(f"[{now.isoformat()}] 一括スコア計算: {len(symbols)}銘柄")

    # yfinanceで一括取得
    data = yf.download(symbols, period="2y", group_by="ticker", progress=False, threads=True)

    results = []
    for sym in symbols:
        try:
            if len(symbols) == 1:
                df = data
            else:
                df = data[sym] if sym in data.columns.get_level_values(0) else None
            if df is None or df.empty:
                print(f"  ✖ {sym}: データなし"); continue

            df = df.dropna(subset=["Close"])
            closes = df["Close"].values.tolist()
            highs = df["High"].values.tolist()
            lows = df["Low"].values.tolist()
            volumes = df["Volume"].values.tolist()

            if len(closes) < 60:
                print(f"  ✖ {sym}: データ不足({len(closes)}日)"); continue

            # VIX取得(^VIXが一括取得に含まれていれば)
            vix = None
            try:
                vix_df = data["^VIX"] if "^VIX" in data.columns.get_level_values(0) else None
                if vix_df is not None:
                    vix = float(vix_df["Close"].dropna().iloc[-1])
            except: pass

            score = compute_score(closes, highs, lows, volumes, vix=vix, fund=fund)
            price = closes[-1]
            prev = closes[-2] if len(closes) > 1 else price
            change = round((price / prev - 1) * 100, 2)

            results.append({
                "symbol": sym,
                "price": round(price, 2),
                "change": change,
                "score": score,
                "days": len(closes),
            })
            print(f"  ✓ {sym}: {price:.2f} ({change:+.2f}%) score={score}")
        except Exception as e:
            print(f"  ✖ {sym}: {e}")

    output = {"updated_at": now.isoformat(), "scores": results}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n完了: {len(results)}/{len(symbols)}銘柄 → {OUT}")

if __name__ == "__main__":
    main()
