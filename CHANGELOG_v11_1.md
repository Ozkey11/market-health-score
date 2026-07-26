# Market Health Score — 変更点 (v11.1 / 2026-07-26)

**バッチ実行ログとスクリーンショットから判明した不具合の修正**

v11.0 からの差分です。実際に GitHub Actions を回していただいたログのおかげで、
サンドボックスでは再現できない問題が5件見つかりました。

---

## A. GitHub Actions が最後に失敗していた

### 症状

```
[main fd2c787] Update market data [skip ci]
 7 files changed, 3463 insertions(+), 705 deletions(-)
error: cannot pull with rebase: You have unstaged changes.
error: Please commit or stash them.
Error: Process completed with exit code 128.
```

**コミットは成功していたのに push できず、更新が反映されていませんでした。**

### 原因

バッチは `database/market_data.sqlite` を書き換えますが、これはコミット対象外です
（1.2MBのバイナリを毎日コミットするとリポジトリが肥大化するため、v10.6 でそう決めました）。

その結果、`git add -A data/` の後も **SQLite の変更が未ステージのまま残り**、
`git pull --rebase` が「未ステージの変更があるとリベースできない」と拒否していました。

### 修正

コミット後・rebase 前に、コミット対象外の変更を元に戻すようにしました。

```bash
git commit -m "Update market data [skip ci]"
git checkout -- . || true          # ← 追加
git clean -fd database || true      # ← 追加
git pull --rebase origin main
git push origin main
```

---

## B. JPX の信用残 PDF が「1銘柄も解析できませんでした」

### 原因は解析ロジックではなく、テキスト抽出でした

実際の PDF を取り寄せて確認したところ、**解析ロジック自体は正常に動作していました**。
実データのテキストで検証したところ 7行中7行を正しく抽出できています。

問題は `pdf_to_text()` にありました。

```python
try:
    import fitz          # PyMuPDF
    ...
except ImportError:
    pass
from pypdf import PdfReader   # ← ここへ落ちる
```

PyMuPDF は 1.24 以降でモジュール名が `pymupdf` になり、`fitz` は別名です。
バージョンによっては `import fitz` が通らず、**pypdf にフォールバック**します。
そして pypdf はこの PDF で**語と語の間の空白を落とす**ことがあり、
`13010 JP3257200000` が `13010JP3257200000` のようになって正規表現が外れていました。

### 修正

1. **抽出方式を3つ試し、「実際に何銘柄取れたか」で最良を選ぶ**ようにしました
   （pymupdf → pdftotext → pypdf。1,000銘柄以上取れた時点で打ち切り）
2. `import pymupdf` を先に試し、駄目なら `fitz` を試すようにしました
3. 正規表現の区切りを緩め、**空白が落ちても拾える**ようにしました
4. 解析0件のときは**抽出テキストの先頭400字をログに出す**ようにしました

あわせて、行の先頭が「…1479 銘柄B 極洋　普通株式…」のように
セクション見出しと連結しているケースも拾えるようにしています
（実データで1行取りこぼしていました）。

ログには次のように出るようになります。

```
      抽出 pymupdf: 1,234,567文字 / 銘柄4238件
      → pymupdf を採用
```

---

## C. Put/Call Ratio が取得できているのに表示されない

### 原因

`fetchFundJsonFromGitHub()` が **URL を手で設定しないと何も読まない**実装でした。

```python
if(!url){ console.log('fundamentals.json: URL未設定'); return null; }
```

バッチは `data/fundamentals.json` を正しく作っていましたが、
アプリはその場所を知らないため読みにいかず、
PCR が `null` → 「取得不可(米国のみ)」と表示されていました。
手入力欄だけが有効に見えていたのはこのためです。

**PCR だけでなく、シラーPER・イールドカーブ・ISM など
fundamentals.json 由来の指標すべてが同じ状態でした。**

### 修正

アプリと `data/` は同じ場所に置かれるので、**既定で `./data/fundamentals.json`
を見にいく**ようにしました。設定欄に URL が入っていればそちらを優先します。

---

## D. 需給データが画面のどこにも出ない

### 原因

需給の表示は**シグナルが立ったときだけ**出る作りでした。
Z スコアが ±2 を超えたときにしか表示されないため、
データが取れていても画面には何も出ませんでした。

しかも取得開始から日が浅く（12営業日分）、Z スコアが ±2 に達することは
まずありません。「動いていないのでは」と見えるのは当然でした。

### 修正

**シグナルの有無にかかわらず、需給の現況を常に表示**するようにしました。

```
需給データ（バッチ取得・65銘柄収録）
  空売り出来高比率  米国・日次(FINRA)          43.3%  Z+0.82
  AAII 強気-弱気    米国個人・週次              -12.5
  Zスコアは直近60件からの偏差です。件数が少ないうちは参考値です（12件）。
  日付は対象日で、公表はこれより数日遅れます。
```

- 銘柄固有のデータが無い場合は「※この銘柄の個別データは未収録（市場全体の指標のみ）」と明示
- バッチ未実行の場合は「未取得です。batch/main.py supply を実行すると表示されます」と表示
- 鮮度が落ちたデータには「鮮度低」バッジ

---

## E. FINRA の対象が13銘柄しかない

### 原因

`data/watchlist.json` だけを見ていたため、そこに無い銘柄は永久に対象外でした。

### 修正

**`data/supply_symbols.json` を新設**し、watchlist とあわせて取得対象にしました。
このファイルに追記すれば自由に増やせます。既定で **65銘柄**を収録しています
（主要ETF・大型株・半導体・レバレッジETF・ミーム株など）。

```json
{
  "symbols": ["SPY", "QQQ", "AAPL", "NVDA", "SOXL", "..."]
}
```

**全7,000銘柄を保存しない理由**: 1日7,000行 × 250営業日 = 175万行となり、
配信JSONが実用的な大きさを超えるためです。

なお**日本株は指定不要**です。JPX の PDF には全銘柄（約4,250）が載っており、
解析できれば全銘柄が保存されます。B の修正で解析できるようになります。

---

## F. AAII が読み取れない

ご指摘の代替URLを含め、**4つの取得先を順に試す**ようにしました。

1. `https://www.aaii.com/sentimentsurvey/sent_results`（ご提案・優先）
2. `https://www.aaii.com/sentimentsurvey`
3. `https://en.macromicro.me/charts/20828/us-aaii-sentimentsurvey`（ご提案）
4. `https://en.macromicro.me/charts/116484/us-aaii-investor-sentiment-survey`（ご提案）

すべて失敗した場合は、どのURLで何が起きたかを `data_quality.json` に記録します。
全期間データは会員限定のため、`import_supply_history.py --aaii` での手動取り込みも引き続き使えます。

---

## G. 変更ファイル

| ファイル | 変更 |
|---|---|
| `.github/workflows/update-data.yml` | rebase 前に未ステージ変更を戻す |
| `batch/fetch_supply_jp.py` | PDF抽出を3方式の best-of に / 正規表現を緩和 / 失敗時に中身を出力 |
| `batch/fetch_supply_extra.py` | AAII の取得先を4つに |
| `batch/fetch_supply_us.py` | supply_symbols.json を読むように |
| `data/supply_symbols.json` | 新規（65銘柄） |
| `index.html` | fundamentals.json の既定URL / 需給の現況表示 |
| `CHANGELOG_v11_1.md` | 本ファイル（新規） |

---

## H. 動作確認

| 検証項目 | 結果 |
|---|---|
| 実PDFテキストでの解析 | **7行中7行**（修正前は6行、抽出失敗時は0行） |
| 空白が落ちたケース | 7行とも抽出可能 |
| fundamentals.json の既定URL | URL未設定でも `./data/fundamentals.json` を取得、PCR 0.79 を読める |
| 需給の現況表示 | シグナル無しでも生値とZスコアを返す |
| 未収録銘柄 | 市場全体の指標のみ返し、`symbolFound=false` を立てる |
| 取得対象銘柄 | 13件 → **65件** |
| 既存テストの回帰 | v10.5系28 / パターン22 / v11.0系34 / 需給25 / 作業④22 すべて通過 |

---

## I. 導入方法

置き換えるのは次の5つです。

1. `index.html`
2. `batch/fetch_supply_jp.py`
3. `batch/fetch_supply_extra.py`
4. `batch/fetch_supply_us.py`
5. `.github/workflows/update-data.yml`

あわせて `data/supply_symbols.json` を追加してください（新規ファイル）。

置き換え後、Actions から手動実行すると次が確認できるはずです。

- JPX の信用残が「4,2xx銘柄」と表示される
- 最後の push が成功する
- アプリの市場心理パネルに Put/Call Ratio が出る
- エントリー準備度の下に需給データの現況が出る

---

## J. 残っている既知の問題

- **Fear & Greed（HTTP 418）**: CNN の非公式APIで、ボット判定でブロックされています。
  代替の取得先を探すか、手入力での運用になります。
- **CBOE の Put/Call Ratio**: CSV の形式変更で失敗していますが、
  fundamentals.json 側（MacroMicro 経由）で取得できているため、C の修正で表示されます。
- **JPXは2026年9月28日に信用残の様式を変更**します（v10.8 参照）。
  日次化されるのは改善ですが、切替後は解析結果の確認が必要です。
