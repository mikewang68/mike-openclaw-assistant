#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_vector_encoder.py - 股票行情向量编码器
把每只股票的行情特征编码为50维向量，用FAISS做相似度检索

使用方法：
  python3 stock_vector_encoder.py --build    # 全量建索引
  python3 stock_vector_encoder.py --search 000001  # 查找相似股
"""

import os, sys, json, argparse
import numpy as np
import pymongo
import faiss
from datetime import datetime, timedelta
from pathlib import Path

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')
DATA_DIR = Path(__file__).parent / "data"
VECTOR_FILE = DATA_DIR / "stock_vectors.npy"
INDEX_FILE = DATA_DIR / "stock_index.faiss"
META_FILE = DATA_DIR / "stock_vectors_meta.json"
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

def compute_vector(bars):
    """对单只股票的行情数据生成50维特征向量"""
    DIM = 50
    closes = np.array([b['收盘'] for b in bars], dtype=np.float64)
    highs = np.array([b['最高'] for b in bars], dtype=np.float64)
    lows = np.array([b['最低'] for b in bars], dtype=np.float64)
    volumes = np.array([b.get('成交量', 0) for b in bars], dtype=np.float64)
    pct = np.array([b.get('涨跌幅', 0) for b in bars], dtype=np.float64) / 100.0
    turnover = np.array([b.get('换手率', 0) for b in bars], dtype=np.float64) / 100.0
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


def build_index(days=60):
    """全量构建FAISS索引（高效聚合查询版）"""
    DATA_DIR.mkdir(exist_ok=True)
    db = get_db()

    latest = db['k_data'].find_one(sort=[('日期', -1)])
    cutoff = (datetime.strptime(latest['日期'], '%Y-%m-%d') - timedelta(days=days)).strftime('%Y-%m-%d')
    print(f"📊 日期范围: {cutoff} ~ {latest['日期']}")

    pipeline = [
        {'$match': {'日期': {'$gte': cutoff}}},
        {'$sort': {'日期': 1}},
        {'$group': {
            '_id': '$股票代码',
            'bars': {'$push': {
                '收盘': '$收盘', '开盘': '$开盘', '最高': '$最高',
                '最低': '$最低', '成交量': '$成交量', '涨跌幅': '$涨跌幅', '换手率': '$换手率'
            }}
        }},
        {'$limit': 6000}
    ]
    print("🔄 聚合查询行情数据（约1分钟）...")
    rows = list(db['k_data'].aggregate(pipeline, allowDiskUse=True, maxTimeMS=300000))
    print(f"  获取 {len(rows)} 只股票")

    print("⚙️  向量化...")
    vectors = []
    meta = []
    for r in rows:
        code = r['_id']
        bars = r['bars']
        if len(bars) < 5:
            continue
        vec = compute_vector(bars)
        vectors.append(vec)
        meta.append({'code': code})

    vectors = np.vstack(vectors)
    print(f"  向量矩阵: {vectors.shape}")

    np.save(VECTOR_FILE, vectors)
    with open(META_FILE, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)

    print("🔍 构建FAISS索引...")
    index = faiss.IndexFlatIP(DIM)
    index.add(vectors)
    faiss.write_index(index, str(INDEX_FILE))
    print(f"✅ 索引完成: {vectors.shape[0]} 只股票, dim={DIM}")
    return index, meta


def load_index():
    """加载已有索引"""
    if not VECTOR_FILE.exists() or not INDEX_FILE.exists():
        return None, None
    vectors = np.load(VECTOR_FILE)
    index = faiss.read_index(str(INDEX_FILE))
    with open(META_FILE, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    return index, meta


def search(query_code, top_k=20, days=60):
    """
    查找与query_code最相似的top_k只股票
    返回: [(code, similarity), ...]
    """
    index, meta = load_index()
    if index is None:
        print("⚠️ 索引不存在，请先运行 --build")
        return []

    code_to_idx = {m['code']: i for i, m in enumerate(meta)}
    if query_code not in code_to_idx:
        print(f"⚠️ {query_code} 不在索引中")
        return []

    query_idx = code_to_idx[query_code]
    query_vec = index.reconstruct(query_idx).reshape(1, -1)
    D, I = index.search(query_vec, top_k + 1)

    results = []
    for sim, idx in zip(D[0], I[0]):
        if idx < 0 or idx >= len(meta):
            continue
        if meta[idx]['code'] == query_code:
            continue
        results.append((meta[idx]['code'], float(sim)))

    return results[:top_k]


def test_similarity(n=5):
    """随机选n只股票，打印相似股"""
    index, meta = load_index()
    if index is None:
        return
    print(f"\n{'='*60}")
    print(f"📊 随机 {n} 只股票的 Top5 相似股")
    print(f"{'='*60}")
    import random
    for code in random.sample([m['code'] for m in meta], min(n, len(meta))):
        results = search(code, top_k=5)
        print(f"\n{code}:")
        for c, s in results:
            print(f"  → {c}: {s:.4f}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='股票行情向量编码器')
    parser.add_argument('--build', action='store_true', help='全量建索引')
    parser.add_argument('--search', type=str, help='查询某只股票的相似股')
    parser.add_argument('--topk', type=int, default=10, help='返回TopK相似股')
    parser.add_argument('--days', type=int, default=60, help='回溯天数')
    args = parser.parse_args()

    if args.build:
        print(f"🔨 全量建索引（回溯{args.days}天）...")
        build_index(days=args.days)

    if args.search:
        results = search(args.search, top_k=args.topk, days=args.days)
        print(f"\n📈 {args.search} 的 Top{len(results)} 相似股:")
        for c, s in results:
            print(f"  {c}: similarity={s:.4f}")

    if not args.build and not args.search:
        # 默认测试
        test_similarity(n=3)
