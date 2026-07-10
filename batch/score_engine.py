#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
score_engine.py — 株価スコア計算エンジン (Python版)
HTMLアプリのLayer2+L3+L4+L5のスコア計算をPythonに移植。
bulk_score.pyから呼ばれ、1000銘柄の一括スコア計算に使用。
"""
import numpy as np

def sma(arr, n):
    if len(arr) < n: return None
    return np.mean(arr[-n:])

def rsi(closes, period=14):
    if len(closes) < period + 1: return None
    diffs = np.diff(closes[-(period+1):])
    gains = np.where(diffs > 0, diffs, 0)
    losses = np.where(diffs < 0, -diffs, 0)
    ag, al = np.mean(gains), np.mean(losses)
    if al == 0: return 100
    rs = ag / al
    return 100 - 100 / (1 + rs)

def mfi(highs, lows, closes, volumes, period=14):
    if len(closes) < period + 1: return None
    tp = (np.array(highs[-(period+1):]) + np.array(lows[-(period+1):]) + np.array(closes[-(period+1):])) / 3
    flow = tp[1:] * np.array(volumes[-period:])
    pos = np.where(np.diff(tp) > 0, flow, 0)
    neg = np.where(np.diff(tp) < 0, flow, 0)
    sp, sn = np.sum(pos), np.sum(neg)
    if sn == 0: return 100
    return 100 - 100 / (1 + sp / sn)

def ma_dev(closes, period):
    ma = sma(closes, period)
    if ma is None or ma == 0: return None
    return (closes[-1] / ma - 1) * 100

def atr_ratio(highs, lows, closes, atr_p=14, avg_p=20):
    if len(closes) < atr_p + avg_p + 1: return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    cur = np.mean(trs[-atr_p:])
    hist = trs[-(atr_p+avg_p):-atr_p]
    ha = np.mean(hist) if hist else cur
    return cur / ha if ha > 0 else 1

def price_z(closes, period=50):
    if len(closes) < period: return None
    seg = closes[-period:]
    mu, sd = np.mean(seg), np.std(seg)
    if sd == 0: return 0
    return (closes[-1] - mu) / sd

def compute_score(closes, highs, lows, volumes, vix=None, fund=None, market='US'):
    """
    簡易スコア計算: 0=大底 / 50=中立 / 100=天井
    fund: dict with hy_spread, shiller_pe, pe_ttm, yield_curve, move, copper_z, wti_z, dxy_z, ism, ism_prev
    """
    n = len(closes)
    if n < 60: return None

    # Layer2: テクニカル (0-100, 50=中立)
    rsi_v = rsi(closes)
    mfi_v = mfi(highs, lows, closes, volumes)
    ma50 = ma_dev(closes, 50)
    ma200 = ma_dev(closes, 200)
    atr_v = atr_ratio(highs, lows, closes)
    pz_v = price_z(closes)

    # 各指標の底/天井スコア (0-1)
    def clamp(x, lo, hi):
        if x is None: return 0.5
        return max(0, min(1, (x - lo) / (hi - lo)))

    # 底スコア (低いほど大底)
    bot = 0
    bot += 14 * (1 - clamp(rsi_v, 25, 70)) if rsi_v else 0
    bot += 16 * (1 - clamp(mfi_v, 15, 70)) if mfi_v else 0
    bot += 17 * clamp(-(ma50 or 0), 5, 15)
    bot += 14 * clamp((atr_v or 1) - 1, 0, 1.2)
    bot += 10 * clamp(-(pz_v or 0), 1, 3)
    bot_w = 71  # 合計重み

    # 天井スコア
    top = 0
    top += 16 * clamp(mfi_v, 70, 90) if mfi_v else 0
    top += 14 * clamp(rsi_v, 60, 80) if rsi_v else 0
    top += 12 * clamp(pz_v or 0, 1, 2.5)
    top += 10 * clamp(ma50 or 0, 5, 15)
    top_w = 52

    l2_bot = 100 * bot / bot_w if bot_w else 0
    l2_top = 100 * top / top_w if top_w else 0

    # Layer4: VIX
    vix_bot, vix_top = 0, 0
    if vix is not None:
        if vix >= 40: vix_bot = 100
        elif vix >= 28: vix_bot = 100 * (vix - 28) / 12
        if vix <= 12: vix_top = 100
        elif vix <= 18: vix_top = 100 * (18 - vix) / 6

    # テク+需給 合成 (L2:0.55 + L4:0.20, L3は省略)
    tq_bot = l2_bot * 0.75 + vix_bot * 0.25
    tq_top = l2_top * 0.75 + vix_top * 0.25

    # Layer5: ファンダ
    f_bot, f_top, f_bot_w, f_top_w = 0, 0, 0, 0
    if fund:
        hy = fund.get('hy_spread')
        if hy is not None:
            f_bot += 24 * clamp(hy, 5, 10); f_bot_w += 24
            f_top += 12 * clamp(3.5 - hy, 0, 1.2); f_top_w += 12
        mv = fund.get('move')
        if mv is not None:
            f_bot += 16 * clamp(mv, 120, 170); f_bot_w += 16
            f_top += 8 * clamp(80 - mv, 0, 25); f_top_w += 8
        sh = fund.get('shiller_pe')
        if sh is not None:
            f_top += 22 * clamp(sh, 30, 40); f_top_w += 22
        pt = fund.get('pe_ttm')
        if pt is not None:
            f_bot += 12 * clamp(16 - pt, 0, 4); f_bot_w += 12
            f_top += 14 * clamp(pt, 22, 28); f_top_w += 14
        yc = fund.get('yield_curve')
        if yc is not None:
            f_top += 22 * clamp(-yc, 0, 0.75); f_top_w += 22
        cz = fund.get('copper_z')
        if cz is not None:
            f_bot += 16 * clamp(-cz, 1, 3); f_bot_w += 16
        wz = fund.get('wti_z')
        if wz is not None:
            f_bot += 10 * clamp(-wz, 1, 3); f_bot_w += 10
        dz = fund.get('dxy_z')
        if dz is not None:
            f_bot += 10 * clamp(dz, 1, 2.5); f_bot_w += 10

    fund_bot = 100 * f_bot / f_bot_w if f_bot_w else 0
    fund_top = 100 * f_top / f_top_w if f_top_w else 0

    # 全総合
    if f_bot_w > 0:
        all_bot = 0.70 * tq_bot + 0.30 * fund_bot
        all_top = 0.55 * tq_top + 0.45 * fund_top
    else:
        all_bot, all_top = tq_bot, tq_top

    overall = 50 + (all_top - all_bot) / 2
    return round(max(0, min(100, overall)), 1)
