#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_kg_builder.py - 股票知识图谱构建
从现有 stock.code / stock.fin_forecast 提取三元组，存入 stock 数据库
不和论文知识图谱混用，collection 名：stock_kg / stock_events / stock_concepts
"""

import sys
import os
import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pymongo
from pymongo import UpdateOne, InsertOne

MONGO_URI = os.environ.get(
    'MONGO_URI',
    'mongodb://stock:681123@192.168.1.2:27017/admin'
)

counter_lock = Lock()
stats = {'concepts': 0, 'industries': 0, 'ratings': 0, 'errors': 0}

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

def get_codes():
    db = get_db()
    return list(db['code'].find(
        {'name': {'$exists': 1}},
        {'code': 1, 'name': 1, 'industry': 1, 'conception': 1}
    ))

# ─── 1. 概念三元组：股票 → HAS_CONCEPT ──────────────────────

def build_concept_triples(codes):
    """从 code.conception 提取 HAS_CONCEPT 三元组"""
    db = get_db()
    ops = []
    concept_count = {}  # 统计每个概念的热度

    for doc in codes:
        code = doc['code']
        name = doc['name']
        conceptions = doc.get('conception') or []

        for conc in conceptions:
            conc = conc.strip()
            if not conc:
                continue

            # 三元组
            ops.append(UpdateOne(
                {
                    '_id': f'{code}_HAS_CONCEPT_{conc}',
                    'subject': code,
                    'subject_name': name,
                    'relation': 'HAS_CONCEPT',
                    'object': conc,
                },
                {'$set': {
                    'subject': code,
                    'subject_name': name,
                    'relation': 'HAS_CONCEPT',
                    'object': conc,
                    'updated_at': datetime.now().isoformat(),
                }},
                upsert=True
            ))

            # 概念热度统计
            concept_count[conc] = concept_count.get(conc, 0) + 1

    if ops:
        db['stock_kg'].bulk_write(ops, ordered=False)

    # 更新概念热度
    concept_ops = []
    for conc, cnt in concept_count.items():
        concept_ops.append(UpdateOne(
            {'_id': conc},
            {'$set': {
                'name': conc,
                'stock_count': cnt,
                'type': 'concept',
                'updated_at': datetime.now().isoformat(),
            }},
            upsert=True
        ))
    if concept_ops:
        db['stock_concepts'].bulk_write(concept_ops, ordered=False)

    with counter_lock:
        stats['concepts'] += len(ops)
    return len(ops)


# ─── 2. 行业三元组：股票 → IN_INDUSTRY ──────────────────────

def build_industry_triples(codes):
    """从 code.industry 提取 IN_INDUSTRY 三元组"""
    db = get_db()
    ops = []

    for doc in codes:
        code = doc['code']
        name = doc['name']
        industry = doc.get('industry', '').strip()

        if not industry or industry in ('待分类', ''):
            continue

        ops.append(UpdateOne(
            {
                '_id': f'{code}_IN_INDUSTRY_{industry}',
                'subject': code,
                'subject_name': name,
                'relation': 'IN_INDUSTRY',
                'object': industry,
            },
            {'$set': {
                'subject': code,
                'subject_name': name,
                'relation': 'IN_INDUSTRY',
                'object': industry,
                'updated_at': datetime.now().isoformat(),
            }},
            upsert=True
        ))

    if ops:
        db['stock_kg'].bulk_write(ops, ordered=False)

    with counter_lock:
        stats['industries'] += len(ops)
    return len(ops)


# ─── 3. 评级三元组：机构评级预测 ────────────────────────────

def build_rating_triples():
    """从 fin_forecast 提取 RATED_BY 三元组"""
    db = get_db()
    ops = []

    for doc in db['fin_forecast'].find():
        code = doc.get('股票代码', '')
        name = doc.get('股票简称', '')

        buy = doc.get('机构投资评级(近六个月)-买入', 0)
        add = doc.get('机构投资评级(近六个月)-增持', 0)
        neutral = doc.get('机构投资评级(近六个月)-中性', 0)

        # 综合评级
        if buy + add > 3:
            rating = '强烈推荐'
        elif buy + add > 0:
            rating = '推荐'
        elif neutral > 0:
            rating = '中性'
        else:
            rating = None

        if not rating:
            continue

        eps_2025 = doc.get('2025预测每股收益')
        eps_2026 = doc.get('2026预测每股收益')

        ops.append(UpdateOne(
            {
                '_id': f'{code}_RATED_BY_{datetime.now().strftime("%Y%m")}',
                'subject': code,
                'subject_name': name,
                'relation': 'RATED_BY',
                'object': rating,
            },
            {'$set': {
                'subject': code,
                'subject_name': name,
                'relation': 'RATED_BY',
                'object': rating,
                'rating_buy': float(buy),
                'rating_add': float(add),
                'rating_neutral': float(neutral),
                'eps_2025': float(eps_2025) if eps_2025 else None,
                'eps_2026': float(eps_2026) if eps_2026 else None,
                'source': 'fin_forecast',
                'updated_at': datetime.now().isoformat(),
            }},
            upsert=True
        ))

    if ops:
        db['stock_kg'].bulk_write(ops, ordered=False)

    with counter_lock:
        stats['ratings'] += len(ops)
    return len(ops)


# ─── 主流程 ─────────────────────────────────────────────────

def build_all(max_workers=5):
    print(f"\n{'='*50}")
    print(f"📊 股票知识图谱构建  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    start = time.time()

    # 一次性加载所有股票数据
    print("📡 加载股票名单...")
    codes = get_codes()
    print(f"  共 {len(codes)} 只股票")

    # 1. 概念三元组（可并行）
    print("\n🔄 提取 HAS_CONCEPT 三元组...")
    t1 = time.time()
    n1 = build_concept_triples(codes)
    print(f"  ✅ {n1} 条概念三元组 ({time.time()-t1:.1f}s)")

    # 2. 行业三元组
    print("\n🔄 提取 IN_INDUSTRY 三元组...")
    t2 = time.time()
    n2 = build_industry_triples(codes)
    print(f"  ✅ {n2} 条行业三元组 ({time.time()-t2:.1f}s)")

    # 3. 评级三元组
    print("\n🔄 提取 RATED_BY 三元组...")
    t3 = time.time()
    n3 = build_rating_triples()
    print(f"  ✅ {n3} 条评级三元组 ({time.time()-t3:.1f}s)")

    elapsed = time.time() - start

    # 统计 stock_kg 总数
    db = get_db()
    total_kg = db['stock_kg'].estimated_document_count()
    total_concepts = db['stock_concepts'].estimated_document_count()

    print(f"\n{'='*50}")
    print(f"✅ 构建完成！总耗时: {elapsed:.1f}s")
    print(f"   stock_kg:     {total_kg} 条三元组")
    print(f"   stock_concepts: {total_concepts} 个概念实体")
    print(f"   概念三元组:   +{stats['concepts']} 条")
    print(f"   行业三元组:   +{stats['industries']} 条")
    print(f"   评级三元组:   +{stats['ratings']} 条")
    print(f"{'='*50}")


if __name__ == '__main__':
    build_all()
