#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_sentiment_encoder.py - 舆情向量编码器
用 MiniMax Chat API 提取情感/概念特征向量
入库 MongoDB: stock_sentiment_vectors
"""

import os, sys, json, time
import pymongo
import requests
from datetime import datetime
from pathlib import Path

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')
API_KEY = 'sk-cp-CBUQm3M8PXAsAa9zgaNI_zvnsFtXgirPGgOmBF1cYM6fwykMG01aGC-bcouLyWA-SrHtn-Wt87FmqHcRi4NN_it72uqBGEo1grkgyVCYzbqyCgiUUO-wXzw'
API_URL = 'https://api.minimaxi.com/v1/text/chatcompletion_v2'
MODEL = 'MiniMax-M2.7'

SENTIMENT_DIM = 32  # 情感/概念特征维度

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

def call_minimax(prompt, max_tokens=200):
    """调用 MiniMax Chat API"""
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {
        'model': MODEL,
        'max_tokens': max_tokens,
        'messages': [{'role': 'user', 'content': prompt}]
    }
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data['choices'][0]['message']['content']
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None

def extract_sentiment_features(news_item):
    """
    用 LLM 提取单条舆情的情感/概念特征向量（32维）
    返回: list[float] 或 None
    """
    title = news_item.get('title', '')[:200]
    content = news_item.get('content', '')[:500]
    
    prompt = f"""你是一个A股舆情分析师。请分析以下新闻，输出一个32维情感/概念特征向量。

要求：输出32个浮点数，范围[0,1]，用逗号分隔，顺序如下：
0.整体情感分数（0=负面，0.5=中性，1=正面）
1.利好强度（0~1）
2.利空强度（0~1）
3.政策相关度（0~1）
4.科技相关度（0~1）
5.新能源相关度（0~1）
6.消费相关度（0~1）
7.金融相关度（0~1）
8.医药相关度（0~1）
9.地产相关度（0~1）
10.教育相关度（0~1）
11.互联网相关度（0~1）
12.半导体相关度（0~1）
13.军工相关度（0~1）
14.业绩相关度（0~1）
15.营收增长相关（0~1）
16.净利润增长相关（0~1）
17.订单/合同相关（0~1）
18.研发突破相关（0~1）
19.市场份额相关（0~1）
20.出口相关（0~1）
21.内循环相关（0~1）
22.高管变动相关（0~1）
23.股权变动相关（0~1）
24.诉讼风险相关（0~1）
25.监管风险相关（0~1）
26.舆情热度估算（0~1，0=冷门，1=热门）
27.传播潜力（0~1）
28.机构关注度（0~1）
29.散户情绪带动（0~1）
30.短期影响（0~1）
31.长期影响（0~1）

新闻标题：{title}
新闻内容：{content}

输出格式：只输出32个数字，用逗号分隔，不要任何解释。
"""

    result = call_minimax(prompt, max_tokens=200)
    if not result:
        return None
    
    try:
        # 提取数字列表
        import re
        numbers = re.findall(r'0(?:\.\d+)?|1(?:\.0+)?', result)
        numbers = [float(n) for n in numbers[:32]]
        if len(numbers) >= 32:
            return numbers[:32]
    except:
        pass
    return None

def encode_batch(news_items):
    """
    批量编码舆情，返回 [(news_id, vector), ...]
    """
    results = []
    for i, news in enumerate(news_items):
        vec = extract_sentiment_features(news)
        if vec:
            results.append((news['_id'], vec))
        else:
            # fallback：全0向量
            results.append((news['_id'], [0.5] * SENTIMENT_DIM))
        
        if (i + 1) % 5 == 0:
            print(f"  已处理 {i+1}/{len(news_items)} 条")
        time.sleep(0.3)  # 避免API超速
    
    return results

def build_sentiment_index(days=7):
    """
    对最近days天的舆情建FAISS索引
    """
    db = get_db()
    cutoff = (datetime.now().timestamp() - days * 86400)
    
    # 读取未编码的舆情
    news_list = list(db['stock_sentiment'].find(
        {'vector_encoded': {'$ne': True}},
        limit=500  # 每次最多500条
    ))
    
    if not news_list:
        print("  没有待编码的舆情")
        return 0
    
    print(f"  待编码: {len(news_list)} 条")
    
    # 批量编码
    encoded = encode_batch(news_list)
    
    # 写入MongoDB
    for news_id, vec in encoded:
        db['stock_sentiment'].update_one(
            {'_id': news_id},
            {'$set': {'vector': vec, 'vector_encoded': True, 'encoded_at': datetime.now().isoformat()}}
        )
    
    print(f"✅ 已编码 {len(encoded)} 条舆情")
    return len(encoded)

def build_faiss_index():
    """
    用已编码的舆情向量构建FAISS索引
    按股票代码聚合：每只股票 = 平均舆情向量
    """
    db = get_db()
    
    # 聚合同一只股票的舆情向量
    pipeline = [
        {'$match': {'vector': {'$exists': True}}},
        {'$group': {
            '_id': '$code',
            'avg_vector': {'$avg': '$vector'},
            'count': {'$sum': 1},
            'latest_time': {'$max': '$pub_time'}
        }},
        {'$match': {'_id': {'$ne': None}, 'count': {'$gte': 1}}}
    ]
    
    rows = list(db['stock_sentiment'].aggregate(pipeline))
    if not rows:
        print("⚠️ 没有已编码的舆情向量")
        return None, None
    
    vectors = []
    meta = []
    for r in rows:
        if r['avg_vector'] and len(r['avg_vector']) == SENTIMENT_DIM:
            vec = np.array(r['avg_vector'], dtype=np.float32)
            # L2归一化
            norm = np.linalg.norm(vec)
            if norm > 1e-9:
                vec = vec / norm
            vectors.append(vec)
            meta.append({'code': r['_id'], 'news_count': r['count'], 'latest': r['latest_time']})
    
    if not vectors:
        return None, None
    
    import numpy as np
    vectors = np.vstack(vectors)
    
    # FAISS index
    import faiss
    dim = vectors.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vectors)
    
    # 保存
    DATA_DIR = Path(__file__).parent / 'data'
    DATA_DIR.mkdir(exist_ok=True)
    np.save(DATA_DIR / 'sentiment_vectors.npy', vectors)
    faiss.write_index(index, str(DATA_DIR / 'sentiment_index.faiss'))
    with open(DATA_DIR / 'sentiment_meta.json', 'w') as f:
        json.dump(meta, f)
    
    print(f"✅ 舆情FAISS索引: {vectors.shape[0]} 只股票, dim={dim}")
    return index, meta

def search_sentiment(query_code, top_k=10):
    """查找与指定股票舆情最相似的其他股票"""
    import numpy as np
    import faiss
    DATA_DIR = Path(__file__).parent / 'data'
    
    index = faiss.read_index(str(DATA_DIR / 'sentiment_index.faiss'))
    with open(DATA_DIR / 'sentiment_meta.json') as f:
        meta = json.load(f)
    
    code_to_idx = {m['code']: i for i, m in enumerate(meta)}
    if query_code not in code_to_idx:
        print(f"⚠️ {query_code} 没有舆情记录")
        return []
    
    idx = code_to_idx[query_code]
    query_vec = index.reconstruct(idx).reshape(1, -1)
    D, I = index.search(query_vec, top_k + 1)
    
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
    parser.add_argument('--encode', action='store_true', help='编码舆情向量')
    parser.add_argument('--build', action='store_true', help='构建FAISS索引')
    parser.add_argument('--search', type=str, help='查询舆情相似股')
    args = parser.parse_args()
    
    if args.encode:
        n = build_sentiment_index()
        print(f"编码完成: {n} 条")
    
    if args.build:
        build_faiss_index()
    
    if args.search:
        results = search_sentiment(args.search)
        print(f"\n📰 {args.search} 舆情最相似的股票:")
        for code, sim, cnt in results:
            print(f"  {code}: similarity={sim:.4f} ({cnt}条舆情)")
