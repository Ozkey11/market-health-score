"""
seed_from_csv.py — プロジェクト実データCSVからSQLiteをシードする補助スクリプト
（Phase 1: JSON手動作成の代替。ローカル/初回セットアップ用）
"""
import csv, sys
sys.path.insert(0, '.')
import db

FILES = [
    ("/mnt/project/spy_daily_indicators.csv", "SPY"),
    ("/mnt/project/qqq_daily_indicators.csv", "QQQ"),
    ("/mnt/project/iwm_daily_indicators.csv", "IWM"),
    ("/mnt/project/soxl_daily_indicators.csv", "SOXL"),
    ("/mnt/project/topix1306_daily_indicators.csv", "1306.T"),
]

conn = db.connect()
db.init_db(conn)
run_id = "seed-local"

for path, sym in FILES:
    try:
        n = 0
        with open(path, encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                d = row.get('date')
                c = row.get('close')
                if not d or not c:
                    continue
                db.upsert_price(conn, sym, d,
                    float(row.get('open') or 0) or None,
                    float(row.get('high') or 0) or None,
                    float(row.get('low') or 0) or None,
                    float(c) or None,
                    float(c) or None,
                    float(row.get('volume') or 0) or None,
                    "seed_csv")
                n += 1
        conn.commit()
        db.log_quality(conn, run_id, f"prices:{sym}", "ok", f"CSV seed {n}行")
        print(f"{sym}: {n} rows")
    except Exception as e:
        print(f"{sym}: FAILED {e}")

# VIX + SP500 (^GSPC close only)
with open("/mnt/project/vix指数_sp500_過去データ.csv", encoding='utf-8-sig') as f:
    n = 0
    last_vix = None
    for row in csv.DictReader(f):
        d, vix, sp = row.get('Date'), row.get('VIX'), row.get('SP500')
        if d and sp:
            db.upsert_price(conn, "^GSPC", d, None, None, None, float(sp), float(sp), None, "seed_csv")
        if d and vix:
            db.upsert_sentiment(conn, "US", d, "vix", float(vix), "seed_csv")
            last_vix = (d, vix)
        n += 1
    conn.commit()
    print(f"^GSPC+VIX: {n} rows (last VIX {last_vix})")
db.log_quality(conn, run_id, "prices:^GSPC", "ok", "CSV seed")
db.log_quality(conn, run_id, "sentiment:vix", "ok", "CSV seed")
# 取得できない項目は失敗として記録（UI通知フローの実演）
from sources import link_for
db.log_quality(conn, run_id, "sentiment:fear_greed", "failed", "バッチ未実行のため未取得（サンプル）", link_for("sentiment:fear_greed"))
db.log_quality(conn, run_id, "sentiment:put_call", "failed", "バッチ未実行のため未取得（サンプル）", link_for("sentiment:put_call"))
db.log_quality(conn, run_id, "fundamental:shiller_per", "manual", "シラーPERは手動入力運用", link_for("fundamental:shiller_per"))
conn.commit()

import build_json
build_json.build_latest(conn, run_id)
build_json.build_history(conn)
build_json.build_data_quality(conn, run_id)
build_json.build_sources_json()
build_json.build_api_status(run_id, "seed", "seed", {"note": "CSV seed run"})
conn.close()
print("done")
