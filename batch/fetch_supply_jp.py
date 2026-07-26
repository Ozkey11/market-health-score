# -*- coding: utf-8 -*-
"""
fetch_supply_jp.py — 日本の信用取引残高 (JPX)

分析レポート v2.1 / v7.0 の結論:
  ・信用買残Zは200日線から-10%以下の局面で+1.46pt。底判定に効く
  ・ただし日本株はスコアが低いほど買残Zが単調に高くなる(total≦20で+1.42σ)。
    需給を独立レイヤーとして重く配分すると同じ情報の二重計上になる
  ・買残混雑スコアは-0.05・陽性率48%で無情報。ゲートには使わないこと

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  重要: 2026年9月28日にJPXの公表様式が変わります
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  JPXは信用取引残高集計システムをリプレースし、2026/9/28(月)から
  公表資料が変更されます(2026/7/6付 JPX公表)。

    現行「銘柄別信用取引週末残高」 週次(火16:30) PDF
      ↓
    新 「銘柄別信用取引残高」     日次(毎営業日16:00) PDF

  変更点:
    ・週次 → 日次になる(アプリにとっては大きな改善)
    ・ファイル形式はPDFのまま(Excelの提供はない)
    ・各銘柄が2行になる(1行目=株数、2行目=金額)
    ・上場株式数比の列が追加される
    ・並び順が「銘柄種別・市場区分ごと」から「銘柄コード順」に変わる

  そのため本モジュールは様式を自動判定して解析器を切り替える構造にしています。
  9/28以降は FORMAT_2026 側の解析器が使われますが、実物で未検証のため、
  切替後は必ず data/data_quality.json の結果を確認してください。
  なお9/27のシステム移行が失敗した場合は延期されます。

データ元:
  https://www.jpx.co.jp/markets/statistics-equities/margin/05.html
  ・APIキー不要・無料
  ・PDFのみ(27MB前後・約170ページ)

補足: JPX公式のJ-Quants APIでも信用取引週末残高を取得できますが、
      スタンダードプラン(月額3,300円)以上が必要で、無料プランでは提供されません。
"""
import io
import os
import re
import json
import traceback
from datetime import date, datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

from db import upsert_supply_symbol, log_quality
from sources import link_for

LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"
LIST_URL_NEW = "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html"
SOURCE = "JPX 銘柄別信用取引残高"
UA = "market-health-score/1.0 (personal research)"
MAX_WEEKS = 3               # 一度に取りにいく最大本数


# ══════════════════════════════════════════════════════════
#  PDF → テキスト
# ══════════════════════════════════════════════════════════
def _extract_pymupdf(data):
    """PyMuPDF。1.24以降はモジュール名が pymupdf で、fitz は別名。
    バージョンによっては fitz が使えないため両方試す。"""
    mod = None
    try:
        import pymupdf as mod
    except ImportError:
        import fitz as mod
    doc = mod.open(stream=data, filetype="pdf")
    try:
        return "\n".join(p.get_text() for p in doc)
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _extract_pypdf(data):
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


def _extract_pdftotext(data):
    """poppler の pdftotext。-layout で列の並びを保つ。"""
    import subprocess, tempfile, os as _os
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", path, "-"],
                           capture_output=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200])
        return r.stdout.decode("utf-8", "replace")
    finally:
        try:
            _os.unlink(path)
        except Exception:
            pass


EXTRACTORS = [("pymupdf", _extract_pymupdf),
              ("pdftotext", _extract_pdftotext),
              ("pypdf", _extract_pypdf)]


def pdf_to_text(data, want_rows=True):
    """PDFのバイト列からテキストを取り出す。

    2026-07-26 修正:
      当初は pymupdf を試して失敗したら pypdf、という一本道だった。
      しかし pypdf は本PDFで語と語の間の空白を落とすことがあり、
      解析結果が0件になってしまう事故が起きた（GitHub Actions実測）。
      そこで複数の方式を試し、「実際に何銘柄取れたか」で最良のものを選ぶ。

    速度実測(85ページ): pymupdf 0.23秒 / pdftotext 0.25秒 / pypdf 3.3秒
    """
    best_txt, best_n, best_name, errors = "", -1, None, []
    for name, fn in EXTRACTORS:
        try:
            txt = fn(data)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
            continue
        if not txt:
            errors.append(f"{name}: 空のテキスト")
            continue
        n = len(set(m.group("code") for m in ROW_CURRENT.finditer(txt))) if want_rows else len(txt)
        n2 = len(set(m.group("code") for m in ROW_2026.finditer(txt))) if want_rows else 0
        n = max(n, n2)
        print(f"      抽出 {name}: {len(txt):,}文字 / 銘柄{n}件")
        if n > best_n:
            best_txt, best_n, best_name = txt, n, name
        if want_rows and n >= 1000:
            break            # 十分取れていれば他は試さない
    if best_name is None:
        raise RuntimeError("PDFからテキストを取り出せませんでした: " + " / ".join(errors))
    print(f"      → {best_name} を採用")
    return best_txt


# ══════════════════════════════════════════════════════════
#  解析器
# ══════════════════════════════════════════════════════════
# 現行様式(〜2026/9/25 申込分)
#   例: B タマホーム　普通株式 14190 JP3470900006 1,224,600 ▲ 8,900 234,000 ▲ 3,800 523,900 ...
#   列: 売残高 前週比 買残高 前週比 (以降は内訳)
#   ※ 社名とコードが空白なしで繋がる行、外国籍ETF(US/SG/JE始まりのISIN)もある
# 区切りの空白は抽出方式によって失われることがあるため [ \u3000]* と緩めにする。
# 単位コードの直前は行頭・空白・数字のほか「銘柄」等の文字が来ることもある
# (例: 「プライム Prime 1479 銘柄B 極洋　普通株式 13010 ...」)。
ROW_CURRENT = re.compile(
    r'(?:^|[\s\d]|銘柄)(?P<unit>[ABCJKMTF])[ \u3000]+'
    r'(?P<name>.{2,60}?)[ \u3000]*'
    r'(?P<code>[0-9][0-9A-Z]{4})[ \u3000]*'
    r'(?P<isin>[A-Z]{2}[0-9A-Z]{10})[ \u3000]*'
    r'(?P<sell>[\d,]+)[ \u3000]+(?P<sc>▲[ \u3000]*)?(?P<scv>[\d,]+)[ \u3000]+'
    r'(?P<buy>[\d,]+)[ \u3000]+(?P<bc>▲[ \u3000]*)?(?P<bcv>[\d,]+)'
)

# 新様式(2026/9/28〜)
#   例: B A社　普通株式 プライム 貸 11110 JP1111111111 株数 Shs. 9,300 900 0.1% 177,900 12,500 1.5% ...
#   列: 売残高 前日比 上場比 買残高 前日比 上場比 (以降は内訳)
#   金額行(金額 Val.)は読み飛ばす
ROW_2026 = re.compile(
    r'(?:^|[\s\d])(?P<unit>[ABCJKMTF])[ \u3000]+'
    r'(?P<name>.{2,60}?)[ \u3000]*'
    r'(?P<code>[0-9][0-9A-Z]{4})[ \u3000]+'
    r'(?P<isin>[A-Z]{2}[0-9A-Z]{10})[ \u3000]+'
    r'株数[ \u3000]*(?:Shs\.)?[ \u3000]+'
    r'(?P<sell>[\d,]+)[ \u3000]+(?P<sc>▲[ \u3000]*)?(?P<scv>[\d,]+)[ \u3000]+'
    r'(?P<sratio>[\d.]+%|\*)[ \u3000]+'
    r'(?P<buy>[\d,]+)[ \u3000]+(?P<bc>▲[ \u3000]*)?(?P<bcv>[\d,]+)'
)


def _num(s):
    return int(s.replace(",", "")) if s else 0


def detect_format(text):
    """PDFの様式を判定する。'2026' か 'current'。"""
    head = text[:4000]
    if "銘柄別信用取引残高" in head and "前日比" in head:
        return "2026"
    if "株数 Shs." in head or "株数Shs." in head:
        return "2026"
    return "current"


def parse_margin_pdf(text):
    """PDFテキストから銘柄別の信用残を取り出す。

    戻り値: (様式, 申込日 or None, [{code,name,sell,buy,sell_chg,buy_chg}, ...])
    """
    fmt = detect_format(text)
    rx = ROW_2026 if fmt == "2026" else ROW_CURRENT

    # 申込日: 「2026/7/3 申込み現在」「申込み現在 2026/10/5」の両方に対応
    as_of = None
    m = re.search(r'(\d{4})/(\d{1,2})/(\d{1,2})\s*申込み?現在', text[:3000]) \
        or re.search(r'申込み?現在\s*(\d{4})/(\d{1,2})/(\d{1,2})', text[:3000])
    if m:
        try:
            as_of = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass

    rows, seen = [], set()
    for m in rx.finditer(text):
        code = m.group("code")
        if code in seen:
            continue
        seen.add(code)
        rows.append(dict(
            code=code,
            name=m.group("name").strip(),
            sell=_num(m.group("sell")),
            buy=_num(m.group("buy")),
            sell_chg=(-_num(m.group("scv")) if m.group("sc") else _num(m.group("scv"))),
            buy_chg=(-_num(m.group("bcv")) if m.group("bc") else _num(m.group("bcv"))),
        ))
    return fmt, as_of, rows


def expected_count(text):
    """PDFに書かれている「総合計 ○○ 銘柄」を読み、解析漏れの検知に使う。"""
    m = re.search(r'総合計[^\d]{0,20}(\d{3,5})\s*銘柄', text)
    return int(m.group(1)) if m else None


def to_yahoo_ticker(code5):
    """JPXの5桁コード(4桁+チェックデジット)をYahoo形式へ。16050 → 1605.T"""
    return code5[:4] + ".T"


# ══════════════════════════════════════════════════════════
#  取得
# ══════════════════════════════════════════════════════════
def _fetch(url, timeout=120):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def list_pdf_urls():
    """一覧ページから信用残PDFのURLを新しい順に返す。

    ディレクトリ名(tvdivq...)は将来変わりうるため、日付を組み立てるのではなく
    ページ内のリンクを拾う方式にしている。
    """
    urls = []
    for page in (LIST_URL, LIST_URL_NEW):
        try:
            html = _fetch(page, timeout=30).decode("utf-8", errors="replace")
        except Exception as e:
            print(f"    一覧ページの取得に失敗 {page}: {e}")
            continue
        # 現行: syumatsu2026071000.pdf / 新様式でも同様のパターンを想定して広めに拾う
        for m in re.finditer(r'href="([^"]*?(?:syumatsu|meigara|margin)[^"]*?(\d{8})\d{0,2}\.pdf)"',
                             html, re.I):
            href, ymd = m.group(1), m.group(2)
            if href.startswith("/"):
                href = "https://www.jpx.co.jp" + href
            urls.append((ymd, href))
        if urls:
            break
    urls.sort(key=lambda x: x[0], reverse=True)
    seen, out = set(), []
    for ymd, u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append((ymd, u))
    return out


def _already_have(conn, d):
    row = conn.execute(
        "SELECT COUNT(*) FROM supply_symbol_daily WHERE date=? AND metric_name='margin_long'",
        (d,),
    ).fetchone()
    return bool(row and row[0])


def fetch_all_supply_jp(conn, run_id, max_files=MAX_WEEKS):
    """JPXの信用残PDFを取得して解析し、SQLiteへ格納する。戻り値 (成功数, 失敗数)。"""
    try:
        urls = list_pdf_urls()
    except Exception as e:
        log_quality(conn, run_id, "supply_jp:list", "failed",
                    f"{type(e).__name__}: {e}", LIST_URL)
        return 0, 1
    if not urls:
        log_quality(conn, run_id, "supply_jp:list", "failed",
                    "一覧ページからPDFリンクを見つけられませんでした（様式変更の可能性）",
                    LIST_URL)
        return 0, 1

    print(f"  PDFリンク {len(urls)}件を検出（新しい順に最大{max_files}件を処理）")
    ok = ng = 0
    for ymd, url in urls[:max_files]:
        iso_guess = f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
        if _already_have(conn, iso_guess):
            print(f"    {iso_guess}: 取得済み")
            continue
        try:
            print(f"    {iso_guess}: ダウンロード中 ...")
            data = _fetch(url)
            print(f"      {len(data)/1e6:.1f}MB → テキスト抽出中 ...")
            text = pdf_to_text(data)
            fmt, as_of, rows = parse_margin_pdf(text)
            as_of = as_of or iso_guess
            exp = expected_count(text)
            if not rows:
                # 何が取れていたのか分からないと直しようがないので先頭を出す
                head = " ".join(text[:400].split())
                print(f"      抽出テキストの先頭: {head}")
                raise ValueError("1銘柄も解析できませんでした（様式変更の可能性）"
                                 f" 先頭: {head[:150]}")
            if exp and len(rows) < exp * 0.9:
                # 9割を下回るなら解析漏れとみなし、警告として残す
                log_quality(conn, run_id, f"supply_jp:{as_of}", "stale",
                            f"解析 {len(rows)}件 / PDF記載 {exp}件（解析漏れの可能性）", url)

            # 公表は申込日の翌営業日以降。現行様式は火曜公表なので +2営業日を目安にする
            rel = (date.fromisoformat(as_of) + timedelta(days=4)).isoformat()
            n = 0
            for r in rows:
                sym = to_yahoo_ticker(r["code"])
                upsert_supply_symbol(conn, sym, as_of, "margin_short", r["sell"], SOURCE, rel)
                upsert_supply_symbol(conn, sym, as_of, "margin_long", r["buy"], SOURCE, rel)
                upsert_supply_symbol(conn, sym, as_of, "margin_short_chg", r["sell_chg"], SOURCE, rel)
                upsert_supply_symbol(conn, sym, as_of, "margin_long_chg", r["buy_chg"], SOURCE, rel)
                if r["sell"] > 0:
                    upsert_supply_symbol(conn, sym, as_of, "margin_ratio",
                                         round(r["buy"] / r["sell"], 4), SOURCE, rel)
                n += 1
            conn.commit()
            msg = f"{n}銘柄 (様式:{fmt}" + (f" / PDF記載{exp}銘柄" if exp else "") + ")"
            log_quality(conn, run_id, f"supply_jp:{as_of}", "ok", msg)
            print(f"      {msg}")
            ok += 1
        except HTTPError as e:
            log_quality(conn, run_id, f"supply_jp:{iso_guess}", "failed", f"HTTP {e.code}", url)
            ng += 1
        except Exception as e:
            conn.rollback()
            log_quality(conn, run_id, f"supply_jp:{iso_guess}", "failed",
                        f"{type(e).__name__}: {e}", url)
            ng += 1
            traceback.print_exc()

    if ok == 0 and ng == 0:
        print("    最新分は取得済みです")
    return ok, ng


if __name__ == "__main__":
    from db import connect, init_db
    c = connect()
    init_db(c)
    print(fetch_all_supply_jp(c, "manual"))
