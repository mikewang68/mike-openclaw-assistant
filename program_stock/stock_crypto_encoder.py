#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_crypto_encoder.py - 加密货币新闻向量编码
Ollama nomic-embed-text (768维)
入库 MongoDB: stock_crypto_news (embedding字段)
FAISS索引: data/crypto_news_index.faiss
"""

import os, sys, json, time
import numpy as np
import pymongo
import faiss
import requests
from datetime import datetime
from pathlib import Path

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')
OLLAMA_URL = 'http://192.168.1.2:11434/api/embed'
OLLAMA_MODEL = 'nomic-embed-text:latest'
EMBED_DIM = 768

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

def get_ollama_embedding(texts):
    payload = {'model': OLLAMA_MODEL, 'input': texts}
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
        if resp.status_code == 200:
            return resp.json().get('embeddings', [])
    except Exception as e:
        print(f'  ⚠️ Ollama error: {e}')
    return None

def encode_batch(news_items):
    """批量编码新闻向量"""
    texts = [f"{n.get('title','')}。{n.get('content','')}"[:512] for n in news_items]
    BATCH = 8
    results = []
    for i in range(0, len(texts), BATCH):
        batch_texts = texts[i:i+BATCH]
        batch_news = news_items[i:i+BATCH]
        vecs = get_ollama_embedding(batch_texts)
        if vecs is None:
            vecs = [[0.0] * EMBED_DIM] * len(batch_texts)
        for n, vec in zip(batch_news, vecs):
            if len(vec) != EMBED_DIM:
                vec = (vec + [0.0] * EMBED_DIM)[:EMBED_DIM]
            v = np.array(vec, dtype=np.float32)
            n2 = np.linalg.norm(v)
            if n2 > 1e-9:
                v = v / n2
            results.append({'news': n, 'embedding': v.tolist()})
        print(f"  Embedded {min(i+BATCH, len(texts))}/{len(texts)}")
        time.sleep(0.3)
    return results

def build_crypto_news_index(days=7):
    """对近days天的加密货币新闻编码并建FAISS索引"""
    db = get_db()
    cutoff = (datetime.now().timestamp() - days * 86400)
    
    news_list = list(db['stock_crypto_news'].find(
        {'$or': [
            {'embedding': {'$exists': False}},
            {'embedding': {'$exists': True}, 'embedding.0': {'$exists': False}}
        ]},
        limit=200
    ))
    # Also get some already-encoded
    already = list(db['stock_crypto_news'].find({'embedding': {'$exists': True}}).limit(500))
    
    # Filter to recent only
    cutoff_dt = datetime.fromtimestamp(cutoff)
    news_list = [n for n in news_list if n.get('pub_time', '') and 
                 datetime.strptime(n['pub_time'][:10], '%Y-%m-%d') >= cutoff_dt]
    
    if not news_list:
        print(f'  没有待编码的加密货币新闻')
        return 0
    
    print(f'  待编码: {len(news_list)} 条')
    encoded = encode_batch(news_list)
    
    for item in encoded:
        db['stock_crypto_news'].update_one(
            {'_id': item['news']['_id']},
            {'$set': {
                'embedding': item['embedding'],
                'encoded_at': datetime.now().isoformat()
            }}
        )
    
    print(f'  已写入 {len(encoded)} 条embedding')
    return len(encoded)

def build_faiss_index():
    """构建FAISS索引（按币种聚合）"""
    db = get_db()
    docs = list(db['stock_crypto_news'].find({'embedding': {'$exists': True}}))
    if not docs:
        print('⚠️ 没有已编码的新闻')
        return None, None
    
    # 按 coin 聚合
    coin_to_embs = {}
    coin_to_time = {}
    for d in docs:
        emb = d.get('embedding')
        if not emb or len(emb) != EMBED_DIM:
            continue
        coins = d.get('coins', []) or []
        for coin in coins:
            if not coin:
                continue
            if coin not in coin_to_embs:
                coin_to_embs[coin] = []
                coin_to_time[coin] = d.get('pub_time', '')
            coin_to_embs[coin].append(emb)
    
    # 也处理无明确币种的新闻（归入"综合"）
    for d in docs:
        emb = d.get('embedding')
        if not emb or len(emb) != EMBED_DIM:
            continue
        coins = d.get('coins', []) or []
        if not coins:
            if 'GLOBAL' not in coin_to_embs:
                coin_to_embs['GLOBAL'] = []
                coin_to_time['GLOBAL'] = d.get('pub_time', '')
            coin_to_embs['GLOBAL'].append(emb)
    
    vectors = []
    meta = []
    for coin, embs in coin_to_embs.items():
        if len(embs) < 1:
            continue
        avg = np.mean(embs, axis=0).astype(np.float32)
        n2 = np.linalg.norm(avg)
        if n2 > 1e-9:
            avg = avg / n2
        vectors.append(avg)
        meta.append({'coin': coin, 'news_count': len(embs), 'latest': coin_to_time[coin]})
    
    if not vectors:
        print('⚠️ 没有有效向量')
        return None, None
    
    vectors = np.vstack(vectors)
    index = faiss.IndexFlatIP(EMBED_DIM)
    index.add(vectors)
    
    DATA_DIR = Path(__file__).parent / 'data'
    DATA_DIR.mkdir(exist_ok=True)
    np.save(DATA_DIR / 'crypto_news_vectors.npy', vectors)
    faiss.write_index(index, str(DATA_DIR / 'crypto_news_index.faiss'))
    with open(DATA_DIR / 'crypto_news_meta.json', 'w') as f:
        json.dump(meta, f)
    
    print(f'✅ 加密货币新闻索引: {len(vectors)} coins, dim={EMBED_DIM}')
    return index, meta

def search_crypto_news(query_coin=None, top_k=10):
    """搜索与指定币种最相关的加密货币新闻"""
    DATA_DIR = Path(__file__).parent / 'data'
    idx_path = DATA_DIR / 'crypto_news_index.faiss'
    if not idx_path.exists():
        print('⚠️ 索引不存在')
        return []
    
    index = faiss.read_index(str(idx_path))
    with open(DATA_DIR / 'crypto_news_meta.json') as f:
        meta = json.load(f)
    
    coin_to_idx = {m['coin']: i for i, m in enumerate(meta)}
    
    if query_coin and query_coin in coin_to_idx:
        idx = coin_to_idx[query_coin]
        qv = index.reconstruct(idx).reshape(1, -1)
    elif 'GLOBAL' in coin_to_idx:
        idx = coin_to_idx['GLOBAL']
        qv = index.reconstruct(idx).reshape(1, -1)
    else:
        label = query_coin or 'GLOBAL'
        print(f'⚠️ {label} 没有索引')
        return []
    
    D, I = index.search(qv, top_k + 1)
    results = []
    for sim, i in zip(D[0], I[0]):
        if i < 0 or i >= len(meta):
            continue
        if meta[i]['coin'] == query_coin:
            continue
        results.append((meta[i]['coin'], float(sim), meta[i]['news_count']))
    return results[:top_k]

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--encode', action='store_true')
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--search', type=str, default=None)
    args = parser.parse_args()
    
    if args.encode:
        n = build_crypto_news_index()
        print(f'编码完成: {n}')
    
    if args.build:
        build_faiss_index()
    
    if args.search:
        results = search_crypto_news(args.search)
        print(f'\n📰 {args.search} 相关新闻:')
        for coin, sim, cnt in results:
            print(f'  {coin}: similarity={sim:.4f} ({cnt}篇)')
    
    if not args.encode and not args.build and not args.search:
        # Test
        n = build_crypto_news_index()
        if n > 0:
            build_faiss_index()
