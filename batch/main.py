#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch/main.py — GitHub Actionsから呼ばれる統合バッチ
  1. fetch_fundamentals.py でファンダ指標を取得 → data/fundamentals.json
  2. (将来拡張用) 他のバッチ処理

エラーがあっても途中で止めず、最後にサマリーを出す。
"""
import sys
import os

# batch/ ディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    errors = []

    # ── ファンダメンタル指標の取得 ──
    print("=" * 60)
    print("Step 1: ファンダメンタル指標の取得")
    print("=" * 60)
    try:
        from fetch_fundamentals import run_fetch
        run_fetch()
    except Exception as e:
        print(f"✖ ファンダメンタル取得でエラー: {e}")
        errors.append(f"fundamentals: {e}")

    # ── サマリー ──
    print("\n" + "=" * 60)
    if errors:
        print(f"完了(エラーあり): {len(errors)}件")
        for e in errors:
            print(f"  ✖ {e}")
        sys.exit(1)
    else:
        print("全ステップ正常完了")

if __name__ == "__main__":
    main()
