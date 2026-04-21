#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_fusion_search.py - 行情+舆情融合搜索（分资产类别）

重要原则：
- A股和加密货币特征分布不同（涨跌幅/波动率/成交量机制不同）
- 同资产类别内融合（A股×A股，或加密货币×加密货币）
- 跨类别只做信息联动（通过舆情中的币种关联），不打分混合

架构：
  A股候选股 → A股行情FAISS(50D) + A股舆情FAISS(768D) → A股Top5
                          ↓ 币种关联检测
                   关联的加密货币 → 加密货币FAISS(50D/768D) → 币种Top5
"""

import os, sys, json, time
import numpy as np
import pymongo
import faiss
import requests
from datetime import datetime, timedelta
from pathlib import Path

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')

# ── 路径配置 ─────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / 'data'
MARKET_VEC = DATA_DIR / 'stock_vectors.npy'    # 50维行情向量（A股）
MARKET_IDX = DATA_DIR / 'stock_index.faiss'
MARKET_META = DATA_DIR / 'stock_vectors_meta.json'
SENTIMENT_VEC = DATA_DIR / 'sentiment_vectors.npy'  # 768维舆情（A股）
SENTIMENT_IDX = DATA_DIR / 'sentiment_index.faiss'
SENTIMENT_META = DATA_DIR / 'sentiment_meta.json'

MARKET_DIM = 50
SENTIMENT_DIM = 768

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

# ── 加密货币：获取Binance K线数据 ────────────────────────
BINANCE_PUBLIC = 'https://api.binance.com/api/v3'

def get_binance_klines(symbol='BTCUSDT', interval='1d', days=30):
    """获取Binance K线，返回OHLCV列表"""
    params = {'symbol': symbol, 'interval': interval, 'limit': days * 2}
    try:
        r = requests.get(f'{BINANCE_PUBLIC}/klines', params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return [{'open_time': k[0], 'open': float(k[1]), 'high': float(k[2]),
                     'low': float(k[3]), 'close': float(k[4]), 'vol': float(k[5])}
                    for k in data]
    except Exception as e:
        print(f'  ⚠️ Binance {symbol}: {e}')
    return []

def compute_crypto_features(klines):
    """
    从K线生成加密货币特征向量（50维，对齐A股市场向量结构）
    注意：这只用于加密货币×加密货币的相似度，不与A股直接比较
    """
    if len(klines) < 5:
        return None
    closes = np.array([k['close'] for k in klines], dtype=np.float64)
    highs = np.array([k['high'] for k in klines], dtype=np.float64)
    lows = np.array([k['low'] for k in klines], dtype=np.float64)
    vols = np.array([k['vol'] for k in klines], dtype=np.float64)
    pct = np.concatenate([[0], np.diff(closes) / closes[:-1]])
    n = len(closes)

    vec = np.zeros(50, dtype=np.float32)
    i = 0
    # 动量（10维）
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
    # 技术因子（20维）
    if n >= 15:
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        rsi = 100 - (100 / (1 + avg_gain / (avg_loss + 1e-9))) if avg_loss > 0 else 50
        vec[i] = float((rsi - 50) / 50)
    i += 1
    if n >= 26:
        ema12 = np.mean(closes[-12:]) if n >= 12 else closes[-1]
        ema26 = np.mean(closes[-26:])
        macd = (ema12 - ema26) / (np.mean(closes[-9:]) + 1e-9)
        vec[i] = float(np.clip(macd * 5, -1, 1))
    i += 1
    if n >= 20:
        sma20 = np.mean(closes[-20:])
        std20 = np.std(closes[-20:])
        bb = (closes[-1] - sma20) / (2 * std20 + 1e-9)
        vec[i] = float(np.clip(bb, -1, 1))
    i += 1
    for p in [5, 10, 20]:
        if n >= p:
            slope = np.polyfit(np.arange(p), closes[-p:], 1)[0]
            angle = np.arctan(slope / (np.mean(closes[-p:]) + 1e-9)) * 180 / np.pi
            vec[i] = float(np.clip(angle / 45, -1, 1))
        i += 1
    if n >= 20 and vols[-20:].mean() > 0:
        vec[i] = float(np.clip((vols[-1] / vols[-20:].mean()) - 1, -1, 1))
    i += 1
    if n >= 5:
        vec[i] = float(np.clip((vols[-1] - np.mean(vols[-5:])) / (np.mean(vols[-5:]) + 1e-9), -1, 1))
    i += 1
    if n >= 14:
        tr = np.maximum(highs[-14:] - lows[-14:], np.abs(highs[-14:] - closes[-15:-1]))
        vec[i] = float(np.clip(np.mean(tr) / (closes[-1] + 1e-9), 0, 1))
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
        obv = np.sum(np.where(pct > 0, vols, -vols))
        obv_prev = np.sum(np.where(pct[:-1] > 0, vols[:-1], -vols[:-1]))
        vec[i] = float(np.clip((obv - obv_prev) / (np.sum(vols[-5:]) + 1e-9), -1, 1))
    i += 1
    while i < 30:
        i += 1
    # 波动率（10维）
    for p in [5, 10, 20, 60]:
        vec[i] = float(np.clip(np.std(pct[-p:]) * np.sqrt(252) if len(pct) >= p else 0, 0, 1))
        i += 1
    if n >= 20:
        ret = pct[-20:]
        std = np.std(ret)
        vec[i] = float(np.clip(np.mean(((ret - np.mean(ret)) / (std + 1e-9)) ** 3) / 3 if std > 1e-9 else 0, -1, 1))
    i += 1
    if n >= 20:
        ret = pct[-20:]
        std = np.std(ret)
        vec[i] = float(np.clip((np.mean(((ret - np.mean(ret)) / (std + 1e-9)) ** 4) - 3) / 3 if std > 1e-9 else 0, -1, 1))
    i += 1
    if n >= 20:
        up = np.std(pct[-20:][pct[-20:] > 0]) if len(pct[-20:][pct[-20:] > 0]) > 1 else 0
        dn = np.std(pct[-20:][pct[-20:] < 0]) if len(pct[-20:][pct[-20:] < 0]) > 1 else 0
        vec[i] = float(np.clip((up - dn) / (up + dn + 1e-9), -1, 1))
    i += 1
    while i < 40:
        i += 1
    # 趋势（5维）
    if n >= 20:
        slope = np.polyfit(np.arange(20), closes[-20:], 1)[0]
        angle = np.arctan(slope / (np.mean(closes[-20:]) + 1e-9)) * 180 / np.pi
        vec[i] = float(np.clip(angle / 45, -1, 1))
    i += 1
    ma_scores = [1 if n >= p and closes[-1] > closes[-p] else -1 for p in [5, 10, 20, 60] if n >= p]
    if ma_scores:
        vec[i] = float(np.mean(ma_scores))
    i += 1
    if n >= 20:
        ma5, ma10, ma20 = closes[-5:].mean(), closes[-10:-5].mean(), closes[-20:-10].mean()
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

def build_crypto_index():
    """构建加密货币行情FAISS索引（仅用于加密货币×加密货币搜索）"""
    symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']
    vectors = []
    meta = []
    for sym in symbols:
        print(f"  Fetching {sym}...")
        klines = get_binance_klines(sym, interval='1d', days=60)
        if not klines:
            continue
        vec = compute_crypto_features(klines)
        if vec is not None:
            vectors.append(vec)
            meta.append({'code': sym, 'type': 'crypto', 'name': sym.replace('USDT', '/USDT')})
            print(f"    {sym}: OK ({len(klines)} klines)")
    if not vectors:
        print("⚠️ No crypto vectors built")
        return None, None
    vectors = np.vstack(vectors)
    index = faiss.IndexFlatIP(MARKET_DIM)
    index.add(vectors)
    DATA_DIR.mkdir(exist_ok=True)
    np.save(DATA_DIR / 'crypto_vectors.npy', vectors)
    faiss.write_index(index, str(DATA_DIR / 'crypto_index.faiss'))
    with open(DATA_DIR / 'crypto_meta.json', 'w') as f:
        json.dump(meta, f)
    print(f"✅ Crypto index: {len(vectors)} assets, dim={MARKET_DIM}")
    return index, meta

# ── A股融合搜索（行情×舆情）──────────────────────────────
def load_market_index():
    if not MARKET_IDX.exists():
        return None, None
    index = faiss.read_index(str(MARKET_IDX))
    with open(MARKET_META) as f:
        meta = json.load(f)
    return index, meta

def load_sentiment_index():
    if not SENTIMENT_IDX.exists():
        return None, None
    index = faiss.read_index(str(SENTIMENT_IDX))
    with open(SENTIMENT_META) as f:
        meta = json.load(f)
    return index, meta

def search_astock_fusion(query_code, market_index, market_meta, sentiment_index, sentiment_meta,
                          market_weight=0.6, sentiment_weight=0.4, top_k=10):
    """
    A股融合搜索：行情FAISS(50D) + 舆情FAISS(768D)
    仅返回A股股票，不包含加密货币
    """
    m_vectors = np.load(MARKET_VEC)
    s_vectors = np.load(SENTIMENT_VEC)

    # Market搜索
    m_code_to_idx = {m['code']: i for i, m in enumerate(market_meta)}
    market_sim = {}
    if query_code in m_code_to_idx:
        idx = m_code_to_idx[query_code]
        qv = m_vectors[idx].reshape(1, -1)
        D, I = market_index.search(qv, top_k + 1)
        for sim, i in zip(D[0], I[0]):
            if i >= 0 and i < len(market_meta) and market_meta[i]['code'] != query_code:
                market_sim[market_meta[i]['code']] = float(sim)

    # Sentiment搜索
    s_code_to_idx = {m['code']: i for i, m in enumerate(sentiment_meta)}
    sentiment_sim = {}
    if query_code in s_code_to_idx:
        idx = s_code_to_idx[query_code]
        qv = s_vectors[idx].reshape(1, -1)
        D, I = sentiment_index.search(qv, top_k + 1)
        for sim, i in zip(D[0], I[0]):
            if i >= 0 and i < len(sentiment_meta) and sentiment_meta[i]['code'] != query_code:
                sentiment_sim[sentiment_meta[i]['code']] = float(sim)

    # 融合打分（仅限A股）
    all_codes = set(list(market_sim.keys()) + list(sentiment_sim.keys()))
    results = []
    for code in all_codes:
        ms = market_sim.get(code, 0.0)
        ss = sentiment_sim.get(code, 0.0)
        has_market = len(market_sim) > 0
        has_sentiment = len(sentiment_sim) > 0
        if not has_sentiment:
            total = ms  # 只有行情
        elif not has_market:
            total = ss  # 只有舆情
        else:
            total = market_weight * ms + sentiment_weight * ss
        results.append((code, total, ms, ss))

    results.sort(key=lambda x: -x[1])
    return results[:top_k]

# ── 加密货币搜索（行情×新闻）──────────────────────────────
def load_crypto_market_index():
    p = DATA_DIR / 'crypto_index.faiss'
    if not p.exists():
        return None, None
    index = faiss.read_index(str(p))
    with open(DATA_DIR / 'crypto_meta.json') as f:
        meta = json.load(f)
    return index, meta

def load_crypto_news_index():
    p = DATA_DIR / 'crypto_news_index.faiss'
    if not p.exists():
        return None, None
    index = faiss.read_index(str(p))
    with open(DATA_DIR / 'crypto_news_meta.json') as f:
        meta = json.load(f)
    return index, meta

def search_crypto_fusion(query_coin, market_index, market_meta, news_index, news_meta,
                          market_weight=0.6, news_weight=0.4, top_k=5):
    """
    加密货币融合搜索：行情FAISS(50D) + 新闻FAISS(768D)
    仅限加密货币×加密货币，不与A股混合
    """
    try:
        c_vectors = np.load(DATA_DIR / 'crypto_vectors.npy')
    except:
        c_vectors = None
    try:
        n_vectors = np.load(DATA_DIR / 'crypto_news_vectors.npy')
    except:
        n_vectors = None

    market_sim = {}
    if market_index and c_vectors is not None:
        m_code_to_idx = {m['code']: i for i, m in enumerate(market_meta)}
        if query_coin in m_code_to_idx:
            idx = m_code_to_idx[query_coin]
            qv = c_vectors[idx].reshape(1, -1)
            D, I = market_index.search(qv, top_k + 1)
            for sim, i in zip(D[0], I[0]):
                if i >= 0 and i < len(market_meta) and market_meta[i]['code'] != query_coin:
                    market_sim[market_meta[i]['code']] = float(sim)

    news_sim = {}
    if news_index and n_vectors is not None:
        n_code_to_idx = {m['coin']: i for i, m in enumerate(news_meta)}
        if query_coin in n_code_to_idx:
            idx = n_code_to_idx[query_coin]
            qv = n_vectors[idx].reshape(1, -1)
            D, I = news_index.search(qv, top_k + 1)
            for sim, i in zip(D[0], I[0]):
                if i >= 0 and i < len(news_meta) and news_meta[i]['coin'] != query_coin:
                    news_sim[news_meta[i]['coin']] = float(sim)

    all_coins = set(list(market_sim.keys()) + list(news_sim.keys()))
    results = []
    for coin in all_coins:
        ms = market_sim.get(coin, 0.0)
        ss = news_sim.get(coin, 0.0)
        if len(news_sim) == 0:
            total = ms
        elif len(market_sim) == 0:
            total = ss
        else:
            total = market_weight * ms + news_weight * ss
        results.append((coin, total, ms, ss))

    results.sort(key=lambda x: -x[1])
    return results[:top_k]

# ── 跨类别联动搜索 ────────────────────────────────────────
def get_stock_crypto_links(query_code, sentiment_meta):
    """
    通过A股舆情中的coins字段，判断该股票关联哪些加密货币
    返回：[(coin, news_count), ...]
    """
    db = get_db()
    docs = list(db['stock_sentiment'].find(
        {'codes': query_code, 'coins': {'$exists': True, '$ne': []}},
        {'coins': 1}
    ).limit(50))
    coin_count = {}
    for d in docs:
        for c in d.get('coins', []) or []:
            if c and c.endswith('USDT'):
                coin_count[c] = coin_count.get(c, 0) + 1
    return sorted(coin_count.items(), key=lambda x: -x[1])

def search_with_linkage(query_code, top_k=10):
    """
    主搜索函数：
    1. A股融合搜索 → A股Top5（打分混合）
    2. 跨类别联动 → 关联加密货币 → 加密货币融合搜索 → 币种Top5
    
    A股和加密货币结果分开呈现，不混合打分
    """
    market_idx, market_meta = load_market_index()
    sentiment_idx, sentiment_meta = load_sentiment_index()
    crypto_mkt_idx, crypto_mkt_meta = load_crypto_market_index()
    crypto_news_idx, crypto_news_meta = load_crypto_news_index()

    print(f"  A股行情: {len(market_meta) if market_meta else 0} 只")
    print(f"  A股舆情: {len(sentiment_meta) if sentiment_meta else 0} 只")
    print(f"  加密货币行情: {len(crypto_mkt_meta) if crypto_mkt_meta else 0} 个")
    print(f"  加密货币新闻: {len(crypto_news_meta) if crypto_news_meta else 0} 个")

    # 1. A股融合搜索
    stock_results = []
    if market_idx and sentiment_idx:
        stock_results = search_astock_fusion(
            query_code, market_idx, market_meta,
            sentiment_idx, sentiment_meta,
            market_weight=0.6, sentiment_weight=0.4, top_k=top_k
        )

    # 2. 跨类别联动
    linked_coins = get_stock_crypto_links(query_code, sentiment_meta)
    
    crypto_results = []
    if linked_coins and (crypto_mkt_idx or crypto_news_idx):
        print(f"  联动加密货币: {[c for c, n in linked_coins[:3]]}")
        for coin, cnt in linked_coins[:3]:
            coin_results = search_crypto_fusion(
                coin, crypto_mkt_idx, crypto_mkt_meta,
                crypto_news_idx, crypto_news_meta,
                market_weight=0.6, news_weight=0.4, top_k=3
            )
            for coin_code, score, ms, ss in coin_results:
                crypto_results.append((coin_code, score, ms, ss, coin, cnt))

    return stock_results, crypto_results

# ── CLI ──────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--search', type=str, help='搜索A股股票代码')
    parser.add_argument('--crypto', action='store_true', help='重建加密货币行情索引')
    parser.add_argument('--topk', type=int, default=10)
    args = parser.parse_args()

    if args.crypto:
        build_crypto_index()

    if args.search:
        stock_r, crypto_r = search_with_linkage(args.search, top_k=args.topk)

        print(f"\n📊 A股融合搜索 Top{len(stock_r)} ({args.search})")
        print(f"{'代码':<10} {'综合':<8} {'行情':<8} {'舆情':<8}")
        print('-' * 40)
        for code, total, ms, ss in stock_r:
            print(f"{code:<10} {total:.4f}   {ms:.4f}   {ss:.4f}")

        if crypto_r:
            print(f"\n📈 相关加密货币（联动）:")
            print(f"{'币种':<12} {'综合':<8} {'行情':<8} {'新闻':<8} {'联动源':<10} {'新闻数'}")
            print('-' * 60)
            for coin, total, ms, ss, src, cnt in crypto_r:
                print(f"{coin:<12} {total:.4f}   {ms:.4f}   {ss:.4f}   {src:<10} {cnt}")
        else:
            print("\n📈 无关联加密货币")
