# Market Health Score — データ基盤・GitHub運用

株価テクニカルスコア判定アプリ（`index.html`）と、そのデータ基盤
（Pythonバッチ → SQLite → JSON → GitHub Pages）の一式です。
設計は `MarketHealth_DataInfra_GitHub_Design.pdf` に準拠しています。

## 構成

```
market-health-score/
├── index.html              # アプリ本体（直接API取得 + Pages JSON読込の両対応）
├── data/                   # 配信用JSON（バッチが生成、Pagesが配信）
│   ├── latest.json         # 最新値・データ品質サマリ
│   ├── history_1y.json     # 価格・スコア履歴（400営業日）
│   ├── data_quality.json   # 取得失敗・欠損の詳細ログ
│   ├── sources.json        # 指標ごとの取得先リンク集
│   ├── config.json         # 設定
│   └── api_status.json     # バッチ実行状況
├── batch/                  # Pythonバッチ
│   ├── main.py             # エントリポイント
│   ├── db.py               # SQLite DDL・upsert
│   ├── fetch_prices.py     # 価格（yfinance）
│   ├── fetch_macro.py      # マクロ（FRED）
│   ├── fetch_sentiment.py  # VIX / Put-Call / Fear&Greed
│   ├── fetch_fundamentals.py # PER等（FMP）
│   ├── build_json.py       # SQLite→JSON変換
│   ├── sources.py          # 取得先リンク集（正本）
│   └── seed_from_csv.py    # 手元CSVからの初期シード（Phase 1用）
├── database/               # SQLite正本DB（バッチが生成）
└── .github/workflows/update-data.yml  # 毎営業日 JST7:00 自動実行
```

## セットアップ手順（設計書ロードマップ準拠）

### Phase 0-1: GitHub Pages公開・JSON確認
1. このフォルダをGitHubリポジトリにpush
2. Settings → Pages → Branch: main / root で公開
3. スマホ/PCで `https://<user>.github.io/<repo>/` を開く
4. 画面上部に「📦 データ基盤connected」が出ればJSON読込成功

### Phase 2: ローカルバッチ実行
```bash
pip install -r requirements.txt
python batch/main.py        # API取得→SQLite→JSON生成
# 初回で過去データを一括投入したい場合:
python batch/seed_from_csv.py
```

### Phase 3: GitHub Actions自動化
1. リポジトリ Settings → Secrets and variables → Actions に登録:
   - `FRED_API_KEY`（https://fred.stlouisfed.org/docs/api/api_key.html で無料取得）
   - `FMP_API_KEY`（任意）
   - `ALPHA_VANTAGE_API_KEY`（任意）
2. Actions タブ → Update Market Data → Run workflow で手動テスト
3. 成功すれば毎営業日 JST 7:00 に自動更新

### Phase 4: データ品質・監視
- 取得失敗は `data/data_quality.json` に記録され、アプリ起動時に
  ⚠バナーで通知＋**取得先リンク**を表示
- 失敗した指標はバナーの「✏️手動入力」から値を補完可能
  （localStorageに保存、3日間有効、スコア計算の代替値として使用）

## アプリのデータソース
| タブ | 説明 |
|---|---|
| Yahoo Finance | ブラウザから直接取得（キー不要・CORSプロキシ経由） |
| Alpha Vantage / Finnhub | 無料APIキーで直接取得 |
| 📦 JSON(Pages) | バッチ生成の `data/history_1y.json` を読込。**最も安定** |

感情指標（VIX等）の取得失敗時は ①Pages JSON → ②手動入力 の順で自動補完します。
