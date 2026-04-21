#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_kg_daily.py - 每日增量：财经新闻 → 三元组
从 akshare stock_news_em 抓取今日/昨日新闻，
用 LLM 抽取三元组，写入 stock_kg（relation: NEWS_EVENT）
"""

import os, sys, time, json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from stock_kg_builder import get_db, stats

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')

import akshare as ak
import requests
from pymongo import UpdateOne

RATE_LIMITER_INTERVAL = 0.2  # 5 calls/s

def llm_extract_triples(news_list):
    """
    用共享 skill 抽取财经新闻三元组
    输入: news_list = [{title, content, source, time}]
    输出: [{subject, relation, object, evidence}]
    """
    sys.path.insert(0, '/home/node/.openclaw/skills/llm-triple-extractor')
    from extractor import extract_triples

    news_text = "\n".join([
        f"- [{n['source']}] {n['time']}: {n['title']}"
        for n in news_list
    ])

    try:
        triples = extract_triples(
            text=news_text,
            domain='stock_news',
            max_triples=20,
        )
        return triples
    except Exception as e:
        print(f"  ⚠️ LLM抽取失败: {e}")
    return []


def fetch_recent_news(days=3):
    """抓取近days天的财经新闻"""
    print(f"📡 抓取近{days}天财经新闻...")
    all_news = []

    for day_offset in range(days):
        date = (datetime.now() - timedelta(days=day_offset)).strftime('%Y%m%d')
        try:
            df = ak.stock_news_em()
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                title = str(row.get('新闻标题', ''))
                src = str(row.get('文章来源', ''))
                pub_time = str(row.get('发布时间', ''))
                if not title or title == 'nan':
                    continue
                all_news.append({
                    'title': title,
                    'source': src,
                    'time': pub_time,
                    'date': date,
                })
            time.sleep(RATE_LIMITER_INTERVAL)
        except Exception as e:
            print(f"  ⚠️ {date} 抓取失败: {e}")

    print(f"  共获取 {len(all_news)} 条新闻")
    return all_news


def fetch_em_news(days=3):
    """用东财API补充财经新闻"""
    all_news = []
    for day_offset in range(days):
        date_str = (datetime.now() - timedelta(days=day_offset)).strftime('%Y%m%d')
        try:
            url = f"https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=20&page_index=1&ann_type=SHA,CYB,SZA&begin_date={date_str}&end_date={date_str}"
            r = requests.get(url, timeout=5)
            data = r.json().get('data', {})
            items = data.get('list', [])
            for item in items:
                codes = item.get('codes', [])
                for c in codes:
                    code = c.get('inner_code', '')
                    name = c.get('code_name', '')
                all_news.append({
                    'title': item.get('title', ''),
                    'source': '东财公告',
                    'time': item.get('notice_date', ''),
                    'date': date_str,
                })
            time.sleep(RATE_LIMITER_INTERVAL)
        except Exception:
            pass
    return all_news


def upsert_news_triples(news_list):
    """用LLM抽取并写入stock_kg"""
    if not news_list:
        print("  无新闻，跳过")
        return 0

    print(f"  调用LLM抽取三元组...")
    triples = llm_extract_triples(news_list)
    print(f"  LLM抽取到 {len(triples)} 个三元组")

    if not triples:
        return 0

    db = get_db()
    ops = []
    for t in triples:
        if not all(k in t for k in ('subject', 'relation', 'object')):
            continue
        subject = t['subject']
        relation = t['relation']
        obj = t['object']
        evidence = t.get('evidence', '')

        ops.append(UpdateOne(
            {
                '_id': f'{subject}_{relation}_{obj}_{datetime.now().strftime("%Y%m%d%H%M")}',
                'subject': subject,
                'relation': relation,
                'object': obj,
            },
            {'$set': {
                'subject': subject,
                'subject_name': subject,
                'relation': relation,
                'object': obj,
                'evidence': evidence,
                'source': 'daily_news',
                'news_date': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat(),
            }},
            upsert=True
        ))

    if ops:
        db['stock_kg'].bulk_write(ops, ordered=False)

    return len(triples)


def run():
    print(f"\n{'='*50}")
    print(f"📰 每日股票KG增量（新闻→三元组）  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    # 1. 抓新闻
    news = fetch_recent_news(days=3)
    em_news = fetch_em_news(days=3)
    all_news = news + em_news

    # 2. 去重
    seen = set()
    unique_news = []
    for n in all_news:
        key = n['title'][:30]
        if key not in seen:
            seen.add(key)
            unique_news.append(n)

    print(f"\n📰 去重后 {len(unique_news)} 条新闻待处理")

    # 3. LLM抽取 + 写入
    count = upsert_news_triples(unique_news)

    # 4. 统计
    db = get_db()
    total = db['stock_kg'].estimated_document_count()
    news_triples = db['stock_kg'].count_documents({'source': 'daily_news'})

    print(f"\n{'='*50}")
    print(f"✅ 完成！")
    print(f"   本次新增: {count} 条")
    print(f"   stock_kg总数: {total}")
    print(f"   其中新闻三元组: {news_triples}")
    print(f"{'='*50}")


if __name__ == '__main__':
    run()
