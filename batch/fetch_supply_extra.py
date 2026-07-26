# -*- coding: utf-8 -*-
r"""
fetch_supply_extra.py — 日経空売り比率 (JPX) と AAII センチメント

分析レポートでの評価:
  ・日経空売り比率 ≧45%  : 20日超過 +0.73 / 陽性率83% (v7.0 §8-1)
      日本で最も実用的な日次の需給フロー指標。ただし効果量は中程度
  ・AAII spread ≦-30     : 20日超過 +1.25 / tvt 0.52 (v7.0 §6-1)
      米国個人投資家の総悲観。日本株にも効くのが確認されている

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1) 日経空売り比率 (JPX 空売り集計・日次)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  https://www.jpx.co.jp/markets/statistics-equities/short-selling/index.html
  ・APIキー不要・無料・毎営業日
  ・1ページのみの小さなPDF ({YYMMDD}-m.pdf)
  ・掲載列: 実注文(a) / 空売り-価格規制あり(b) / 空売り-価格規制なし(c) / 合計(d)
  ・一般に「空売り比率」と呼ばれるのは (b+c)/d。JPXのFAQにも
    「新聞等の報道ではこの両者を合計して空売り比率と呼んでいる」とある

  注意: PDFのURLはファイルごとにディレクトリ名が変わる
        (例 t13vrt000001emfs-att)。日付から組み立てられないため、
        一覧ページのリンクを拾う方式にしている。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  2) AAII センチメント
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  https://www.aaii.com/sentimentsurvey
  ・毎週木曜に公表 (強気/中立/弱気の%)
  ・全期間のスプレッドシートは**AAII会員限定**のため自動取得できない
  ・そこで本モジュールは「公開ページから最新週の1件だけ」を取りにいく

  過去分は、すでにお持ちの `AAII投資家sentiment_全データ.xls` を
  import_supply_history.py の --aaii で取り込んでください。
  公開ページの体裁が変わると取得できなくなりますが、その場合も
  data_quality.json に記録され、手入力での補完が可能です。
"""
import io
import re
import traceback
from datetime import date, datetime, timedelta
from urllib.request import urlopen, Request

from db import upsert_supply, log_quality
from sources import link_for

JP_LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/short-selling/index.html"
# 2026-07-26 修正: 公開トップページだけでは読めないことがあるため複数試す
AAII_URLS = [
    "https://www.aaii.com/sentimentsurvey/sent_results",
    "https://www.aaii.com/sentimentsurvey",
    "https://en.macromicro.me/charts/20828/us-aaii-sentimentsurvey",
    "https://en.macromicro.me/charts/116484/us-aaii-investor-sentiment-survey",
]
AAII_URL = AAII_URLS[0]
UA = "market-health-score/1.0 (personal research)"
MAX_FILES = 10


def _fetch(url, timeout=60):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


# ══════════════════════════════════════════════════════════
#  日経空売り比率
# ══════════════════════════════════════════════════════════
# 例: 2026年5月29日 11,385,187 65.5% 5,175,095 29.8% 814,270 4.7% 17,374,552
SHORT_ROW = re.compile(
    r'(?P<y>\d{4})年\s*(?P<m>\d{1,2})月\s*(?P<d>\d{1,2})日\s+'
    r'(?P<real>[\d,]+)\s+(?P<realp>[\d.]+)\s*%\s+'
    r'(?P<reg>[\d,]+)\s+(?P<regp>[\d.]+)\s*%\s+'
    r'(?P<nreg>[\d,]+)\s+(?P<nregp>[\d.]+)\s*%\s+'
    r'(?P<total>[\d,]+)'
)


def parse_short_selling_pdf(text):
    """空売り集計PDFのテキストから (日付, 指標dict) を返す。見つからなければ None。"""
    m = SHORT_ROW.search(text)
    if not m:
        return None
    d = date(int(m.group("y")), int(m.group("m")), int(m.group("d"))).isoformat()
    regp = float(m.group("regp"))
    nregp = float(m.group("nregp"))
    n = lambda s: float(s.replace(",", ""))
    return d, {
        # 一般に報道される「空売り比率」= 価格規制あり + 価格規制なし
        "short_sale_ratio": round(regp + nregp, 2),
        "short_ratio_regulated": regp,
        "short_ratio_unregulated": nregp,
        "real_order_value": n(m.group("real")),
        "short_value": n(m.group("reg")) + n(m.group("nreg")),
        "total_value": n(m.group("total")),
    }


def list_short_selling_pdfs(html):
    """一覧ページのHTMLから (YYMMDD, URL) を新しい順に返す。"""
    out = []
    for m in re.finditer(r'href="([^"]*?/short-selling/[^"]*?(\d{6})-m\.pdf)"', html, re.I):
        href, ymd = m.group(1), m.group(2)
        if href.startswith("/"):
            href = "https://www.jpx.co.jp" + href
        out.append((ymd, href))
    out.sort(key=lambda x: x[0], reverse=True)
    seen, res = set(), []
    for ymd, u in out:
        if u in seen:
            continue
        seen.add(u)
        res.append((ymd, u))
    return res


def _have(conn, market, d, metric):
    r = conn.execute(
        "SELECT COUNT(*) FROM supply_demand_daily WHERE market=? AND date=? AND metric_name=?",
        (market, d, metric),
    ).fetchone()
    return bool(r and r[0])


def fetch_jp_short_ratio(conn, run_id, max_files=MAX_FILES):
    """JPXの空売り集計(日次)を取得する。戻り値 (成功数, 失敗数)。"""
    from fetch_supply_jp import pdf_to_text
    try:
        html = _fetch(JP_LIST_URL, timeout=30).decode("utf-8", errors="replace")
    except Exception as e:
        log_quality(conn, run_id, "short_ratio_jp:list", "failed",
                    f"{type(e).__name__}: {e}", JP_LIST_URL)
        return 0, 1

    pdfs = list_short_selling_pdfs(html)
    if not pdfs:
        log_quality(conn, run_id, "short_ratio_jp:list", "failed",
                    "一覧ページからPDFリンクを見つけられませんでした（様式変更の可能性）",
                    JP_LIST_URL)
        return 0, 1

    print(f"  PDFリンク {len(pdfs)}件を検出")
    ok = ng = 0
    for ymd, url in pdfs[:max_files]:
        guess = f"20{ymd[0:2]}-{ymd[2:4]}-{ymd[4:6]}"
        if _have(conn, "JP", guess, "short_sale_ratio"):
            continue
        try:
            text = pdf_to_text(_fetch(url), want_rows=False)
            r = parse_short_selling_pdf(text)
            if not r:
                raise ValueError("集計行を解析できませんでした")
            d, vals = r
            for k, v in vals.items():
                upsert_supply(conn, "JP", d, k, v, "JPX 空売り集計(日次)")
            conn.commit()
            log_quality(conn, run_id, f"short_ratio_jp:{d}", "ok",
                        f"空売り比率 {vals['short_sale_ratio']}%")
            print(f"    {d}: 空売り比率 {vals['short_sale_ratio']}%")
            ok += 1
        except Exception as e:
            conn.rollback()
            log_quality(conn, run_id, f"short_ratio_jp:{guess}", "failed",
                        f"{type(e).__name__}: {e}", url)
            ng += 1
    if ok == 0 and ng == 0:
        print("    最新分は取得済みです")
    return ok, ng


# ══════════════════════════════════════════════════════════
#  AAII センチメント
# ══════════════════════════════════════════════════════════
def parse_aaii_html(html):
    """AAIIの公開ページから最新週の強気/中立/弱気(%)を取り出す。

    体裁が変わりやすいので、複数の書き方を順に試す。
    """
    txt = re.sub(r"<[^>]+>", " ", html)
    txt = re.sub(r"&nbsp;?", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    # 「decreased 15.3 percentage points to 29.6%」のような文では、
    # 15.3 は"変化幅"であって水準ではない。先に取り除かないと誤って拾ってしまう。
    txt = re.sub(r"\d{1,2}(?:\.\d)?\s*percentage\s*points?", " ", txt, flags=re.I)

    def find(word):
        # 水準は「to 29.6%」「is 31.5%」の形で書かれることが多いので、そちらを優先する
        for pat in (word + r"[^%]{0,160}?\bto\s+(\d{1,2}\.\d)\s*%",
                    word + r"[^%]{0,160}?\bis\s+(\d{1,2}\.\d)\s*%",
                    word + r"[^%]{0,60}?(\d{1,2}\.\d)\s*%"):
            m = re.search(pat, txt, re.I)
            if m:
                return float(m.group(1))
        return None

    bull = find(r"bullish sentiment") or find(r"bullish")
    neut = find(r"neutral sentiment") or find(r"neutral")
    bear = find(r"bearish sentiment") or find(r"bearish")
    if bull is None or bear is None:
        return None
    total = bull + (neut or 0) + bear
    # 3つの合計が100%前後にならない場合は拾い間違いとみなす
    if neut is not None and not (95 <= total <= 105):
        return None
    return {"aaii_bull": bull, "aaii_bear": bear,
            "aaii_neutral": neut, "aaii_spread": round(bull - bear, 1)}


def _last_thursday():
    d = date.today()
    while d.weekday() != 3:     # 3 = 木曜
        d -= timedelta(days=1)
    return d.isoformat()


def fetch_aaii(conn, run_id):
    """AAIIの最新週を取得する。戻り値 (成功数, 失敗数)。

    2026-07-26 修正:
      公開トップページ1本だけを見ていたが、体裁変更で読み取れなくなった。
      公式の結果ページとMacroMicroのミラーを含め、順に試すようにする。
    """
    vals, used, errs = None, None, []
    for url in AAII_URLS:
        try:
            html = _fetch(url, timeout=30).decode("utf-8", errors="replace")
        except Exception as e:
            errs.append(f"{url}: {type(e).__name__}")
            continue
        v = parse_aaii_html(html)
        if v:
            vals, used = v, url
            break
        errs.append(f"{url}: 数値を読み取れず")
    if not vals:
        log_quality(conn, run_id, "aaii", "failed",
                    "いずれの取得先からも読み取れませんでした（" + " / ".join(errs[:3])
                    + "）。全期間データは会員限定のため import_supply_history.py --aaii で手動取り込み可",
                    AAII_URLS[0])
        return 0, 1
    print(f"    取得先: {used}")
    d = _last_thursday()
    if _have(conn, "US", d, "aaii_bull"):
        print("    最新週は取得済みです")
        return 0, 0
    for k, v in vals.items():
        if v is not None:
            upsert_supply(conn, "US", d, k, v, "AAII Sentiment Survey")
    conn.commit()
    log_quality(conn, run_id, "aaii", "ok",
                f"強気{vals['aaii_bull']}% / 弱気{vals['aaii_bear']}% / spread{vals['aaii_spread']}")
    print(f"    {d}: 強気{vals['aaii_bull']}% 弱気{vals['aaii_bear']}% spread{vals['aaii_spread']}")
    return 1, 0


def fetch_all_extra(conn, run_id):
    """空売り比率とAAIIをまとめて取得する。片方が失敗しても他方は続行する。"""
    ok = ng = 0
    try:
        print("  [日本] JPX 空売り集計（日次）")
        a, b = fetch_jp_short_ratio(conn, run_id)
        ok += a
        ng += b
    except Exception as e:
        print(f"    失敗: {type(e).__name__}: {e}")
        traceback.print_exc()
        ng += 1
    try:
        print("  [米国] AAII センチメント")
        a, b = fetch_aaii(conn, run_id)
        ok += a
        ng += b
    except Exception as e:
        print(f"    失敗: {type(e).__name__}: {e}")
        traceback.print_exc()
        ng += 1
    return ok, ng


if __name__ == "__main__":
    from db import connect, init_db
    c = connect()
    init_db(c)
    print(fetch_all_extra(c, "manual"))
