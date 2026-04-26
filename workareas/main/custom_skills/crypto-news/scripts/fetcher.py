#!/usr/bin/env python3
"""
CryptoNewsFetcher — 加密货币舆情多源抓取

来源：
1. Tavily Search API
2. SearXNG（需自建实例）
3. Cointelegraph RSS
4. Decrypt RSS
5. TheBlock RSS
6. PANewsLab HTML

写入 MongoDB: crypto.crypto_news
"""

import os, sys, json, time, hashlib
import requests
from datetime import datetime
from typing import List, Dict, Optional
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

# ─── 配置 ────────────────────────────────────────────────
MONGO_URI = os.environ.get(
    'MONGO_URI',
    'mongodb://stock:681123@192.168.1.2:27017/admin'
)
TAVILY_API_KEY = os.environ.get('TAVILY_API_KEY', '')
SEARXNG_URL = os.environ.get('SEARXNG_URL', 'http://192.168.1.2:38080')  # NAS SearXNG
DB_NAME = "crypto"
COLLECTION = "crypto_news"
MAX_PER_SOURCE = 20

# ─── MongoDB ─────────────────────────────────────────────

def get_db():
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    return client[DB_NAME]

def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()

def save_to_mongo(news_list: List[Dict]) -> int:
    if not news_list:
        print("  [mongo] 无数据写入")
        return 0
    db = get_db()
    coll = db[COLLECTION]
    inserted = 0
    for item in news_list:
        item['hash'] = hash_content(item.get('title', '') + item.get('published', ''))
        item['saved_at'] = datetime.now().isoformat()
        result = coll.update_one(
            {'hash': item['hash']},
            {'$set': item},
            upsert=True
        )
        if result.upserted_id or result.modified_count:
            inserted += 1
    print(f"  [mongo] 写入 {inserted} 条")
    return inserted

# ─── Tavily ─────────────────────────────────────────────

def fetch_tavily(query: str = "crypto bitcoin ethereum news", max_results: int = 10) -> List[Dict]:
    """用 Tavily API 搜索加密货币新闻"""
    if not TAVILY_API_KEY:
        print("  [tavily] 无 API Key，跳过")
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        result = client.search(
            query=query,
            max_results=max_results,
            # 只搜索近7天的结果
            search_depth="advanced",
        )
        news = []
        for r in (result.get('results') or []):
            news.append({
                "title": r.get('title', '').strip(),
                "link": r.get('url', '').strip(),
                "published": datetime.now().isoformat(),
                "description": r.get('content', '')[:300],
                "source": "tavily",
            })
        print(f"  [tavily] 获取 {len(news)} 条")
        return news
    except Exception as e:
        print(f"  [tavily] 错误: {e}")
        return []

# ─── SearXNG ────────────────────────────────────────────

def fetch_searxng(query: str = "bitcoin ethereum crypto news", max_results: int = 10) -> List[Dict]:
    """用 SearXNG 搜索（需自建实例）"""
    if not SEARXNG_URL:
        print("  [searxng] 无 URL（需自建实例），跳过")
        return []
    try:
        resp = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json", "engines": "google", "limit": max_results},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if resp.status_code != 200:
            print(f"  [searxng] HTTP {resp.status_code}")
            return []
        data = resp.json()
        news = []
        for r in (data.get('results') or [])[:max_results]:
            news.append({
                "title": r.get('title', '').strip(),
                "link": r.get('url', '').strip(),
                "published": datetime.now().isoformat(),
                "description": r.get('content', '')[:300] if r.get('content') else '',
                "source": "searxng",
            })
        print(f"  [searxng] 获取 {len(news)} 条")
        return news
    except Exception as e:
        print(f"  [searxng] 错误: {e}")
        return []

# ─── RSS ────────────────────────────────────────────────

RSS_SOURCES = [
    {"name": "cointelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "decrypt",      "url": "https://decrypt.co/feed"},
    {"name": "theblock",     "url": "https://www.theblock.co/rss.xml"},
]

def parse_rss(source: Dict) -> List[Dict]:
    news = []
    try:
        r = requests.get(source["url"], timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code != 200:
            print(f"  [{source['name']}] HTTP {r.status_code}")
            return news
        root = ET.fromstring(r.content)
        channel = root.find('channel')
        if channel is None:
            return news
        items = channel.findall('item')
        for item in items[:MAX_PER_SOURCE]:
            try:
                title = item.findtext('title', '').strip()
                link  = item.findtext('link', '').strip()
                pub   = item.findtext('pubDate', '') or item.findtext('dc:date', '')
                desc  = (item.findtext('description') or '')[:200]
                if not title or not link:
                    continue
                published = None
                if pub:
                    try:
                        from dateutil import parser as dp
                        published = dp.parse(pub).isoformat()
                    except:
                        published = datetime.now().isoformat()
                news.append({
                    "title": title,
                    "link": link,
                    "published": published,
                    "description": desc,
                    "source": source["name"],
                })
            except Exception:
                continue
        print(f"  [{source['name']}] 解析 {len(news)} 条")
    except Exception as e:
        print(f"  [{source['name']}] 错误: {e}")
    return news

def fetch_rss() -> List[Dict]:
    all_news = []
    for src in RSS_SOURCES:
        all_news.extend(parse_rss(src))
    return all_news

# ─── PANewsLab ──────────────────────────────────────────

def fetch_panewslab() -> List[Dict]:
    news = []
    try:
        r = requests.get(
            "https://www.panewslab.com/zh",
            timeout=15,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        if r.status_code != 200:
            print(f"  [panewslab] HTTP {r.status_code}")
            return news
        soup = BeautifulSoup(r.text, 'html.parser')
        seen = set()
        count = 0
        for t in soup.find_all('a', href=True):
            href = t.get('href', '')
            text = t.get_text(strip=True)
            if '/zh/articles/' not in href or href in seen:
                continue
            if len(text) < 10 or len(text) > 150:
                continue
            seen.add(href)
            full_url = href if href.startswith('http') else f'https://www.panewslab.com{href}'
            news.append({
                "title": text,
                "link": full_url,
                "published": datetime.now().isoformat(),
                "description": '',
                "source": "panewslab",
            })
            count += 1
            if count >= MAX_PER_SOURCE:
                break
        print(f"  [panewslab] 解析 {count} 条")
    except Exception as e:
        print(f"  [panewslab] 错误: {e}")
    return news

# ─── 主流程 ─────────────────────────────────────────────

class CryptoNewsFetcher:
    def __init__(self, tavily_key: str = '', searxng_url: str = ''):
        self.tavily_key = tavily_key or TAVILY_API_KEY
        self.searxng_url = searxng_url or SEARXNG_URL

    def fetch_tavily(self, query: str = "crypto bitcoin ethereum news", max_results: int = 10) -> List[Dict]:
        if not self.tavily_key:
            return []
        original = TAVILY_API_KEY
        import os as _os
        _os.environ['TAVILY_API_KEY'] = self.tavily_key
        result = fetch_tavily(query, max_results)
        _os.environ['TAVILY_API_KEY'] = original
        return result

    def fetch_searxng(self, query: str = "bitcoin ethereum crypto news", max_results: int = 10) -> List[Dict]:
        if not self.searxng_url:
            return []
        original = SEARXNG_URL
        import os as _os
        _os.environ['SEARXNG_URL'] = self.searxng_url
        result = fetch_searxng(query, max_results)
        _os.environ['SEARXNG_URL'] = original
        return result

    def fetch_all(self, tavily_query: str = "crypto bitcoin ethereum 2026", max_results: int = 10) -> int:
        """抓取所有来源并写入 MongoDB"""
        all_news = []
        seen_links = {}

        # 1. Tavily
        print("[Tavily] 搜索中...")
        tavily_news = fetch_tavily(tavily_query, max_results)
        for n in tavily_news:
            if n['link'] not in seen_links:
                seen_links[n['link']] = True
                all_news.append(n)

        # 2. SearXNG
        print("[SearXNG] 搜索中...")
        searxng_news = fetch_searxng(tavily_query, max_results)
        for n in searxng_news:
            if n['link'] not in seen_links:
                seen_links[n['link']] = True
                all_news.append(n)

        # 3. RSS
        print("[RSS] 抓取中...")
        for n in fetch_rss():
            if n['link'] not in seen_links:
                seen_links[n['link']] = True
                all_news.append(n)

        # 4. PANewsLab
        print("[PANewsLab] 抓取中...")
        for n in fetch_panewslab():
            if n['link'] not in seen_links:
                seen_links[n['link']] = True
                all_news.append(n)

        print(f"\n[总计] 去重后 {len(all_news)} 条")
        return save_to_mongo(all_news)


def main():
    print("=" * 55)
    print(f"加密货币舆情抓取  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)
    fetcher = CryptoNewsFetcher()
    count = fetcher.fetch_all()
    print(f"\n完成，写入 {count} 条到 MongoDB [{DB_NAME}.{COLLECTION}]")


if __name__ == "__main__":
    main()
