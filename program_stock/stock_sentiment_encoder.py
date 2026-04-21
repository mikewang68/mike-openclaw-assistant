#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_sentiment_encoder.py - 舆情向量编码器
两个数据源：A股新闻 + 加密货币新闻
A股: Ollama nomic-embed-text (768维) + MiniMax Chat结构化情感(32维)
加密货币: Ollama embedding (768维)
入库 MongoDB: stock_sentiment
向量文件: data/sentiment_vectors.npy / sentiment_index.faiss
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
EMBED_DIM = 768  # Ollama nomic-embed-text output

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

def get_ollama_embedding(texts: list[str]) -> list[list[float]]:
    """
    调用 Ollama nomic-embed-text 获取768维embedding
    texts: 文本列表（batch）
    返回: list of embedding vectors
    """
    payload = {'model': OLLAMA_MODEL, 'input': texts}
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('embeddings', [])
    except Exception as e:
        print(f'  ⚠️ Ollama error: {e}')
    return None

def encode_news_batch(news_items: list) -> list:
    """
    对舆情列表生成embedding
    每条: {code, title, content, pub_time, source, ...}
    返回: [{news_id, embedding, sentiment_struct}, ...]
    """
    results = []
    BATCH = 8  # Ollama batch size
    
    for i in range(0, len(news_items), BATCH):
        batch = news_items[i:i+BATCH]
        # 组合标题+内容作为embedding输入
        texts = [f"{n.get('title','')}。{n.get('content','')}"[:512] for n in batch]
        
        vecs = get_ollama_embedding(texts)
        if vecs is None:
            # fallback: 768维零向量
            vecs = [[0.0] * EMBED_DIM] * len(batch)
        
        for n, vec in zip(batch, vecs):
            if len(vec) == EMBED_DIM:
                # L2归一化（余弦相似度等价内积）
                v = np.array(vec, dtype=np.float32)
                n2 = np.linalg.norm(v)
                if n2 > 1e-9:
                    v = v / n2
                results.append({'news': n, 'embedding': v.tolist()})
            else:
                # 填充或截断到768维
                padded = (vec + [0.0] * EMBED_DIM)[:EMBED_DIM]
                results.append({'news': n, 'embedding': padded})
        
        print(f"  Embedded {min(i+BATCH, len(news_items))}/{len(news_items)}")
        time.sleep(0.3)
    
    return results

def build_sentiment_index(days: int = 7):
    """
    对最近days天的舆情建立FAISS索引
    索引结构：每只股票(code) = 该股所有舆情embedding的平均，归一化
    """
    db = get_db()
    
    # 读取近days天未编码的舆情
    cutoff_ts = (datetime.now().timestamp() - days * 86400)
    cutoff_dt = datetime.fromtimestamp(cutoff_ts).strftime('%Y-%m-%d')
    
    news_list = list(db['stock_sentiment'].find(
        {'$or': [
            {'vector_encoded': {'$ne': True}},
            {'vector_encoded': {'$exists': False}}
        ]},
        limit=200
    ))
    
    if not news_list:
        print("  没有待编码的舆情")
        return 0
    
    print(f"  待编码: {len(news_list)} 条")
    encoded = encode_news_batch(news_list)
    
    # 写入MongoDB + 标记
    for item in encoded:
        news_id = item['news']['_id']
        emb = item['embedding']
        db['stock_sentiment'].update_one(
            {'_id': news_id},
            {'$set': {
                'embedding': emb,
                'vector_encoded': True,
                'encoded_at': datetime.now().isoformat()
            }}
        )
    
    print(f"  已写入 {len(encoded)} 条embedding")
    return len(encoded)

def build_faiss_index():
    """
    用已编码的舆情向量构建FAISS索引
    Python内聚合：code -> avg(embeddings)
    """
    db = get_db()
    
    # 读取所有有embedding的文档
    docs = list(db['stock_sentiment'].find({'embedding': {'$exists': True}}))
    if not docs:
        print("⚠️ 没有已编码的舆情向量")
        return None, None
    
    # Python侧按code聚合
    code_to_embs = {}
    code_to_time = {}
    for d in docs:
        emb = d.get('embedding')
        if not emb or len(emb) != EMBED_DIM:
            continue
        codes = d.get('codes', []) or []
        pub_time = d.get('pub_time', '')
        for code in codes:
            if not code:
                continue
            if code not in code_to_embs:
                code_to_embs[code] = []
                code_to_time[code] = pub_time
            code_to_embs[code].append(emb)
    
    # 求平均
    vectors = []
    meta = []
    for code, embs in code_to_embs.items():
        if len(embs) < 1:
            continue
        avg = np.mean(embs, axis=0).astype(np.float32)
        n2 = np.linalg.norm(avg)
        if n2 > 1e-9:
            avg = avg / n2
        vectors.append(avg)
        meta.append({'code': code, 'news_count': len(embs), 'latest': code_to_time[code]})
    
    if not vectors:
        print("⚠️ 没有有效向量")
        return None, None
    
    vectors = np.vstack(vectors)
    
    # FAISS IndexFlatIP = 内积（归一化后=余弦相似度）
    index = faiss.IndexFlatIP(EMBED_DIM)
    index.add(vectors)
    
    DATA_DIR = Path(__file__).parent / 'data'
    DATA_DIR.mkdir(exist_ok=True)
    np.save(DATA_DIR / 'sentiment_vectors.npy', vectors)
    faiss.write_index(index, str(DATA_DIR / 'sentiment_index.faiss'))
    with open(DATA_DIR / 'sentiment_meta.json', 'w') as f:
        json.dump(meta, f)
    
    print(f"✅ 舆情索引: {vectors.shape[0]} 只(code), dim={EMBED_DIM}")
    return index, meta

def search_sentiment(query_code: str, top_k: int = 10):
    """找与query_code舆情最相似的其他code"""
    DATA_DIR = Path(__file__).parent / 'data'
    
    index = faiss.read_index(str(DATA_DIR / 'sentiment_index.faiss'))
    with open(DATA_DIR / 'sentiment_meta.json') as f:
        meta = json.load(f)
    
    code_to_idx = {m['code']: i for i, m in enumerate(meta)}
    if query_code not in code_to_idx:
        print(f"⚠️ {query_code} 没有舆情记录")
        return []
    
    idx = code_to_idx[query_code]
    qv = index.reconstruct(idx).reshape(1, -1)
    D, I = index.search(qv, top_k + 1)
    
    results = []
    for sim, i in zip(D[0], I[0]):
        if i < 0 or i >= len(meta):
            continue
        if meta[i]['code'] == query_code:
            continue
        results.append((meta[i]['code'], float(sim), meta[i]['news_count']))
    
    return results[:top_k]

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--encode', action='store_true', help='编码舆情向量(Ollama)')
    parser.add_argument('--build', action='store_true', help='构建FAISS索引')
    parser.add_argument('--search', type=str, help='查询舆情相似股')
    parser.add_argument('--topk', type=int, default=10)
    args = parser.parse_args()
    
    if args.encode:
        n = build_sentiment_index()
        print(f"编码完成: {n} 条")
    
    if args.build:
        build_faiss_index()
    
    if args.search:
        results = search_sentiment(args.search, top_k=args.topk)
        print(f"\n📰 {args.search} 舆情最相似的:")
        for code, sim, cnt in results:
            print(f"  {code}: similarity={sim:.4f} ({cnt}条舆情)")
    
    if not args.encode and not args.build and not args.search:
        # 测试embedding
        test = get_ollama_embedding(['hello world'])
        print(f"Ollama test: {len(test[0]) if test else 'FAILED'} dim")
