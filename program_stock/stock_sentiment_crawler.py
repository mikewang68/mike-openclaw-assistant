#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_sentiment_crawler.py - 舆情数据爬取
每天收盘后运行，抓取东方财富/同花顺财经新闻
入库 MongoDB: stock_sentiment
"""

import os, sys, json, hashlib, re
from datetime import datetime, timedelta
import pymongo
import akshare

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

def hash_content(text):
    """对内容做SHA256去重"""
    return hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()

def extract_stock_codes(text):
    """从文本中提取6位股票代码"""
    codes = set()
    # 匹配 600xxx, 000xxx, 300xxx, 688xxx 等
    for m in re.findall(r'(?:^|[^0-9])([68]\d{5}|[0-4]\d{5})(?:[^0-9]|$)', text):
        if len(m) == 6:
            codes.add(m)
    return list(codes)

def crawl_eastmoney_news(date=None):
    """
    爬取东方财富财经新闻
    """
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')
    
    results = []
    try:
        df = akshare.stock_news_em()
        if df is None or df.empty:
            print("⚠️ stock_news_em() 返回空")
            return results
        
        cols = df.columns.tolist()
        # 列名映射
        col_map = {}
        for c in cols:
            cl = c.lower()
            if '标题' in c or 'title' in cl:
                col_map['title'] = c
            elif '内容' in c or 'summary' in cl or 'content' in cl:
                col_map['content'] = c
            elif '时间' in c or 'date' in cl or 'time' in cl:
                col_map['time'] = c
            elif '关键词' in c or 'tag' in cl or '来源' in c:
                col_map['tag'] = c
        
        print(f"  列名: {cols}")
        
        for _, row in df.iterrows():
            try:
                title = str(row.get(col_map.get('title', ''), '')).strip()
                content = str(row.get(col_map.get('content', ''), ''))[:500]
                pub_time = str(row.get(col_map.get('time', ''), date))
                tag = str(row.get(col_map.get('tag', ''), ''))
                
                if not title or len(title) < 5:
                    continue
                
                # 从标题/内容提取股票代码
                combined = title + content
                codes = extract_stock_codes(combined)
                
                results.append({
                    'codes': codes,
                    'title': title,
                    'content': content,
                    'pub_time': pub_time,
                    'source': 'eastmoney',
                    'tag': tag,
                    'content_hash': hash_content(title + content),
                    'crawl_date': date,
                })
            except Exception as e:
                continue
    except Exception as e:
        print(f"⚠️ 东方财富爬取失败: {e}")
    
    return results

def save_news(news_list):
    """去重写入MongoDB，返回新增数量"""
    if not news_list:
        print("  无数据")
        return 0
    
    db = get_db()
    existing = set(r['content_hash'] for r in db['stock_sentiment'].find({}, {'content_hash': 1}))
    
    new_docs = []
    for n in news_list:
        if n['content_hash'] not in existing:
            new_docs.append(n)
            existing.add(n['content_hash'])
    
    if new_docs:
        db['stock_sentiment'].insert_many(new_docs)
    
    print(f"  爬取{len(news_list)}条，去重新增{len(new_docs)}条")
    return len(new_docs)

def crawl_today():
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"\n📡 爬取 {today} 舆情...")
    news = crawl_eastmoney_news(today)
    saved = save_news(news)
    print(f"✅ 完成: 新增{saved}条")
    return saved

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, default=None)
    args = parser.parse_args()
    
    if args.date:
        news = crawl_eastmoney_news(args.date)
        save_news(news)
    else:
        crawl_today()
