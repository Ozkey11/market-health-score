"""
fetch_prices.py — 価格データ取得（Yahoo Finance / yfinance）
・取得失敗しても例外で全体を止めず、data_quality_logに記録して続行する。
・SQLiteには全履歴（最低500営業日）、JSONには300-500営業日を配信（build_json側で制御）。
"""
import traceback
from db import upsert_price, log_quality
from sources import link_for

# 取得対象。volume_proxy は出来高代用シンボル（指数の出来高が信頼できない場合）
TARGETS = [
    {"symbol": "^GSPC", "name": "S&P 500",      "market": "US", "volume_proxy": "SPY"},
    {"symbol": "SPY",   "name": "SPDR S&P500",  "market": "US", "volume_proxy": None},
    {"symbol": "QQQ",   "name": "Invesco QQQ",  "market": "US", "volume_proxy": None},
    {"symbol": "IWM",   "name": "iShares R2000","market": "US", "volume_proxy": None},
    {"symbol": "SOXL",  "name": "SOXL",         "market": "US", "volume_proxy": None},
    {"symbol": "^N225", "name": "日経225",       "market": "JP", "volume_proxy": "1570.T"},
    {"symbol": "1306.T","name": "TOPIX ETF",    "market": "JP", "volume_proxy": None},
    {"symbol": "^VIX",  "name": "VIX",          "market": "US", "volume_proxy": None},
]

HISTORY_PERIOD = "3y"  # SQLiteへは3年分（500営業日以上を確保）


def fetch_all_prices(conn, run_id):
    """全対象銘柄の日足を取得してSQLiteへupsert。戻り値: (成功数, 失敗数)"""
    try:
        import yfinance as yf
    except ImportError:
        log_quality(conn, run_id, "prices:*", "failed",
                    "yfinance未インストール。pip install yfinance",
                    link_for("prices:^GSPC"))
        return 0, len(TARGETS)

    ok, ng = 0, 0
    for t in TARGETS:
        sym = t["symbol"]
        item = f"prices:{sym}"
        try:
            df = yf.Ticker(sym).history(period=HISTORY_PERIOD, interval="1d", auto_adjust=False)
            if df is None or df.empty:
                raise ValueError("空のレスポンス")
            rows = 0
            for idx, row in df.iterrows():
                date = idx.strftime("%Y-%m-%d")
                upsert_price(
                    conn, sym, date,
                    float(row.get("Open") or 0) or None,
                    float(row.get("High") or 0) or None,
                    float(row.get("Low") or 0) or None,
                    float(row.get("Close") or 0) or None,
                    float(row.get("Adj Close") or row.get("Close") or 0) or None,
                    float(row.get("Volume") or 0) or None,
                    "yfinance",
                )
                rows += 1
            conn.commit()
            log_quality(conn, run_id, item, "ok", f"{rows}行取得")
            ok += 1
        except Exception as e:
            conn.rollback()
            log_quality(conn, run_id, item, "failed",
                        f"{type(e).__name__}: {e}", link_for(item))
            ng += 1
            traceback.print_exc()
    return ok, ng
