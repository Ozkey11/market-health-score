# -*- coding: utf-8 -*-
r"""
import_supply_history.py — 需給データの過去分をまとめてSQLiteへ取り込む

日々の差分取得は fetch_supply_us / fetch_supply_jp が行いますが、
Zスコアやパーセンタイルを計算するには過去の履歴が必要です。
すでに手元にあるファイルを一度だけ流し込むための道具です。

使い方（Windows）:

  # FINRAの日次ファイル（CNMSshvol*.txt）が入ったフォルダを取り込む
  py batch\import_supply_history.py --finra path\to\finra_daily_short_volume

  # JPXの信用残PDFが入ったフォルダを取り込む
  py batch\import_supply_history.py --jpx path\to\jpx_pdf

  # AAIIセンチメントの全期間スプレッドシートを取り込む
  py batch\import_supply_history.py --aaii path\to\aaii_sentiment.xls

  # 銘柄を絞る（既定は data/watchlist.json。--all で全銘柄）
  py batch\import_supply_history.py --finra data\finra --all

FINRAの過去分そのものの入手には、別途お持ちの
`finra_daily_short_volume_downloader.py` をお使いください。
JPXの週末残高PDFは、公表ページに直近5週ぶんしか掲載されていないため、
過去分は手元に保存済みのものを使う必要があります。
"""
import argparse
import io
import os
import sys
import glob
import traceback
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import connect, init_db, upsert_supply_symbol, upsert_supply   # noqa: E402


def import_aaii(conn, path, verbose=True):
    """AAIIの全期間スプレッドシートを取り込む。戻り値 (取込週数, 失敗数)。

    AAIIの配布ファイルは以下の形:
      4行目がヘッダ (Date / Bullish / Neutral / Bearish / Total)
      6行目以降がデータ。値は小数 (0.36 = 36%)
    全期間データはAAII会員限定のため、手元のファイルを取り込む方式にしている。
    """
    try:
        import pandas as pd
    except ImportError:
        print("  pandas が必要です: py -m pip install pandas xlrd openpyxl")
        return 0, 1
    try:
        df = pd.read_excel(path, header=None)
    except Exception as e:
        print(f"  読み込みに失敗しました: {type(e).__name__}: {e}")
        print("  .xls なら xlrd、.xlsx なら openpyxl が必要です")
        return 0, 1

    # ヘッダ行(Date/Bullish/Bearish が並ぶ行)を探す
    hdr = None
    for i in range(min(20, len(df))):
        row = [str(x).strip().lower() for x in df.iloc[i].tolist()]
        if "date" in row and "bullish" in row and "bearish" in row:
            hdr = i
            cols = {name: row.index(name) for name in ("date", "bullish", "neutral", "bearish")
                    if name in row}
            break
    if hdr is None:
        print("  ヘッダ行(Date/Bullish/Bearish)が見つかりません")
        return 0, 1

    ok = ng = 0
    for i in range(hdr + 1, len(df)):
        r = df.iloc[i]
        try:
            d = pd.to_datetime(r[cols["date"]], errors="coerce")
            if pd.isna(d):
                continue
            bull = r[cols["bullish"]]
            bear = r[cols["bearish"]]
            if pd.isna(bull) or pd.isna(bear):
                continue
            bull, bear = float(bull), float(bear)
            # 小数(0.36)でも百分率(36.0)でも受け付ける
            if bull <= 1.5 and bear <= 1.5:
                bull, bear = bull * 100, bear * 100
            neut = r[cols["neutral"]] if "neutral" in cols else None
            if neut is not None and not pd.isna(neut):
                neut = float(neut)
                if neut <= 1.5:
                    neut *= 100
            else:
                neut = None
            ds = d.date().isoformat()
            upsert_supply(conn, "US", ds, "aaii_bull", round(bull, 2), "AAII Sentiment Survey")
            upsert_supply(conn, "US", ds, "aaii_bear", round(bear, 2), "AAII Sentiment Survey")
            upsert_supply(conn, "US", ds, "aaii_spread", round(bull - bear, 2), "AAII Sentiment Survey")
            if neut is not None:
                upsert_supply(conn, "US", ds, "aaii_neutral", round(neut, 2), "AAII Sentiment Survey")
            ok += 1
        except Exception:
            ng += 1
    conn.commit()
    if verbose and ok:
        rng = conn.execute(
            "SELECT MIN(date), MAX(date) FROM supply_demand_daily WHERE metric_name='aaii_bull'"
        ).fetchone()
        print(f"    {ok}週分を取り込みました (期間 {rng[0]} 〜 {rng[1]})")
    return ok, ng


def import_finra(conn, folder, want, verbose=True):
    """FINRAの日次ファイル群を取り込む。戻り値 (取込日数, 失敗数)。"""
    from fetch_supply_us import parse_finra, SOURCE
    files = sorted(glob.glob(os.path.join(folder, "CNMSshvol*.txt")))
    if not files:
        print(f"  対象ファイルが見つかりません: {folder}\\CNMSshvol*.txt")
        return 0, 0
    print(f"  {len(files)}ファイルを処理します")
    ok = ng = 0
    for i, f in enumerate(files, 1):
        try:
            text = io.open(f, encoding="utf-8-sig", errors="replace").read()
            d, data = parse_finra(text, want)
            rel = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
            for sym, a in data.items():
                if a["ratio"] is None:
                    continue
                upsert_supply_symbol(conn, sym, d, "short_volume_ratio",
                                     round(a["ratio"], 6), SOURCE, rel)
                upsert_supply_symbol(conn, sym, d, "short_volume", a["short"], SOURCE, rel)
                upsert_supply_symbol(conn, sym, d, "total_volume", a["total"], SOURCE, rel)
            ok += 1
            if i % 100 == 0 or i == len(files):
                conn.commit()
                if verbose:
                    print(f"    {i}/{len(files)} 完了 (最新 {d}, {len(data)}銘柄)")
        except Exception as e:
            ng += 1
            print(f"    [NG] {os.path.basename(f)}: {type(e).__name__}: {e}")
    conn.commit()
    return ok, ng


def import_jpx(conn, folder, verbose=True):
    """JPXの信用残PDF群を取り込む。戻り値 (取込ファイル数, 失敗数)。"""
    from fetch_supply_jp import pdf_to_text, parse_margin_pdf, expected_count, \
        to_yahoo_ticker, SOURCE
    files = sorted(glob.glob(os.path.join(folder, "*.pdf")))
    if not files:
        print(f"  対象ファイルが見つかりません: {folder}\\*.pdf")
        return 0, 0
    print(f"  {len(files)}ファイルを処理します")
    ok = ng = 0
    for f in files:
        try:
            with open(f, "rb") as fp:
                data = fp.read()
            text = pdf_to_text(data)
            fmt, as_of, rows = parse_margin_pdf(text)
            if not as_of:
                # ファイル名から日付を拾う (syumatsu2026070300.pdf -> 2026-07-03)
                base = os.path.basename(f)
                digits = "".join(ch for ch in base if ch.isdigit())
                if len(digits) >= 8:
                    as_of = f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
            if not as_of or not rows:
                raise ValueError(f"解析できません (申込日={as_of}, 行数={len(rows)})")
            exp = expected_count(text)
            rel = (date.fromisoformat(as_of) + timedelta(days=4)).isoformat()
            for r in rows:
                sym = to_yahoo_ticker(r["code"])
                upsert_supply_symbol(conn, sym, as_of, "margin_short", r["sell"], SOURCE, rel)
                upsert_supply_symbol(conn, sym, as_of, "margin_long", r["buy"], SOURCE, rel)
                upsert_supply_symbol(conn, sym, as_of, "margin_short_chg", r["sell_chg"], SOURCE, rel)
                upsert_supply_symbol(conn, sym, as_of, "margin_long_chg", r["buy_chg"], SOURCE, rel)
                if r["sell"] > 0:
                    upsert_supply_symbol(conn, sym, as_of, "margin_ratio",
                                         round(r["buy"] / r["sell"], 4), SOURCE, rel)
            conn.commit()
            ok += 1
            note = f"{len(rows)}銘柄" + (f" / PDF記載{exp}銘柄" if exp else "")
            if verbose:
                print(f"    {os.path.basename(f)}: {as_of} {note} (様式:{fmt})")
            if exp and len(rows) < exp * 0.9:
                print(f"      [警告] 解析漏れの可能性があります ({len(rows)}/{exp})")
        except Exception as e:
            conn.rollback()
            ng += 1
            print(f"    [NG] {os.path.basename(f)}: {type(e).__name__}: {e}")
            traceback.print_exc()
    return ok, ng


def main():
    ap = argparse.ArgumentParser(description="需給データの過去分をSQLiteへ取り込みます。")
    ap.add_argument("--finra", metavar="DIR", help="FINRA日次ファイル(CNMSshvol*.txt)のフォルダ")
    ap.add_argument("--jpx", metavar="DIR", help="JPX信用残PDFのフォルダ")
    ap.add_argument("--aaii", metavar="FILE", help="AAIIセンチメントのスプレッドシート(.xls/.xlsx)")
    ap.add_argument("--all", action="store_true",
                    help="ウォッチリストで絞らず全銘柄を取り込む（FINRAは容量が大きくなります）")
    args = ap.parse_args()

    if not args.finra and not args.jpx and not args.aaii:
        ap.print_help()
        return 1

    conn = connect()
    init_db(conn)

    if args.finra:
        want = None
        if not args.all:
            from fetch_supply_us import _watchlist
            want = set(_watchlist())
            print(f"[FINRA] 対象銘柄 {len(want)}件に絞って取り込みます（全銘柄なら --all）")
        else:
            print("[FINRA] 全銘柄を取り込みます")
        ok, ng = import_finra(conn, args.finra, want)
        print(f"[FINRA] 完了: 成功 {ok}日 / 失敗 {ng}日")

    if args.jpx:
        print("[JPX] PDFを取り込みます")
        ok, ng = import_jpx(conn, args.jpx)
        print(f"[JPX] 完了: 成功 {ok}件 / 失敗 {ng}件")

    if args.aaii:
        print("[AAII] スプレッドシートを取り込みます")
        ok, ng = import_aaii(conn, args.aaii)
        print(f"[AAII] 完了: 成功 {ok}週 / 失敗 {ng}週")

    n = conn.execute("SELECT COUNT(*) FROM supply_symbol_daily").fetchone()[0]
    syms = conn.execute("SELECT COUNT(DISTINCT symbol) FROM supply_symbol_daily").fetchone()[0]
    rng = conn.execute("SELECT MIN(date), MAX(date) FROM supply_symbol_daily").fetchone()
    print(f"\nsupply_symbol_daily: {n:,}行 / {syms:,}銘柄 / 期間 {rng[0]} 〜 {rng[1]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
