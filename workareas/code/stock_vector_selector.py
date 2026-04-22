#!/usr/bin/env python3
"""
向量化选股系统 - 严格50D G1向量 + G2财务 + 回测验证
用法:
  python3 stock_vector_selector.py                    # 快速G1回测
  python3 stock_vector_selector.py --group G1 --hold 5 --limit 300
  python3 stock_vector_selector.py --live            # 实时选股
"""

import os, sys, json, warnings
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np
import pymongo
import faiss
import requests
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score

warnings.filterwarnings('ignore')

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')
OLLAMA_URL = 'http://192.168.1.2:11434/api/embed'
OLLAMA_MODEL = 'nomic-embed-text:latest'
DATA_DIR = Path(__file__).parent / "data"

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

# ─── G1: 行情技术向量（严格50D，已修复边界）────────────────
def compute_g1(bars):
    closes = np.array([b['close'] for b in bars], dtype=np.float64)
    highs = np.array([b['high'] for b in bars], dtype=np.float64)
    lows  = np.array([b['low'] for b in bars], dtype=np.float64)
    vols  = np.array([b.get('vol', 0) for b in bars], dtype=np.float64)
    pct   = np.array([b.get('pct_chg', 0) for b in bars], dtype=np.float64) / 100.0
    turn  = np.array([b.get('turnover', 0) for b in bars], dtype=np.float64) / 100.0
    n = len(closes)

    def c(x, a=-1, b=1): return float(np.clip(x, a, b))

    v = np.zeros(50, dtype=np.float32)

    # 1-4: 动量乖离率（5/10/20/60日）
    for j, d in enumerate([5, 10, 20, 60]):
        prev = closes[-d] if d <= n else closes[0]
        v[j] = c((closes[-1] - prev) / (prev + 1e-9) * 5)

    # 5: 当日涨跌
    v[4] = c(pct[-1] * 5)

    # 6: RSI
    if n >= 14:
        deltas = np.diff(closes)
        gains  = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        ag = np.mean(gains[-14:])
        al = np.mean(losses[-14:])
        rsi = 100 - (100 / (1 + ag / (al + 1e-9))) if al > 0 else 100
    else:
        rsi = 50
    v[5] = c((rsi - 50) / 50)

    # 7: MACD
    if n >= 26:
        ema12 = np.mean(closes[-12:]) if n >= 12 else closes[-1]
        ema26 = np.mean(closes[-26:])
        macd  = (ema12 - ema26) / (np.mean(closes[-9:]) + 1e-9)
        v[6] = c(macd * 5)

    # 8: 布林带
    if n >= 20:
        sma20 = np.mean(closes[-20:])
        std20 = np.std(closes[-20:])
        v[7] = ((closes[-1] - sma20) / (2*std20 + 1e-9)) if std20 > 0 else 0.0

    # 9-12: 均线多头(5/10/20/60)
    for j, d in enumerate([5, 10, 20, 60]):
        prev = closes[-d] if d <= n else closes[0]
        v[8+j] = 1.0 if closes[-1] > prev else -1.0

    # 13-17: 量比
    for j, d in enumerate([1, 5, 10, 20]):
        prev = vols[-d] if d <= n else vols[0]
        v[12+j] = c(vols[-1] / (prev + 1e-9) - 1)
    v[16] = c(vols[-1] / (np.mean(vols[-5:]) + 1e-9) - 1) if n >= 5 else 0.0

    # 18-20: 价格位置
    if n >= 20:
        h20, l20 = np.max(highs[-20:]), np.min(lows[-20:])
        v[17] = c((closes[-1] - l20) / (h20 - l20 + 1e-9) * 2 - 1)
        v[18] = c((closes[-1] - np.mean(closes[-20:])) / (np.std(closes[-20:]) + 1e-9), -3, 3)
    v[19] = c(closes[-1] / (np.max(highs[-5:]) + 1e-9) - 1) if n >= 5 else 0.0

    # 21-23: 波动率
    v[20] = c(np.std(pct[-20:]) * 10) if n >= 20 else 0.0
    v[21] = c(np.std(pct[-5:]) * 10) if n >= 5 else 0.0
    v[22] = c(np.std(closes[-20:]) / (np.mean(closes[-20:]) + 1e-9)) if n >= 20 else 0.0

    # 23-25: 换手率
    v[23] = c(turn[-1] * 2) if len(turn) > 0 else 0.0
    v[24] = c(turn[-1] / (np.mean(turn[-5:]) + 1e-9) - 1) if n >= 5 else 0.0
    v[25] = c(np.mean(turn[-20:]) * 2) if n >= 20 else 0.0

    # 26-29: 动量增强（5/10/20/60日）
    for j, d in enumerate([5, 10, 20, 60]):
        prev = closes[-d] if d <= n else closes[0]
        v[26+j] = c((closes[-1] - prev) / (prev + 1e-9) * 3)

    # 30: 加速度
    v[30] = c((pct[-1] - pct[-5]) * 10) if n >= 6 else 0.0

    # 31-34: 成交量趋势
    for j, d in enumerate([5, 10, 20, 60]):
        if n >= d*2:
            v[31+j] = c(np.mean(vols[-d:]) / (np.mean(vols[-(d*2):-d]) + 1e-9) - 1)

    # 35:成交量爆发
    if n >= 5:
        v[35] = c((vols[-1] - np.mean(vols[-5:])) / (np.std(vols[-5:]) + 1e-9))
    else:
        v[35] = 0.0

    # 36-39: 价格相对强弱
    for j, d in enumerate([5, 10, 20, 60]):
        if n >= d:
            rng = np.max(highs[-d:]) - np.min(lows[-d:])
            v[36+j] = c((closes[-1] - closes[-d]) / (rng + 1e-9) * 3)

    # 40: 中期趋势
    v[40] = c((closes[-1] - np.mean(closes[-20:])) / (np.mean(closes[-20:]) + 1e-9) * 5) if n >= 20 else 0.0

    # 41-44: 资金情绪
    if n >= 20:
        avg_v = np.mean(vols[-20:])
        std_v = np.std(vols[-20:])
        v[41] = c((vols[-1] - avg_v) / (std_v + 1e-9))
    v[42] = c(np.mean(pct[-5:]) * 5) if n >= 5 else 0.0
    v[43] = c(np.mean(pct[-20:]) * 5) if n >= 20 else 0.0
    v[44] = c((np.max(pct[-20:]) - np.min(pct[-20:])) * 5) if n >= 20 else 0.0

    # 45: 市场方向
    v[45] = 1.0 if pct[-1] > 0 else -1.0

    # 46-49: 综合技术
    if n >= 20:
        sma20 = np.mean(closes[-20:])
        std20 = np.std(closes[-20:])
        h20, l20 = np.max(highs[-20:]), np.min(lows[-20:])
        v[46] = c((closes[-1] - sma20) / (2*std20 + 1e-9))
        v[47] = c((closes[-1] - l20) / (h20 - l20 + 1e-9), 0, 1)
        v[49] = c(vols[-1] / (np.median(vols[-20:]) + 1e-9) - 1)
    v[48] = v[46] if n >= 20 else 0.0

    return v

# ─── G2: 财务向量（8D）────────────────────────────────────
def compute_g2(code, db):
    yjbb  = db['fin_yjbb'].find_one({'股票代码': code}, sort=[('最新公告日期', -1)])
    zcfz  = db['fin_zcfz'].find_one({'股票代码': code}, sort=[('公告日期', -1)])
    fcst  = db['fin_forecast'].find_one({'股票代码': code}, sort=[('日期', -1)])
    v = np.zeros(8, dtype=np.float32)
    def c(x, a=-1, b=1): return float(np.clip(x, a, b))
    def clip_roe(x): return c((x - 10) / 15) if x and x > 0 else -0.5
    v[0] = clip_roe(yjbb.get('净资产收益率'))
    v[1] = c((yjbb.get('净利润-同比增长', 0) - 20) / 40) if yjbb else 0.0
    v[2] = c((yjbb.get('营业总收入-同比增长', 0) - 15) / 30) if yjbb else 0.0
    v[3] = c((yjbb.get('每股经营现金流量', 0) - 0.5) / 2) if yjbb else 0.0
    v[4] = c((60 - zcfz.get('资产负债率', 60)) / 30) if zcfz else 0.0
    v[5] = c((yjbb.get('毛利率', 20) - 20) / 30) if yjbb else 0.0
    v[6] = c(yjbb.get('每股经营现金流量', 0) / (yjbb.get('每股净资产', 1) + 1e-9) / 2) if yjbb else 0.0
    v[7] = c((fcst.get('机构投资评级(近六个月)-买入', 0) - 5) / 10) if fcst else 0.0
    return v

# ─── 加载历史K线 ─────────────────────────────────────────
def load_bars(code, end_date, all_dates, lookback=80):
    idx = np.searchsorted(all_dates, end_date)
    start_idx = max(0, idx - lookback)
    dates_needed = all_dates[start_idx:idx]
    docs = list(get_db()['k_raw_v3'].find({
        'code': code, 'date': {'$in': dates_needed}
    }))
    docs.sort(key=lambda x: x['date'])
    return [{
        'close': d['close'],
        'high':  d.get('high', d['close']),
        'low':   d.get('low', d['close']),
        'vol':   d.get('vol', 0),
        'pct_chg': d.get('pct_chg', 0),
        'turnover': d.get('turnover', 0)
    } for d in docs]

def get_return(code, date_idx, all_dates, n_days):
    if date_idx + n_days >= len(all_dates):
        return None
    db = get_db()
    p1 = db['k_raw_v3'].find_one({'code': code, 'date': all_dates[date_idx]})
    p2 = db['k_raw_v3'].find_one({'code': code, 'date': all_dates[date_idx + n_days]})
    if not p1 or not p2 or p1.get('close', 0) <= 0:
        return None
    return (p2['close'] - p1['close']) / p1['close']

# ─── 单次回测 ──────────────────────────────────────────────
def run_backtest(date_str, groups=['G1'], hold_days=5, top_k=10, limit=None, threshold=0.05, model='gb'):
    db = get_db()
    all_dates = sorted(db['k_raw_v3'].distinct('date'))
    if date_str not in all_dates:
        idx = np.searchsorted(all_dates, date_str)
        date_str = all_dates[max(0, idx - 1)]

    klines = {d['code']: d for d in db['k_raw_v3'].find({'date': date_str})}
    codes = list(klines.keys())[:limit] if limit else list(klines.keys())

    vectors = []
    valid = []
    for code in codes:
        bars = load_bars(code, date_str, all_dates)
        if len(bars) < 30:
            continue
        parts = []
        if 'G1' in groups:
            parts.append(compute_g1(bars))
        if 'G2' in groups:
            parts.append(compute_g2(code, db))
        if parts:
            vectors.append(np.concatenate(parts))
            valid.append(code)

    if not vectors:
        return {}

    X = np.vstack(vectors)
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    date_idx = all_dates.index(date_str)
    labels = []
    for code in valid:
        ret = get_return(code, date_idx, all_dates, hold_days)
        labels.append(1 if ret and ret > threshold else 0)

    if sum(labels) < 3:
        return {}

    clf = (GradientBoostingClassifier(n_estimators=30, max_depth=3, random_state=42)
           if model == 'gb' else RandomForestClassifier(n_estimators=30, max_depth=3, random_state=42))
    clf.fit(Xs, labels)
    probs = clf.predict_proba(Xs)[:, 1]
    top_idx = np.argsort(probs)[-top_k:]
    top_codes = [valid[i] for i in top_idx]

    rets = []
    for code in top_codes:
        r = get_return(code, date_idx, all_dates, hold_days)
        if r is not None:
            rets.append(r)

    return {
        'stocks': [{'code': c, 'name': klines[c].get('name', c)} for c in top_codes],
        'avg_return': np.mean(rets) if rets else 0,
        'win_rate': sum(1 for r in rets if r > 0) / max(len(rets), 1),
        'avg_pct': f"{np.mean(rets)*100:.2f}%",
        'count': len(rets)
    }

# ─── 批量回测 ──────────────────────────────────────────────
def full_backtest(dates, groups=['G1'], hold_days=5, threshold=0.05, top_k=10, limit=None, model='gb'):
    db = get_db()
    all_dates = sorted(db['k_raw_v3'].distinct('date'))
    valid_dates = sorted([d for d in dates if d in all_dates], key=lambda x: all_dates.index(x))

    gname = '+'.join(groups)
    # dim unused
    print(f"\n{'='*60}")
    print(f"📊 {gname} 回测 | threshold>{threshold:.0%} | top-{top_k} | hold={hold_days}d | {len(valid_dates)}截点")
    print(f"   截点: {valid_dates}")
    print(f"{'='*60}")

    all_rets, all_wrs = [], []
    for d in valid_dates:
        r = run_backtest(d, groups, hold_days, top_k, limit, threshold, model)
        if r and r['count'] > 0:
            all_rets.append(r['avg_return'])
            all_wrs.append(r['win_rate'])
            print(f"  ▶ {d}: 均={r['avg_pct']} 胜={r['win_rate']:.0%} n={r['count']}")

    if not all_rets:
        print("  ⚠️ 无有效数据")
        return None

    sharpe = np.mean(all_rets) / (np.std(all_rets) + 1e-9)
    print(f"\n📈 汇总: 均收益={np.mean(all_rets)*100:.2f}% | 胜率={np.mean(all_wrs):.0%} | 夏普={sharpe:.2f} | 样本={len(all_rets)}")
    return {'gname': gname, 'hold': hold_days, 'rets': all_rets, 'wrs': all_wrs, 'sharpe': sharpe}

# ─── 实时选股 ──────────────────────────────────────────────
def live_select(groups=['G1'], top_k=10, hold_days=5, limit=None):
    db = get_db()
    all_dates = sorted(db['k_raw_v3'].distinct('date'))
    latest = all_dates[-1]
    latest_idx = len(all_dates) - 1
    
    # 实时预测：用最近有future数据的日期训练，latest日期预测
    # 确保latest+hold_days <= 最后一个交易日
    train_date_idx = max(0, latest_idx - hold_days)
    train_date = all_dates[train_date_idx]
    
    # 加载特征
    klines = {d['code']: d for d in db['k_raw_v3'].find({'date': latest})}
    code_map = {d['code']: d.get('name', d['code']) for d in db['code'].find({}, {'code': 1, 'name': 1})}
    codes = list(klines.keys())[:limit] if limit else list(klines.keys())
    
    vectors = []
    valid = []
    for code in codes:
        bars = load_bars(code, latest, all_dates)
        if len(bars) < 30:
            continue
        parts = []
        if 'G1' in groups:
            parts.append(compute_g1(bars))
        if 'G2' in groups:
            parts.append(compute_g2(code, db))
        if parts:
            vectors.append(np.concatenate(parts))
            valid.append(code)
    
    if not vectors:
        print("  ⚠️ 无有效特征数据")
        return []
    
    X = np.vstack(vectors)
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    
    # 用train_date的label训练
    labels = []
    for code in valid:
        ret = get_return(code, train_date_idx, all_dates, hold_days)
        labels.append(1 if ret and ret > 0.03 else 0)
    
    clf = GradientBoostingClassifier(n_estimators=30, max_depth=3, random_state=42)
    clf.fit(Xs, labels)
    probs = clf.predict_proba(Xs)[:, 1]
    top_idx = np.argsort(probs)[-top_k:]
    
    results = []
    for i in top_idx:
        ret = get_return(valid[i], latest_idx, all_dates, hold_days)
        results.append({
            'code': valid[i], 
            'name': code_map.get(valid[i], valid[i]),
            'close': klines[valid[i]].get('close', 0),
            'prob': probs[i],
            'recent_ret': f"{ret*100:.2f}%" if ret else '—'
        })
    
    print(f"\n📈 实时选股: {latest} | {'+'.join(groups)} | top-{top_k} | 持仓{hold_days}日")
    print(f"   训练期: {train_date} | 预测期: {latest}")
    if results:
        for s in results:
            print(f"  ✅ {s['code']} {s['name']} 概率={s['prob']:.3f} 近5日={s['recent_ret']}")
    else:
        print("  无候选")
    return results

# ─── 入口 ──────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='backtest', choices=['backtest', 'live'])
    parser.add_argument('--group', default='G1', choices=['G1', 'G2', 'G12'])
    parser.add_argument('--hold', type=int, default=5)
    parser.add_argument('--threshold', type=float, default=0.05)
    parser.add_argument('--top', type=int, default=3)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--model', default='gb', choices=['gb', 'rf'])
    args = parser.parse_args()

    groups = ['G1', 'G2'] if args.group == 'G12' else [args.group]

    if args.mode == 'live':
        live_select(groups, args.top, args.hold, args.limit)
    else:
        dates = ['2025-01-02', '2025-04-02', '2025-07-02', '2025-10-08',
                 '2026-01-02', '2026-04-08']
        for hold in [5, 10, 20]:
            full_backtest(dates, groups, hold, args.threshold, args.top, args.limit, args.model)
        print("\n✅ 回测完成")
