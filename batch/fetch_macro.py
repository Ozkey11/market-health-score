"""
fetch_macro.py — マクロ指標取得（FRED中心）
・FRED_API_KEY が未設定なら全項目skip（data_qualityに記録）。
・取得日(release相当)と対象日(date)を分けて保存する。
"""
import os
import json
import urllib.request
from db import upsert_macro, log_quality
from sources import link_for

FRED_KEY = os.environ.get("FRED_API_KEY", "")

# FREDシリーズ定義（設計書 6.1 マクロ・金利・信用リスク）
FRED_SERIES = [
    {"id": "DGS10",        "label": "米10年債利回り"},
    {"id": "DGS2",         "label": "米2年債利回り"},
    {"id": "BAMLH0A0HYM2", "label": "米HY債スプレッド"},
    {"id": "FEDFUNDS",     "label": "FF金利"},
    {"id": "CPIAUCSL",     "label": "CPI"},
    {"id": "UNRATE",       "label": "失業率"},
]

FRED_URL = ("https://api.stlouisfed.org/fred/series/observations"
            "?series_id={sid}&api_key={key}&file_type=json"
            "&observation_start={start}")


def fetch_all_macro(conn, run_id, start="2023-01-01"):
    if not FRED_KEY:
        for s in FRED_SERIES:
            log_quality(conn, run_id, f"macro:{s['id']}", "failed",
                        "FRED_API_KEY未設定（GitHub Secretsに登録してください）",
                        link_for(f"macro:{s['id']}"))
        return 0, len(FRED_SERIES)

    ok, ng = 0, 0
    for s in FRED_SERIES:
        item = f"macro:{s['id']}"
        try:
            url = FRED_URL.format(sid=s["id"], key=FRED_KEY, start=start)
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.loads(r.read().decode())
            obs = data.get("observations", [])
            if not obs:
                raise ValueError("観測データなし")
            rows = 0
            for o in obs:
                v = o.get("value")
                if v in (None, ".", ""):
                    continue
                upsert_macro(conn, s["id"], o["date"], float(v), "FRED",
                             release_date=o.get("realtime_start"))
                rows += 1
            conn.commit()
            log_quality(conn, run_id, item, "ok", f"{rows}行取得")
            ok += 1
        except Exception as e:
            conn.rollback()
            log_quality(conn, run_id, item, "failed",
                        f"{type(e).__name__}: {e}", link_for(item))
            ng += 1
    return ok, ng
