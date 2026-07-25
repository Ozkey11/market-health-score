#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
batch/main.py — GitHub Actions から呼ばれる統合バッチ

実行するステップ:
  Step 1  価格         fetch_prices.fetch_all_prices       -> SQLite prices_daily
  Step 2  マクロ       fetch_macro.fetch_all_macro         -> SQLite macro_series
  Step 3  センチメント fetch_sentiment.fetch_all_sentiment -> SQLite sentiment_daily
  Step 4  ファンダ     fetch_fundamentals.run_fetch        -> data/fundamentals.json
  Step 5  配信JSON生成 build_json.*                        -> data/*.json

--------------------------------------------------------------------
2026-07-25 改修（作業1）
  従来この main.py は Step 4（ファンダ）しか呼んでおらず、
  fetch_prices.py / fetch_macro.py / fetch_sentiment.py / build_json.py は
  実装済みでありながら一度も実行されていなかった。
  そのため SQLite は sentiment_daily が4行、features_daily・scores_daily・
  supply_demand_daily は0行のまま放置され、data/ 配下も fundamentals.json だけが
  更新される状態だった。本改修で全ステップを配線する。
--------------------------------------------------------------------

設計方針:
  - 1つのステップが失敗しても後続を止めない。部分的な更新でも配信JSONは必ず作る
  - 失敗内容は data_quality_log に記録し、data/data_quality.json 経由でUIへ出す
  - 全ステップが失敗した場合、または配信JSONを作れなかった場合のみ exit 1
    （Actions側のコミットは if: always() なので、赤くなっても失敗記録は配信される）

単体実行:
  py batch\main.py                  ... 全ステップ
  py batch\main.py prices           ... 価格だけ
  py batch\main.py macro sentiment  ... 複数指定も可
  （指定できる名前: prices / macro / sentiment / fundamentals / json）
"""
import os
import sys
import uuid
import traceback

# batch/ ディレクトリをパスに追加（どこから実行してもimportできるように）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STEP_NAMES = ["prices", "macro", "sentiment", "fundamentals", "json"]


def _hr(title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def run_step(no, key, title, fn, counters, errors):
    """1ステップを実行。(成功数, 失敗数) を返す関数を受け取る。
       例外が出ても握りつぶして記録し、後続ステップを続行させる。"""
    _hr("Step {}: {}".format(no, title))
    try:
        ok, ng = fn()
        counters[key] = {"ok": ok, "failed": ng}
        print("  -> 成功 {} 件 / 失敗 {} 件".format(ok, ng))
        if ok == 0 and ng > 0:
            errors.append("{}: 全項目で取得失敗".format(key))
        return True
    except Exception as e:
        counters[key] = {"ok": 0, "failed": None,
                         "error": "{}: {}".format(type(e).__name__, e)}
        errors.append("{}: {}: {}".format(key, type(e).__name__, e))
        print("  [NG] ステップ全体が例外で停止: {}: {}".format(type(e).__name__, e))
        traceback.print_exc()
        return False


def main():
    # ── 実行するステップの決定（引数なしなら全部）──
    args = [a.lower() for a in sys.argv[1:] if not a.startswith("-")]
    targets = [s for s in STEP_NAMES if s in args] if args else list(STEP_NAMES)
    if args and not targets:
        print("[NG] 不明なステップ名: {}".format(args))
        print("     指定できるのは {} です".format(" / ".join(STEP_NAMES)))
        return 1

    run_id = uuid.uuid4().hex[:12]
    errors, counters, skipped = [], {}, []

    # ── DB接続（ここだけは失敗したら続行不能）──
    try:
        from db import connect, init_db, log_run, log_quality, now_iso
        conn = connect()
        init_db(conn)          # テーブルが無ければ作成（既存があれば何もしない）
        started_at = now_iso()
    except Exception as e:
        print("[NG] SQLiteの初期化に失敗しました: {}: {}".format(type(e).__name__, e))
        traceback.print_exc()
        return 1

    print("run_id      : {}".format(run_id))
    print("started_at  : {}".format(started_at))
    print("実行ステップ: {}".format(", ".join(targets)))

    # ── Step 1: 価格 ──
    if "prices" in targets:
        def _prices():
            from fetch_prices import fetch_all_prices
            return fetch_all_prices(conn, run_id)
        run_step(1, "prices", "価格データの取得（yfinance）", _prices, counters, errors)
    else:
        skipped.append("prices")

    # ── Step 2: マクロ ──
    if "macro" in targets:
        if not os.environ.get("FRED_API_KEY", "").strip():
            _hr("Step 2: マクロ指標の取得（FRED）")
            print("  [警告] FRED_API_KEY が未設定のためスキップします。")
            print("         GitHub の Settings -> Secrets and variables -> Actions に")
            print("         FRED_API_KEY を登録すると取得されるようになります。")
            print("         APIキーは https://fred.stlouisfed.org/docs/api/api_key.html で無料取得できます")
            counters["macro"] = {"ok": 0, "failed": 0, "skipped": "FRED_API_KEY未設定"}
            try:
                log_quality(conn, run_id, "macro:*", "failed",
                            "FRED_API_KEY未設定のためスキップ",
                            "https://fred.stlouisfed.org/docs/api/api_key.html")
                conn.commit()
            except Exception:
                conn.rollback()
            skipped.append("macro")
        else:
            def _macro():
                from fetch_macro import fetch_all_macro
                return fetch_all_macro(conn, run_id)
            run_step(2, "macro", "マクロ指標の取得（FRED）", _macro, counters, errors)
    else:
        skipped.append("macro")

    # ── Step 3: センチメント ──
    if "sentiment" in targets:
        def _sentiment():
            from fetch_sentiment import fetch_all_sentiment
            return fetch_all_sentiment(conn, run_id)
        run_step(3, "sentiment", "センチメント指標の取得（VIX・日経VI・F&G・PCR）",
                 _sentiment, counters, errors)
    else:
        skipped.append("sentiment")

    # ── Step 4: ファンダメンタル ──
    # 他と違い SQLite ではなく data/fundamentals.json を直接書き出す独立モジュール
    if "fundamentals" in targets:
        def _fund():
            from fetch_fundamentals import run_fetch
            run_fetch()
            return 1, 0
        run_step(4, "fundamentals", "ファンダメンタル指標の取得", _fund, counters, errors)
    else:
        skipped.append("fundamentals")

    # ── Step 5: 配信用JSONの生成 ──
    json_ok = False
    finished_at = now_iso()
    if "json" in targets:
        _hr("Step 5: 配信用JSONの生成")
        finished_at = now_iso()
        try:
            import build_json as bj
            bj.build_latest(conn, run_id)          # data/latest.json
            bj.build_history(conn)                 # data/history_1y.json
            bj.build_data_quality(conn, run_id)    # data/data_quality.json
            bj.build_sources_json()                # data/sources.json
            bj.build_api_status(run_id, started_at, finished_at, counters)  # data/api_status.json
            json_ok = True
            print("  -> 配信JSONを5件生成しました")
        except Exception as e:
            errors.append("json: {}: {}".format(type(e).__name__, e))
            print("  [NG] 配信JSONの生成に失敗: {}: {}".format(type(e).__name__, e))
            traceback.print_exc()
    else:
        skipped.append("json")

    # ── 実行記録 ──
    fetch_keys = [k for k in ("prices", "macro", "sentiment", "fundamentals") if k in counters]
    any_ok = any(counters[k].get("ok", 0) > 0 for k in fetch_keys)
    if not errors and not skipped:
        status = "ok"
    elif any_ok or json_ok:
        status = "partial"
    else:
        status = "failed"
    try:
        log_run(conn, run_id, started_at, finished_at, status,
                detail="; ".join(errors) if errors else "")
        conn.commit()
    except Exception:
        conn.rollback()

    # ── サマリー ──
    _hr("実行サマリー")
    for k in STEP_NAMES:
        if k == "json":
            if "json" in skipped:
                print("  [--] {:12s} : スキップ".format("json"))
            else:
                print("  [{}] {:12s} : 配信JSON生成".format("OK" if json_ok else "NG", "json"))
            continue
        c = counters.get(k)
        if c is None:
            print("  [--] {:12s} : スキップ".format(k))
        elif c.get("skipped"):
            print("  [--] {:12s} : スキップ（{}）".format(k, c["skipped"]))
        elif c.get("error"):
            print("  [NG] {:12s} : 例外 {}".format(k, c["error"]))
        else:
            mark = "OK" if c["ok"] > 0 else "NG"
            print("  [{}] {:12s} : 成功 {} / 失敗 {}".format(mark, k, c["ok"], c["failed"]))

    print("\n  ステータス: {}".format(status))
    if errors:
        print("  エラー {}件:".format(len(errors)))
        for e in errors:
            print("    [NG] {}".format(e))
        print("\n  ※ 失敗した項目は data/data_quality.json に記録され、")
        print("     アプリ側で取得先リンクとともに表示されます。")

    try:
        conn.close()
    except Exception:
        pass

    # 配信JSONが作れて、かつ何か1つでも取得できていれば成功扱いにする。
    # （部分的な失敗でActionsを赤くすると、失敗記録そのものが配信されなくなるため）
    if "json" in targets and not json_ok:
        return 1
    if fetch_keys and not any_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
