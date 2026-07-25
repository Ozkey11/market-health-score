# Market Health Score — 変更点 (v10.6 / 2026-07-25)

**作業①: main.py に fetch_sentiment / fetch_macro を配線し、Actions のコミット対象を data/ 配下全体に広げる**

前バージョン v10.5(株価更新の不具合修正・読み込み高速化・バックテスト反映)からの差分です。
今回はバッチ層のみの変更で、`index.html` と `sw.js` は変更していません。

---

## 背景 — 何が起きていたか

データ取得状況の棚卸し(`data_source_audit_v1_0`)で判明した問題:

- GitHub Actions は毎営業日 `python batch/main.py` を実行していたが、
  **main.py が呼んでいたのは Step 4(ファンダ)だけ**だった。
- `fetch_prices.py` / `fetch_macro.py` / `fetch_sentiment.py` / `build_json.py` は
  **実装済みで存在するのに一度も実行されていなかった**。
- そのため SQLite は `sentiment_daily` が4行、`features_daily`・`scores_daily`・
  `supply_demand_daily` は0行のまま放置されていた。
- コミット対象も `data/fundamentals.json` の1ファイルのみで、
  他の配信JSON(`latest.json`・`data_quality.json` 等)は
  2026-07-07 の内容で止まっていた。

---

## A. main.py の全面書き換え

### A-1. 全5ステップを配線

| Step | 処理 | 呼び出し先 | 出力先 |
|---|---|---|---|
| 1 | 価格 | `fetch_prices.fetch_all_prices` | SQLite `prices_daily` |
| 2 | マクロ | `fetch_macro.fetch_all_macro` | SQLite `macro_series` |
| 3 | センチメント | `fetch_sentiment.fetch_all_sentiment` | SQLite `sentiment_daily` |
| 4 | ファンダ | `fetch_fundamentals.run_fetch` | `data/fundamentals.json` |
| 5 | 配信JSON生成 | `build_json.*` | `data/latest.json` ほか5件 |

Step 5 で生成される配信JSON:
`latest.json` / `history_1y.json` / `data_quality.json` / `sources.json` / `api_status.json`

### A-2. エラー隔離 — 1つ失敗しても止めない

各ステップを個別に try/except で囲み、失敗しても後続を続行します。
これにより「価格取得は失敗したがファンダは取れた」という部分的な成功でも、
配信JSONは必ず生成されます。

終了コードの方針:

- 配信JSONが作れて、かつ何か1つでも取得できていれば **0(成功扱い)**
- 配信JSONを作れなかった、または全ステップが失敗した場合のみ **1**

部分的な失敗で Actions を赤くすると、**失敗したという記録そのものが配信されなくなる**ため、
あえて成功扱いにしています(コミットは `if: always()` で必ず実行されます)。

### A-3. FRED_API_KEY 未設定時の扱いを改善

キーが無い場合、6シリーズすべてを個別にエラー記録するのではなく、
ステップ単位でスキップし、**APIキーの取得先URLを含む案内を表示**するようにしました。

```
[警告] FRED_API_KEY が未設定のためスキップします。
       GitHub の Settings -> Secrets and variables -> Actions に
       FRED_API_KEY を登録すると取得されるようになります。
       APIキーは https://fred.stlouisfed.org/docs/api/api_key.html で無料取得できます
```

### A-4. ステップの個別実行に対応(デバッグ用)

Windows のコマンドプロンプトから、必要なステップだけを実行できます。

```
py batch\main.py                  … 全ステップ
py batch\main.py prices           … 価格だけ
py batch\main.py macro sentiment  … 複数指定も可
```

指定できる名前: `prices` / `macro` / `sentiment` / `fundamentals` / `json`
不明な名前を指定した場合は、使い方を表示して終了コード1で終わります。

### A-5. 実行サマリーの表示

```
実行サマリー
============================================================
  [NG] prices       : 成功 0 / 失敗 8
  [--] macro        : スキップ（FRED_API_KEY未設定）
  [NG] sentiment    : 成功 0 / 失敗 4
  [OK] fundamentals : 成功 1 / 失敗 0
  [OK] json         : 配信JSON生成

  ステータス: partial
```

---

## B. db.py のバグ修正 — 失敗ログが消えていた

### 症状

作業中に発見しました。**13件の失敗のうち4件しか記録されていませんでした。**

### 原因

各 fetcher の例外処理が次の順序になっています。

```python
except Exception as e:
    conn.rollback()          # ← ここ
    log_quality(conn, ...)   # ← INSERT するがコミットしない
```

`log_quality()` はコミットしないため、**次の銘柄が失敗したときの `conn.rollback()` が、
直前のログ挿入ごと巻き戻していました**。結果、最後の1〜2件しか残りません。

### 修正

`db.py` の `log_quality()` に `conn.commit()` を追加しました。
ログは追記専用であり、呼び出し側はいずれも commit/rollback 直後に呼んでいるため、
未コミットのデータ書き込みを巻き込む心配はありません。

修正後は13件すべてが記録されることを確認しています。
この修正がないと `data/data_quality.json` が実態を反映せず、
**今回の配線作業の目的そのものが達成できません**でした。

---

## C. GitHub Actions ワークフローの更新

### C-1. コミット対象を `data/` 配下全体に拡大

```diff
- git add data/fundamentals.json || true
+ git add -A data/
```

`-A` を付けたことで、**新規に生成されたファイルも拾える**ようになりました。
旧実装ではファイル名を直接指定していたため、`api_status.json` のような
新規ファイルは永久にコミットされませんでした。

### C-2. `if: always()` を付与

```diff
  - name: Commit updated data
+   if: always()
```

バッチが非ゼロで終了しても、**取得に失敗したという記録は配信する**ためです。
これがないと、失敗時にアプリ側で原因が分からなくなります。

### C-3. 取得結果サマリーのステップを追加

失敗した項目とその取得先URLを Actions のログに出します。
どのAPIが壊れたのかを、Actionsの画面だけで判断できるようにするためです。

### C-4. git 操作の整理

旧実装の `git stash` → `git pull --rebase` → `git stash pop` は
競合時に壊れやすいため、**「add → commit → pull --rebase → push」**の順に変更しました。

### C-5. cron のコメントを実態に合わせて修正

`30 22 * * 1-5` は UTC 22:30 実行です。これは米国のその日の引け(20:00–21:00 UTC)と
日本のその日の引け(06:00 UTC)の**両方より後**であり、1回の実行で日米そろった
当日終値が取れます。この意図をコメントとして明記しました。

### C-6. SQLite をコミット対象に含めない理由

`database/market_data.sqlite` は 1.2MB のバイナリで、毎営業日コミットすると
リポジトリが年間300MB規模で肥大化します。SQLite は毎回3年分を取り直すため、
コミットしなくても配信JSONは正しく生成されます。

DBも履歴として残したい場合は、ワークフローの `git add -A data/` に
`database/` を追加してください(コメントに明記済み)。

---

## D. .gitignore の追記

WAL モードで動作するため、実行中に `-wal` / `-shm` の一時ファイルが生成されます。
これらをコミットしないよう除外設定を追加しました。

```
database/*.sqlite-wal
database/*.sqlite-shm
database/*.sqlite-journal
```

---

## 変更ファイル

| ファイル | 変更 |
|---|---|
| `batch/main.py` | 全面書き換え(40行 → 約230行)。全5ステップ配線・エラー隔離・個別実行対応 |
| `batch/db.py` | `log_quality()` に `conn.commit()` を追加(失敗ログの巻き戻し修正) |
| `.github/workflows/update-data.yml` | コミット対象拡大・`if: always()`・サマリー表示・git操作の整理 |
| `.gitignore` | SQLite一時ファイルの除外を追加 |
| `CHANGELOG_v10_6.md` | 本ファイル(新規) |

`index.html` と `sw.js` は**変更していません**(v10.5 のままです)。

---

## 動作確認

サンドボックスはネットワークが遮断されているため、
**「すべての外部APIが落ちた最悪ケース」**として検証しました。

| 検証項目 | 結果 |
|---|---|
| 全5ステップが実行されるか | 実行された(旧版は Step 4 のみ) |
| 1ステップの失敗で止まらないか | 止まらず後続を続行 |
| 全滅しても配信JSONが作られるか | 5件すべて生成された |
| 終了コード | 0(partial) — コミットが実行される |
| 失敗ログの記録 | 修正前 4/13件 → **修正後 13/13件** |
| ファンダの前回値保持 | 全指標が `stale=True` 付きで保持され、消失なし |
| `history_1y.json` の内容 | 7銘柄×400日、旧ファイルと完全一致(欠損なし) |
| 個別ステップ実行 | `main.py sentiment` で該当ステップのみ実行 |
| 不正なステップ名 | 使い方を表示して終了コード1 |

成功パスは yfinance をモックして検証しました。

| 検証項目 | 結果 |
|---|---|
| 8銘柄中7銘柄成功・1銘柄失敗のケース | 成功7/失敗1と正しく集計 |
| SQLiteへの格納 | 210行/7銘柄が格納された |
| ログの記録 | 8件(ok=7 / failed=1) |
| `latest.json` の生成 | 価格・日付が正しく反映された |

ワークフローの各シェルステップは、YAML から抽出して実際に実行し、
heredoc の展開・git 操作(変更なし/既存変更/新規ファイル)を確認済みです。

---

## 次のステップ

作業②以降は未着手です。

- **作業②** ローソク足・チャートパターンを Layer3 へ追加(外部API不要)
- **作業③** 米SVR + 日本信用残の取得バッチを追加
- **作業④** 日経空売り比率・AAII を追加

---

## 導入方法

置き換えるのは次の4ファイルです。`index.html` と `sw.js` は v10.5 のままで構いません。

1. `batch/main.py`
2. `batch/db.py`
3. `.github/workflows/update-data.yml`
4. `.gitignore`

置き換え後、GitHub の Actions タブから **Update Market Data** を
「Run workflow」で手動実行すると、その場で動作を確認できます。
FRED_API_KEY を未登録の場合は Step 2 がスキップされますが、他は動作します。
