"""
fetch_fundamentals.py — ファンダメンタルズ取得
・FMP(Financial Modeling Prep)を第一候補、Alpha Vantageを補助とする。
・シラーPERなど安定APIがないものは手動入力運用を前提とし、
  ここでは取得失敗としてdata_qualityに記録→UIから手動入力してもらう。
・最新値はfundamental_latest、変更履歴はfundamental_historyへ。
"""
import os
import json
import urllib.request
from datetime import date
from db import upsert_fundamental_latest, log_quality
from sources import link_for

FMP_KEY = os.environ.get("FMP_API_KEY", "")
AV_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")


def fetch_sp500_per_fmp(conn, run_id):
    """FMPでS&P500のPER相当を取得（無料枠に注意）。"""
    item = "fundamental:sp500_per"
    if not FMP_KEY:
        log_quality(conn, run_id, item, "failed",
                    "FMP_API_KEY未設定（手動入力またはSecrets登録）",
                    link_for("fundamental:shiller_per"))
        return False
    try:
        # SPYのratios-ttmで代用（指数全体PERの無料安定APIは少ない）
        url = f"https://financialmodelingprep.com/api/v3/ratios-ttm/SPY?apikey={FMP_KEY}"
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read().decode())
        if not data:
            raise ValueError("空レスポンス")
        per = data[0].get("peRatioTTM")
        if per is None:
            raise ValueError("peRatioTTMなし")
        upsert_fundamental_latest(conn, "^GSPC", "per_ttm", float(per),
                                  date.today().isoformat(), "FMP")
        conn.commit()
        log_quality(conn, run_id, item, "ok", f"PER(TTM)={per:.2f}")
        return True
    except Exception as e:
        conn.rollback()
        log_quality(conn, run_id, item, "failed",
                    f"{type(e).__name__}: {e}", link_for("fundamental:shiller_per"))
        return False


def note_manual_items(conn, run_id):
    """安定APIが無く手動入力運用とする項目を毎回data_qualityに記録し、UIへ案内を出す。"""
    manual_items = [
        ("fundamental:shiller_per", "シラーPERは安定APIなし。リンク先で確認し手動入力してください。"),
    ]
    for item, msg in manual_items:
        log_quality(conn, run_id, item, "manual", msg, link_for(item))


def fetch_all_fundamentals(conn, run_id):
    ok = 1 if fetch_sp500_per_fmp(conn, run_id) else 0
    note_manual_items(conn, run_id)
    return ok, 1 - ok
