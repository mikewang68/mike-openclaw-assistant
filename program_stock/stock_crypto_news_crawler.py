#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_crypto_news_crawler.py - 加密货币新闻爬取
数据源：CryptoSlate RSS (https://cryptoslate.com/feed/)
入库 MongoDB: stock_crypto_news
"""

import os, sys, re, hashlib
import requests
import xml.etree.ElementTree as ET
import pymongo
from datetime import datetime

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')
RSS_URL = 'https://cryptoslate.com/feed/'

# 主流加密货币代码（用于匹配新闻关联）
CRYPTO_CODES = {
    'BTC': 'BTCUSDT', 'Bitcoin': 'BTCUSDT',
    'ETH': 'ETHUSDT', 'Ethereum': 'ETHUSDT',
    'BNB': 'BNBUSDT', 'Binance': 'BNBUSDT',
    'SOL': 'SOLUSDT', 'Solana': 'SOLUSDT',
    'XRP': 'XRPUSDT', 'Ripple': 'XRPUSDT',
    'ADA': 'ADAUSDT', 'Cardano': 'ADAUSDT',
    'DOGE': 'DOGEUSDT', 'Dogecoin': 'DOGEUSDT',
    'DOT': 'DOTUSDT', 'Polkadot': 'DOTUSDT',
    'AVAX': 'AVAXUSDT', 'Avalanche': 'AVAXUSDT',
    'LINK': 'LINKUSDT', 'Chainlink': 'LINKUSDT',
    'MATIC': 'MATICUSDT', 'Polygon': 'MATICUSDT',
    'UNI': 'UNIUSDT', 'Uniswap': 'UNIUSDT',
    'LTC': 'LTCUSDT', 'Litecoin': 'LTCUSDT',
    'ATOM': 'ATOMUSDT', 'Cosmos': 'ATOMUSDT',
    'XLM': 'XLMUSDT', 'Stellar': 'XLMUSDT',
    'NEAR': 'NEARUSDT', 'NEAR Protocol': 'NEARUSDT',
    'APT': 'APTUSDT', 'Aptos': 'APTUSDT',
    'ARB': 'ARBUSDT', 'Arbitrum': 'ARBUSDT',
    'OP': 'OPUSDT', 'Optimism': 'OPUSDT',
    'INJ': 'INJUSDT', 'Injective': 'INJUSDT',
    'SUI': 'SUIUSDT', 'Sui': 'SUIUSDT',
    'SEI': 'SEIUSDT', 'Sei': 'SEIUSDT',
    'TIA': 'TIAUSDT', 'Celestia': 'TIAUSDT',
    'RENDER': 'RENDERUSDT', 'Render': 'RENDERUSDT',
    'FET': 'FETUSDT', 'Fetch.ai': 'FETUSDT',
    'GRT': 'GRTUSDT', 'The Graph': 'GRTUSDT',
    'AAVE': 'AAVEUSDT', 'Aave': 'AAVEUSDT',
    'MKR': 'MKRUSDT', 'Maker': 'MKRUSDT',
}

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

def hash_text(text):
    return hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()

def extract_coins(text):
    """从文本中提取关联的加密货币代码"""
    found = set()
    text_upper = text.upper()
    for name, code in CRYPTO_CODES.items():
        if name.upper() in text_upper:
            found.add(code)
    return list(found)

def strip_html(text):
    """去除HTML标签"""
    return re.sub(r'<[^>]+>', '', text)

def fetch_cryptoslate_news():
    """抓取 CryptoSlate RSS 新闻"""
    results = []
    try:
        r = requests.get(RSS_URL, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        if r.status_code != 200:
            print(f'  CryptoSlate HTTP {r.status_code}')
            return results
        
        root = ET.fromstring(r.text)
        channel = root.find('channel')
        if not channel:
            return results
        
        items = channel.findall('item')
        print(f'  CryptoSlate: {len(items)} articles')
        
        for item in items:
            try:
                title = strip_html(item.findtext('title', '')).strip()
                link = item.findtext('link', '').strip()
                pub_str = item.findtext('pubDate', '')
                desc_raw = item.findtext('description', '')
                desc = strip_html(desc_raw or '').strip()[:500]
                
                if not title or not link:
                    continue
                
                # 解析时间
                pub_time = ''
                if pub_str:
                    try:
                        dt = datetime.strptime(pub_str[:25], '%a, %d %b %Y %H:%M:%S')
                        pub_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                    except:
                        pub_time = pub_str[:19]
                
                # 提取关联币种
                combined = f'{title} {desc}'
                coins = extract_coins(combined)
                
                results.append({
                    'title': title,
                    'link': link,
                    'content': desc,
                    'pub_time': pub_time,
                    'source': 'cryptoslate',
                    'coins': coins,
                    'content_hash': hash_text(title + desc),
                    'crawl_date': datetime.now().strftime('%Y-%m-%d'),
                })
            except Exception as e:
                continue
    except Exception as e:
        print(f'  ⚠️ CryptoSlate fetch error: {e}')
    return results

def save_news(news_list):
    """去重写入MongoDB"""
    if not news_list:
        print('  无新数据')
        return 0
    db = get_db()
    existing = set(
        r['content_hash'] for r in 
        db['stock_crypto_news'].find({}, {'content_hash': 1})
    )
    new_docs = [n for n in news_list if n['content_hash'] not in existing]
    if new_docs:
        db['stock_crypto_news'].insert_many(new_docs)
    print(f'  爬取{len(news_list)}条，去重新增{len(new_docs)}条')
    return len(new_docs)

def crawl_today():
    print('📡 爬取加密货币新闻 (CryptoSlate)...')
    news = fetch_cryptoslate_news()
    saved = save_news(news)
    print(f'✅ 完成: 新增{saved}条')
    return saved

if __name__ == '__main__':
    crawl_today()
