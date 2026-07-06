"""
db.py — SQLite正本DB管理モジュール
設計書 3章「SQLite設計」準拠。
raw data / features / scores / config を分離し、
将来のアルゴリズム変更に耐える構造とする。
"""
import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "market_data.sqlite")

DDL = """
CREATE TABLE IF NOT EXISTS symbols (
    symbol      TEXT PRIMARY KEY,
    name        TEXT,
    market      TEXT,           -- 'US' / 'JP'
    asset_type  TEXT,           -- 'index' / 'etf' / 'stock'
    volume_proxy TEXT,          -- 出来高代用シンボル (例: ^GSPC -> SPY)
    active      INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS prices_daily (
    symbol          TEXT NOT NULL,
    date            TEXT NOT NULL,
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    adjusted_close  REAL,
    volume          REAL,
    source          TEXT,
    updated_at      TEXT,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS macro_series (
    series_id    TEXT NOT NULL,
    date         TEXT NOT NULL,   -- 対象日
    value        REAL,
    source       TEXT,
    release_date TEXT,            -- 発表日（対象日と分ける）
    updated_at   TEXT,
    PRIMARY KEY (series_id, date)
);

CREATE TABLE IF NOT EXISTS fundamental_latest (
    symbol      TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value       REAL,
    as_of_date  TEXT,
    source      TEXT,
    is_manual   INTEGER DEFAULT 0,   -- 手動入力フラグ
    updated_at  TEXT,
    PRIMARY KEY (symbol, metric_name)
);

CREATE TABLE IF NOT EXISTS fundamental_history (
    symbol      TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    date        TEXT NOT NULL,
    value       REAL,
    source      TEXT,
    updated_at  TEXT,
    PRIMARY KEY (symbol, metric_name, date)
);

CREATE TABLE IF NOT EXISTS sentiment_daily (
    market      TEXT NOT NULL,     -- 'US' / 'JP'
    date        TEXT NOT NULL,
    metric_name TEXT NOT NULL,     -- 'vix' / 'put_call' / 'fear_greed' / 'nk_vi' 等
    value       REAL,
    source      TEXT,
    is_manual   INTEGER DEFAULT 0,
    updated_at  TEXT,
    PRIMARY KEY (market, date, metric_name)
);

CREATE TABLE IF NOT EXISTS supply_demand_daily (
    market      TEXT NOT NULL,
    date        TEXT NOT NULL,
    metric_name TEXT NOT NULL,     -- 'short_sale_ratio' / 'up_down_ratio' / 'margin_long' 等
    value       REAL,
    source      TEXT,
    updated_at  TEXT,
    PRIMARY KEY (market, date, metric_name)
);

CREATE TABLE IF NOT EXISTS features_daily (
    symbol       TEXT NOT NULL,
    date         TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    value        REAL,
    updated_at   TEXT,
    PRIMARY KEY (symbol, date, feature_name)
);

CREATE TABLE IF NOT EXISTS scores_daily (
    symbol            TEXT NOT NULL,
    date              TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    regime_score      REAL,
    bottom_score      REAL,
    top_score         REAL,
    confirmation_score REAL,
    sentiment_score   REAL,
    overall_score     REAL,
    confidence        REAL,
    raw_json          TEXT,
    updated_at        TEXT,
    PRIMARY KEY (symbol, date, model_version)
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT,
    finished_at TEXT,
    status      TEXT,            -- 'ok' / 'partial' / 'failed'
    detail      TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT,
    item         TEXT,           -- 'prices:^GSPC' / 'sentiment:vix' 等
    status       TEXT,           -- 'ok' / 'failed' / 'stale' / 'manual'
    message      TEXT,
    source_link  TEXT,           -- 取得先リンク（失敗時にUIへ表示）
    occurred_at  TEXT
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn):
    conn.executescript(DDL)
    conn.commit()


def upsert_price(conn, symbol, date, o, h, l, c, adj, vol, source):
    conn.execute(
        """INSERT INTO prices_daily(symbol,date,open,high,low,close,adjusted_close,volume,source,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(symbol,date) DO UPDATE SET
             open=excluded.open, high=excluded.high, low=excluded.low,
             close=excluded.close, adjusted_close=excluded.adjusted_close,
             volume=excluded.volume, source=excluded.source, updated_at=excluded.updated_at""",
        (symbol, date, o, h, l, c, adj, vol, source, now_iso()),
    )


def upsert_macro(conn, series_id, date, value, source, release_date=None):
    conn.execute(
        """INSERT INTO macro_series(series_id,date,value,source,release_date,updated_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(series_id,date) DO UPDATE SET
             value=excluded.value, source=excluded.source,
             release_date=excluded.release_date, updated_at=excluded.updated_at""",
        (series_id, date, value, source, release_date, now_iso()),
    )


def upsert_sentiment(conn, market, date, metric, value, source, is_manual=0):
    conn.execute(
        """INSERT INTO sentiment_daily(market,date,metric_name,value,source,is_manual,updated_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(market,date,metric_name) DO UPDATE SET
             value=excluded.value, source=excluded.source,
             is_manual=excluded.is_manual, updated_at=excluded.updated_at""",
        (market, date, metric, value, source, is_manual, now_iso()),
    )


def upsert_fundamental_latest(conn, symbol, metric, value, as_of_date, source, is_manual=0):
    conn.execute(
        """INSERT INTO fundamental_latest(symbol,metric_name,value,as_of_date,source,is_manual,updated_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(symbol,metric_name) DO UPDATE SET
             value=excluded.value, as_of_date=excluded.as_of_date,
             source=excluded.source, is_manual=excluded.is_manual, updated_at=excluded.updated_at""",
        (symbol, metric, value, as_of_date, source, is_manual, now_iso()),
    )
    # 履歴側にも残す
    conn.execute(
        """INSERT OR REPLACE INTO fundamental_history(symbol,metric_name,date,value,source,updated_at)
           VALUES(?,?,?,?,?,?)""",
        (symbol, metric, as_of_date, value, source, now_iso()),
    )


def log_quality(conn, run_id, item, status, message="", source_link=""):
    conn.execute(
        """INSERT INTO data_quality_log(run_id,item,status,message,source_link,occurred_at)
           VALUES(?,?,?,?,?,?)""",
        (run_id, item, status, message, source_link, now_iso()),
    )


def log_run(conn, run_id, started_at, finished_at, status, detail=""):
    conn.execute(
        """INSERT OR REPLACE INTO run_log(run_id,started_at,finished_at,status,detail)
           VALUES(?,?,?,?,?)""",
        (run_id, started_at, finished_at, status, detail),
    )
