# -*- coding: utf-8 -*-
"""
fetch_supply_us.py — 米国の空売り出来高 (FINRA Daily Short Sale Volume)

分析レポート v2.1 / v6.0 の結論:
  空売り出来高比率(SVR)は、上昇・レンジ・下落のどのレジームでも安定してプラスに効いた
  唯一の需給指標(+0.39 / +0.33 / +0.44)。しかもスコアとほぼ独立した情報を持つ。
  一方、2週間ごとに公表されるショートインタレスト(残高)は単独では-2.34と買いに使えない。
  → 本モジュールが取得するのは「日次のフロー」であって「残高」ではない点に注意。

データ元:
  https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
  ・APIキー不要・無料
  ・毎営業日、翌営業日に公表
  ・パイプ区切り: Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
  ・休場日は404が返る
  ・同一銘柄が市場ごとに複数行に分かれることがあるため、銘柄単位で合算する

過去分の一括取得は、別途お持ちの
`finra_daily_short_volume_downloader.py` を使用してください。
本モジュールは「アプリを日々動かすための差分取得」に特化しています。
"""
import io
import os
import json
import traceback
from datetime import date, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

from db import upsert_supply_symbol, log_quality
from sources import link_for

BASE_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt"
SOURCE = "FINRA RegSHO Daily Short Sale Volume"
LOOKBACK_DAYS = 12          # 何営業日ぶんさかのぼって取りにいくか
UA = "market-health-score/1.0 (personal research)"

# ウォッチリストに無くても常に取っておく指数ETF
ALWAYS = ["SPY", "QQQ", "IWM", "DIA"]


def _watchlist():
    """取得対象の米国銘柄を決める。

    2026-07-26 拡張:
      当初は data/watchlist.json だけを見ていたため13銘柄しか取れていなかった。
      お気に入りに入れた銘柄が対象外だと需給が永久に表示されないため、
      data/supply_symbols.json を追加で読むようにした。
      このファイルに追記すれば、バッチの取得対象を自由に増やせる。

      すべての銘柄(7,000超)を保存しない理由:
        1日あたり7,000行 × 250営業日 = 175万行となり、
        配信JSONが実用的な大きさを超えるため。
    """
    base = os.path.join(os.path.dirname(__file__), "..", "data")
    syms, seen = [], set()

    def add(v):
        v = str(v).strip().upper()
        # 指数(^GSPC等)と日本株(.T)はFINRAの対象外
        if not v or v.startswith("^") or v.endswith(".T") or v in seen:
            return
        seen.add(v)
        syms.append(v)

    for fname in ("watchlist.json", "supply_symbols.json"):
        path = os.path.join(base, fname)
        try:
            with io.open(path, encoding="utf-8") as f:
                data = json.load(f)
            # 配列でも {"symbols":[...]} でも受け付ける
            items = data.get("symbols", []) if isinstance(data, dict) else data
            for v in items:
                add(v)
        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"    {fname} の読み込みに失敗: {e}")

    for s in ALWAYS:
        add(s)
    return syms


def _fetch_text(url, timeout=30):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8-sig", errors="replace")


def parse_finra(text, want):
    """FINRAのパイプ区切りテキストを解析し {symbol: {...}} を返す。

    同じ銘柄が市場(Q/N/B等)ごとに複数行に分かれる場合があるため合算する。
    want が None の場合は全銘柄を返す。
    """
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        raise ValueError("データ行がありません")
    header = lines[0].split("|")
    try:
        iDate = header.index("Date")
        iSym = header.index("Symbol")
        iShort = header.index("ShortVolume")
        iTotal = header.index("TotalVolume")
    except ValueError:
        raise ValueError("必要な列が見つかりません: " + lines[0])
    iExempt = header.index("ShortExemptVolume") if "ShortExemptVolume" in header else None

    out, trade_date = {}, None
    for line in lines[1:]:
        p = line.split("|")
        if len(p) <= max(iSym, iShort, iTotal):
            continue
        sym = p[iSym].strip()
        # FINRAの優先株シンボルは小文字を含む（例: AHTpF = Ashford Hospitality 優先株F）。
        # 大文字化すると AHTPF となり別銘柄と区別できなくなるため、原文のまま保持し、
        # ウォッチリストとの突合だけ大文字で行う。
        if want is not None and sym.upper() not in want:
            continue
        try:
            sv = float(p[iShort] or 0)
            tv = float(p[iTotal] or 0)
            ex = float(p[iExempt] or 0) if iExempt is not None else 0.0
        except ValueError:
            continue
        trade_date = trade_date or p[iDate].strip()
        a = out.setdefault(sym, {"short": 0.0, "exempt": 0.0, "total": 0.0})
        a["short"] += sv
        a["exempt"] += ex
        a["total"] += tv

    if not trade_date or len(trade_date) != 8:
        raise ValueError(f"日付が読み取れません: {trade_date!r}")
    d = f"{trade_date[0:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    for sym, a in out.items():
        a["ratio"] = (a["short"] / a["total"]) if a["total"] > 0 else None
        a["ratio_incl_exempt"] = ((a["short"] + a["exempt"]) / a["total"]) if a["total"] > 0 else None
        a["date"] = d
    return d, out


def _already_have(conn, d):
    row = conn.execute(
        "SELECT COUNT(*) FROM supply_symbol_daily WHERE date=? AND metric_name='short_volume_ratio'",
        (d,),
    ).fetchone()
    return bool(row and row[0])


def fetch_all_supply_us(conn, run_id, lookback=LOOKBACK_DAYS):
    """直近 lookback 営業日ぶんの未取得分を取りにいく。戻り値 (成功日数, 失敗日数)。"""
    want = set(_watchlist())
    print(f"  対象銘柄: {len(want)}件 ({', '.join(sorted(want)[:10])}{'...' if len(want) > 10 else ''})")
    print("  ※ 増やす場合は data/supply_symbols.json に銘柄コードを追記してください")

    days, cur = [], date.today()
    while len(days) < lookback:
        if cur.weekday() < 5:          # 土日を除く(祝日は404で弾かれる)
            days.append(cur)
        cur -= timedelta(days=1)
    days.reverse()

    ok = ng = 0
    for d in days:
        iso = d.isoformat()
        if _already_have(conn, iso):
            continue
        url = BASE_URL.format(d=d.strftime("%Y%m%d"))
        try:
            text = _fetch_text(url)
        except HTTPError as e:
            if e.code == 404:
                continue               # 休場日。エラーではない
            log_quality(conn, run_id, f"supply_us:{iso}", "failed",
                        f"HTTP {e.code}", link_for("supply:us_short_volume"))
            ng += 1
            continue
        except (URLError, OSError) as e:
            log_quality(conn, run_id, f"supply_us:{iso}", "failed",
                        f"{type(e).__name__}: {e}", link_for("supply:us_short_volume"))
            ng += 1
            continue

        try:
            trade_date, data = parse_finra(text, want)
            if trade_date != iso:
                # ファイル名の日付と中身の日付がずれている場合は中身を優先する
                print(f"    注意: ファイル名 {iso} に対し中身は {trade_date}")
            n = 0
            for sym, a in data.items():
                if a["ratio"] is None:
                    continue
                # 公表は翌営業日なので release_date は対象日の翌日とする
                rel = (date.fromisoformat(a["date"]) + timedelta(days=1)).isoformat()
                upsert_supply_symbol(conn, sym, a["date"], "short_volume_ratio",
                                     round(a["ratio"], 6), SOURCE, rel)
                upsert_supply_symbol(conn, sym, a["date"], "short_volume",
                                     a["short"], SOURCE, rel)
                upsert_supply_symbol(conn, sym, a["date"], "total_volume",
                                     a["total"], SOURCE, rel)
                n += 1
            conn.commit()
            log_quality(conn, run_id, f"supply_us:{trade_date}", "ok", f"{n}銘柄")
            print(f"    {trade_date}: {n}銘柄")
            ok += 1
        except Exception as e:
            conn.rollback()
            log_quality(conn, run_id, f"supply_us:{iso}", "failed",
                        f"{type(e).__name__}: {e}", link_for("supply:us_short_volume"))
            ng += 1
            traceback.print_exc()

    if ok == 0 and ng == 0:
        print("    最新分は取得済みです")
    return ok, ng


if __name__ == "__main__":
    from db import connect, init_db
    c = connect()
    init_db(c)
    print(fetch_all_supply_us(c, "manual"))
