# crypto-news Skill

> 加密货币舆情抓取 — 多源聚合：Tavily 搜索 + SearXNG 搜索 + 四大 RSS 源

## 信息源

| 来源 | 类型 | 说明 |
|------|------|------|
| **Tavily** | API 搜索 | `tvly-dev-*` key，已配置。可搜索加密货币关键词，返回标题/摘要/链接。 |
| **SearXNG** | 自建搜索 | 需要在 NAS 上自建 SearXNG 实例（Docker），提供隐私搜索 API。 |
| Cointelegraph | RSS | `https://cointelegraph.com/rss` |
| Decrypt | RSS | `https://decrypt.co/feed` |
| TheBlock | RSS | `https://www.theblock.co/rss.xml` |
| PANewsLab | HTML 爬取 | `https://www.panewslab.com/zh` |

## 环境变量

```bash
# Tavily API Key（已配置）
TAVILY_API_KEY=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

# SearXNG NAS 自建实例（已配置）
SEARXNG_URL=http://192.168.1.2:38080

# MongoDB
MONGO_URI=mongodb://stock:681123@192.168.1.2:27017/admin
```

## 使用方式

### 方式1：Cron 定时抓取（自动运行）

```bash
# 写入 MongoDB: crypto.crypto_news
python3 /home/node/.openclaw/workspace/workareas/main/custom_skills/crypto-news/scripts/fetcher.py
```

### 方式2：作为模块调用

```python
import sys
sys.path.insert(0, '/home/node/.openclaw/workspace/workareas/main/custom_skills/crypto-news/scripts')
from fetcher import CryptoNewsFetcher

fetcher = CryptoNewsFetcher()
results = fetcher.fetch_all()
print(f"获取 {len(results)} 条新闻")
```

### 方式3：指定来源

```python
from fetcher import CryptoNewsFetcher
fetcher = CryptoNewsFetcher()

# 只用 Tavily
fetcher.fetch_tavily(query="bitcoin ETF 2026", max_results=10)

# 只用 RSS
fetcher.fetch_rss()

# 只用 SearXNG
fetcher.fetch_searxng(query="ethereum upgrade crypto")
```

## SearXNG 自建指南

SearXNG 需要自建服务（推荐 Docker 部署到 NAS）：

```bash
# 1. 在 NAS 上运行 SearXNG Docker
docker run -d --name searxng \
  -p 8888:8080 \
  -v /path/to/searxng:/etc/searxng \
  -e SEARXNG_BASE_URL=http://your-nas:8888/ \
  searxng/searxng:latest

# 2. 设置 JSON 格式输出（默认关闭）
# 编辑 /path/to/searxng/settings.yml:
#   search:
#     format: json
#   server:
#     secret_key: "your-secret-key"

# 3. 设置环境变量
export SEARXNG_URL=http://192.168.1.x:8888
```

## 输出格式

每条新闻：
```json
{
  "title": "Bitcoin ETF Approval Sparks Rally",
  "link": "https://example.com/article",
  "published": "2026-04-26T10:30:00",
  "description": "...",
  "source": "tavily|searxng|cointelegraph|decrypt|theblock|panewslab"
}
```

## 后续处理

`crypto.crypto_news` → [knowledge-pipeline](file:///obsidian/03_Project/06_openclaw/workflow_knowledge_pipeline.md) → Neo4j 图谱
