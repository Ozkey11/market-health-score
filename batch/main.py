"""
main.py — バッチエントリポイント
GitHub Actions（.github/workflows/update-data.yml）から毎営業日実行される。
フロー: API取得 → SQLite更新 → JSON生成 → (Actionsがcommit/push)

設計方針（設計書8.2準拠）:
・API失敗でも全体を止めない。失敗はdata_quality_logへ記録し、
  UIが「失敗通知＋取得先リンク＋手動入力」を案内する。
・スコア計算アルゴリズムは本体HTML側(Claude側)で管理するため、
  ここでは compute_scores_placeholder() のみ用意し確定計算はしない。
"""
import sys
import uuid
from datetime import datetime, timezone

import db
from fetch_prices import fetch_all_prices
from fetch_macro import fetch_all_macro
from fetch_sentiment import fetch_all_sentiment
from fetch_fundamentals import fetch_all_fundamentals
import build_json


def compute_scores_placeholder(conn, run_id):
    """
    スコア計算プレースホルダ。
    レイヤー構成・スコア算出式はHTMLアプリ側（CONFIG/エンジン）が正本のため、
    バッチ側では計算しない。将来サーバー計算へ移行する場合は
    ここにHTML側エンジンの移植を行い、scores_dailyへ保存する。
    """
    db.log_quality(conn, run_id, "scores:placeholder", "ok",
                   "スコアはHTMLアプリ側で計算（設計方針）")


def main():
    run_id = uuid.uuid4().hex[:12]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"=== Market Health Batch run_id={run_id} ===")

    conn = db.connect()
    db.init_db(conn)

    counters = {}
    ok, ng = fetch_all_prices(conn, run_id)
    counters["prices"] = {"ok": ok, "failed": ng}
    print(f"prices: ok={ok} failed={ng}")

    ok, ng = fetch_all_macro(conn, run_id)
    counters["macro"] = {"ok": ok, "failed": ng}
    print(f"macro: ok={ok} failed={ng}")

    ok, ng = fetch_all_sentiment(conn, run_id)
    counters["sentiment"] = {"ok": ok, "failed": ng}
    print(f"sentiment: ok={ok} failed={ng}")

    ok, ng = fetch_all_fundamentals(conn, run_id)
    counters["fundamentals"] = {"ok": ok, "failed": ng}
    print(f"fundamentals: ok={ok} failed={ng}")

    compute_scores_placeholder(conn, run_id)

    # JSON生成
    print("building JSON...")
    build_json.build_latest(conn, run_id)
    build_json.build_history(conn)
    build_json.build_data_quality(conn, run_id)
    build_json.build_sources_json()

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_failed = sum(c["failed"] for c in counters.values())
    status = "ok" if total_failed == 0 else "partial"
    db.log_run(conn, run_id, started, finished, status, str(counters))
    conn.commit()

    build_json.build_api_status(run_id, started, finished, counters)
    conn.close()

    print(f"=== done status={status} ===")
    # 部分失敗でもexit 0（設計書5.4: 失敗しても全体を止めない）
    return 0


if __name__ == "__main__":
    sys.exit(main())
