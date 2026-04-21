#!/usr/bin/env python3
"""重建行情向量索引（使用 k_raw_v3 + 后复权因子）"""
import sys, os, json, time
sys.path.insert(0, '/program/stock')
import numpy as np
import pymongo
import faiss
from datetime import datetime, timedelta
from pathlib import Path

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')
DATA_DIR = Path('/program/stock/data')
DATA_DIR.mkdir(exist_ok=True)
DIM = 50

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=60000)['stock']

def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / (avg_loss + 1e-9)))

def compute_vector(closes, highs, lows, volumes, pct, turnover):
    """用后复权价格计算50维向量"""
    n = len(closes)
    vec = np.zeros(DIM, dtype=np.float32)
    i = 0

    # ── 1. 动量因子（10维）───────────────────────────────
    for d in [1, 5, 10, 20, 60]:
        if d <= n and closes[-d] > 0:
            vec[i] = float(np.clip((closes[-1] - closes[-d]) / closes[-d] * 5, -1, 1))
        i += 1
    if n >= 6:
        vec[i] = float(np.clip((pct[-1] - pct[-6]) * 10, -1, 1))
    i += 1
    if n >= 60 and highs[-60:].max() > 0:
        vec[i] = float(np.clip(closes[-1] / highs[-60:].max() - 0.5, -1, 1))
    i += 1
    for d in [5, 20]:
        if n >= d and closes[-d] > 0:
            vec[i] = float(np.clip((closes[-1] - closes[-d]) / closes[-d] * 5, -1, 1))
        i += 1

    # ── 2. 技术因子（20维）───────────────────────────────
    vec[i] = float((compute_rsi(closes) - 50) / 50)
    i += 1
    if n >= 20:
        ema12 = np.mean(closes[-12:]) if n >= 12 else closes[-1]
        ema26 = np.mean(closes[-26:]) if n >= 26 else closes[-1]
        macd = (ema12 - ema26) / (np.mean(closes[-9:]) + 1e-9)
        vec[i] = float(np.clip(macd * 5, -1, 1))
    i += 1
    if n >= 20:
        sma20 = np.mean(closes[-20:])
        std20 = np.std(closes[-20:])
        vec[i] = float(np.clip((closes[-1] - sma20) / (2 * std20 + 1e-9), -1, 1))
    i += 1
    for p in [5, 10, 20]:
        if n >= p:
            x = np.arange(p)
            slope = np.polyfit(x, closes[-p:], 1)[0]
            angle = np.arctan(slope / (np.mean(closes[-p:]) + 1e-9)) * 180 / np.pi
            vec[i] = float(np.clip(angle / 45, -1, 1))
        i += 1
    if n >= 20 and volumes[-20:].mean() > 0:
        vec[i] = float(np.clip((volumes[-1] / volumes[-20:].mean()) - 1, -1, 1))
    i += 1
    if n >= 5:
        vec[i] = float(np.clip((turnover[-1] - np.mean(turnover[-5:])) * 10, -1, 1))
    i += 1
    if n >= 14:
        tr = np.maximum(highs[-14:] - lows[-14:], np.abs(highs[-14:] - closes[-15:-1]))
        atr = np.mean(tr)
        vec[i] = float(np.clip(atr / (closes[-1] + 1e-9), 0, 1))
    i += 1
    if n >= 9:
        low9, high9 = lows[-9:].min(), highs[-9:].max()
        if high9 > low9:
            rsv = (closes[-1] - low9) / (high9 - low9) * 100
            vec[i] = float((rsv - 50) / 50)
    i += 1
    if n >= 14:
        wm = (highs[-14:].max() - closes[-1]) / (highs[-14:].max() - lows[-14:].min() + 1e-9)
        vec[i] = float(wm * 2 - 1)
    i += 1
    if n >= 5:
        obv = np.sum(np.where(pct > 0, volumes, -volumes))
        obv_prev = np.sum(np.where(pct[:-1] > 0, volumes[:-1], -volumes[:-1]))
        vec[i] = float(np.clip((obv - obv_prev) / (np.sum(volumes[-5:]) + 1e-9), -1, 1))
    i += 1
    while i < 30:
        i += 1

    # ── 3. 波动率因子（10维）────────────────────────────
    for p in [5, 10, 20, 60]:
        vec[i] = float(np.clip(np.std(pct[-p:]) * np.sqrt(252) if len(pct) >= p else 0, 0, 1))
        i += 1
    if n >= 20:
        ret = pct[-20:]
        std_ret = np.std(ret)
        if std_ret > 1e-9:
            skew = float(np.mean(((ret - np.mean(ret)) / std_ret) ** 3))
        else:
            skew = 0.0
        vec[i] = float(np.clip(skew / 3, -1, 1))
    i += 1
    if n >= 20:
        ret = pct[-20:]
        std_ret = np.std(ret)
        if std_ret > 1e-9:
            kurt = float(np.mean(((ret - np.mean(ret)) / std_ret) ** 4)) - 3
        else:
            kurt = 0.0
        vec[i] = float(np.clip(kurt / 3, -1, 1))
    i += 1
    if n >= 20:
        up = np.std(pct[-20:][pct[-20:] > 0]) if len(pct[-20:][pct[-20:] > 0]) > 1 else 0
        dn = np.std(pct[-20:][pct[-20:] < 0]) if len(pct[-20:][pct[-20:] < 0]) > 1 else 0
        vec[i] = float(np.clip((up - dn) / (up + dn + 1e-9), -1, 1))
    i += 1
    while i < 40:
        i += 1

    # ── 4. 趋势因子（5维）───────────────────────────────
    if n >= 20:
        x = np.arange(20)
        slope = np.polyfit(x, closes[-20:], 1)[0]
        angle = np.arctan(slope / (np.mean(closes[-20:]) + 1e-9)) * 180 / np.pi
        vec[i] = float(np.clip(angle / 45, -1, 1))
    i += 1
    ma_scores = [1 if n >= p and closes[-1] > closes[-p] else -1 for p in [5, 10, 20, 60] if n >= p]
    if ma_scores:
        vec[i] = float(np.mean(ma_scores))
    i += 1
    if n >= 20:
        ma5, ma10, ma20 = closes[-5:].mean(), closes[-10:-5].mean() if n >= 10 else closes[-10:].mean(), closes[-20:-10].mean() if n >= 20 else closes[-20:].mean()
        conv = 1 - np.std([ma5, ma10, ma20]) / (np.mean([ma5, ma10, ma20]) + 1e-9)
        vec[i] = float(np.clip(conv, -1, 1))
    i += 1
    if n >= 20:
        vec[i] = float(np.sum(pct[-20:] > 0) / 20 * 2 - 1)
    i += 1
    if n >= 5:
        vec[i] = float(np.clip(np.std(pct[-3:]) * 50, -1, 1))
    i += 1

    norm = np.linalg.norm(vec)
    if norm > 1e-9:
        vec = vec / norm
    return vec.astype(np.float32)


# ── 主流程：使用 k_raw_v3 + hfq_factor 后复权 ───────────────
print("Loading data from k_raw_v3 (K3) with hfq adjustment...")
db = get_db()

# 获取最近60个交易日
latest_date_doc = db['k_raw_v3'].find_one(sort=[('date', -1)])
latest_date = latest_date_doc['date']
cutoff = (datetime.strptime(latest_date, '%Y-%m-%d') - timedelta(days=90)).strftime('%Y-%m-%d')
print(f"Date range: {cutoff} ~ {latest_date} (60+ trading days)")

# 获取所有股票代码（最近有数据的）
pipeline_codes = [
    {'$match': {'date': {'$gte': cutoff}}},
    {'$group': {'_id': '$code'}},
    {'$limit': 6000}
]
codes = [r['_id'] for r in db['k_raw_v3'].aggregate(pipeline_codes, allowDiskUse=True)]
print(f"Codes with recent data: {len(codes)}")

# 预加载 hfq_factor（构建 date→factor 映射）
print("Loading hfq_factors...")
factors = {}
for f in db['k_factors_v3'].find({'date': {'$gte': cutoff}}):
    key = (f['code'], f['date'])
    factors[key] = f['hfq_factor']
print(f"  Loaded {len(factors)} factors")

# 批量获取k_raw_v3数据，同时应用复权因子
print("Loading and adjusting k_raw_v3 bars...")
bars_by_code = {}
batch_size = 100
for i in range(0, len(codes), batch_size):
    batch = codes[i:i+batch_size]
    rows = list(db['k_raw_v3'].find(
        {'code': {'$in': batch}, 'date': {'$gte': cutoff}},
        {'code': 1, 'date': 1, 'open': 1, 'high': 1, 'low': 1, 'close': 1, 'vol': 1, 'turnover': 1}
    ).sort('date', 1))
    
    for r in rows:
        code = r['code']
        date = r['date']
        fkey = (code, date)
        hfq = factors.get(fkey, 1.0)
        if code not in bars_by_code:
            bars_by_code[code] = []
        bars_by_code[code].append({
            'close': r['close'] * hfq,
            'open': r['open'] * hfq,
            'high': r['high'] * hfq,
            'low': r['low'] * hfq,
            'vol': r.get('vol', 0),
            'turnover': r.get('turnover', 0),
        })
    
    print(f"  {min(i+batch_size, len(codes))}/{len(codes)}")

print(f"  Loaded {len(bars_by_code)} codes with bars")

# 向量化
print("Encoding vectors...")
vectors = []
meta = []
for code, bars in bars_by_code.items():
    if len(bars) < 30:
        continue
    closes = np.array([b['close'] for b in bars], dtype=np.float64)
    highs = np.array([b['high'] for b in bars], dtype=np.float64)
    lows = np.array([b['low'] for b in bars], dtype=np.float64)
    volumes = np.array([b['vol'] for b in bars], dtype=np.float64)
    turnover = np.array([b['turnover'] for b in bars], dtype=np.float64) / 100.0
    
    # 计算涨跌幅（用复权价格计算）
    pct = np.diff(closes) / closes[:-1]
    pct = np.concatenate([[0], pct])
    
    vec = compute_vector(closes, highs, lows, volumes, pct, turnover)
    vectors.append(vec)
    meta.append({'code': code})

vectors = np.vstack(vectors)
print(f"  Matrix: {vectors.shape}")

# 保存
np.save(DATA_DIR / 'stock_vectors.npy', vectors)
with open(DATA_DIR / 'stock_vectors_meta.json', 'w') as f:
    json.dump(meta, f)
print("Vectors saved.")

# FAISS index
print("Building FAISS index...")
index = faiss.IndexFlatIP(DIM)
index.add(vectors)
faiss.write_index(index, str(DATA_DIR / 'stock_index.faiss'))
print(f"✅ Index built: {vectors.shape[0]} stocks, dim={DIM}")

# 测试
print("\nTest similarity:")
D, I = index.search(vectors[:1], 6)
for sim, idx in zip(D[0][1:], I[0][1:]):
    print(f"  {meta[idx]['code']}: {sim:.4f}")
