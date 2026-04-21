#!/usr/bin/env python3
"""
Phase1a 搜索脚本：arXiv API 顺序搜索5方向，24小时内论文，去重
版本：V8.28（2026-04-19：修复手动运行日期窗口错误；支持--date参数指定输出目录）

用法：
  python3 search_arxiv_24h.py                 # 搜索最近24小时，输出到今天目录
  python3 search_arxiv_24h.py --date 2026-04-18  # 搜索指定日期的00:00~23:59 CST，输出到该日期目录
"""
import urllib.request
import urllib.parse
import json
import time
import xml.etree.ElementTree as ET
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# ========== 时间窗口计算 ==========
def compute_date_window(target_date_str=None):
    """
    计算 arXiv 查询的日期窗口。
    - 手动运行（无 --date）：搜索最近24小时（当前时刻向前推）
    - Cron 运行（--date YYYY-MM-DD）：搜索目标日期的 00:00~23:59 CST
    
    CST = UTC + 8h
    """
    now_utc = datetime.utcnow()
    now_cst = now_utc + timedelta(hours=8)
    
    if target_date_str:
        # Cron 模式：搜索 target_date 的完整一天（00:00~23:59 CST）
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
        today_date = now_cst.replace(hour=0, minute=0, second=0, microsecond=0).date()
        
        if target_date.date() < today_date:
            # target_date 是昨天 → 窗口: 昨天00:00 CST ~ 今天00:00 CST
            # 昨天00:00 CST = 前天16:00 UTC
            # 今天00:00 CST = 昨天16:00 UTC
            date_from_utc = (target_date + timedelta(hours=16) - timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
            date_to_utc   = (target_date + timedelta(hours=16)).replace(hour=16, minute=0, second=0, microsecond=0)
        elif target_date.date() == today_date:
            # target_date 是今天 → 窗口: 今天00:00 CST ~ 现在
            date_from_utc = (target_date + timedelta(hours=16)).replace(hour=16, minute=0, second=0, microsecond=0)
            date_to_utc   = now_utc
        else:
            # target_date 是未来 → 报错
            raise ValueError(f"--date 不能是未来日期: {target_date_str}")
        
        date_str = target_date_str
        window_desc = f"{target_date_str} 00:00~23:59 CST"
    else:
        # 手动模式：搜索最近24小时
        date_from_utc = now_utc - timedelta(hours=24)
        date_to_utc   = now_utc
        date_str = now_cst.strftime("%Y-%m-%d")
        window_desc = f"最近24小时（{date_from_utc.strftime('%m-%d %H:%M')} ~ {date_to_utc.strftime('%m-%d %H:%M')} UTC）"
    
    # arXiv API 只接受日期格式 YYYYMMDD（不接受时间）
    DATE_FROM = date_from_utc.strftime("%Y%m%d")
    DATE_TO   = date_to_utc.strftime("%Y%m%d")
    
    return DATE_FROM, DATE_TO, date_str, window_desc

# ========== 命令行参数 ==========
parser = argparse.ArgumentParser(description="arXiv 论文搜索（5方向，24小时内）")
parser.add_argument("--date", type=str, default=None,
                    help="指定输出目录日期（YYYY-MM-DD），用于 Cron 定时任务。不指定则默认今天。")
args = parser.parse_args()

DATE_FROM, DATE_TO, date_str, window_desc = compute_date_window(args.date)

OUTPUT_FILE = "/home/node/.openclaw/workspace/workareas/shared/papers/{date}/all_raw_papers.json"

# 5个方向（类别, 名称, 关键词[可选]）
# 注意：同一方向如有多个类别，需拆成多条记录，build_query 会正确处理 OR 链接
CATEGORIES = [
    ("cs.LG", "机器学习", None),
    ("cs.CL", "大模型", None),
    ("cs.CR", "区块链", "blockchain OR distributed ledger OR DeFi OR decentralized finance OR smart contract"),
    ("q-fin.TR", "量化交易", None),
    ("q-fin.ST", "量化交易", None),
    ("q-fin.PM", "量化交易", None),
    ("cs.CR", "数字资产安全", "cryptocurrency OR crypto-asset OR token OR wallet OR digital asset OR LLM security OR large language model security OR prompt injection OR AI safety"),
]

QUERY_DELAY = 10  # 每方向间隔10秒（防429）
MAX_RETRIES = 5
RETRY_DELAYS = [30, 60, 120, 240, 480]  # 指数退避秒数

# ========== 工具函数 ==========
def build_query(cat, keywords, date_from, date_to):
    """构建 arXiv API 查询字符串"""
    if keywords:
        return f"cat:{cat} AND ({keywords}) AND submittedDate:[{date_from} TO {date_to}]"
    return f"cat:{cat} AND submittedDate:[{date_from} TO {date_to}]"

def fetch_arxiv(query, max_results=50):
    """查询 arXiv API，带重试机制"""
    base_url = "https://export.arxiv.org/api/query"
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max_results,
    }
    url = f"{base_url}?{urllib.parse.urlencode(params)}"

    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            return data
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "timeout" in error_str.lower() or "read operation timed out" in error_str.lower():
                wait_time = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
                print(f"    ⚠️ 查询失败（{attempt+1}/{MAX_RETRIES}）：{e}")
                print(f"    ⏳ 等待 {wait_time}s 后重试...")
                time.sleep(wait_time)
            else:
                raise
    raise RuntimeError(f"arXiv API 查询失败，已重试 {MAX_RETRIES} 次")

def parse_entries(xml_data):
    """解析 arXiv API 返回的 XML，提取论文信息"""
    root = ET.fromstring(xml_data)
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    papers = []
    for entry in root.findall("atom:entry", ns):
        title = entry.find("atom:title", ns)
        published = entry.find("atom:published", ns)
        summary = entry.find("atom:summary", ns)
        link = entry.find("atom:id", ns)
        authors = [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)]
        categories = [c.get("term") for c in entry.findall("atom:category", ns)]

        # 提取 arXiv ID（从 URL 如 http://arxiv.org/abs/2603.30036v1 获取 2603.30036）
        link_text = link.text if link is not None else ""
        arxiv_id = ""
        if link_text:
            parts = link_text.rstrip("/").split("/")
            arxiv_id = parts[-1] if parts else ""

        papers.append({
            "arxiv_id": arxiv_id,
            "title": title.text.strip().replace("\n", " ") if title is not None else "",
            "published": published.text if published is not None else "",
            "summary": summary.text.strip().replace("\n", " ") if summary is not None else "",
            "authors": authors,
            "categories": categories,
            "link": link_text,
        })
    return papers

def deduplicate_by_arxiv_id(papers):
    """按 arXiv ID 去重，相同ID只保留第一条"""
    seen = set()
    unique = []
    for p in papers:
        aid = p["arxiv_id"]
        if aid and aid not in seen:
            seen.add(aid)
            unique.append(p)
    return unique

# ========== 主流程 ==========
def main():
    output_path = Path(OUTPUT_FILE.format(date=date_str))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"📅 搜索窗口: {window_desc}")
    print(f"🎯 方向: {[c[1] for c in CATEGORIES]}")
    print("=" * 60)

    all_papers = []
    direction_counts = {}

    for i, (cat, name, keywords) in enumerate(CATEGORIES):
        print(f"\n🔍 [{name}] (cat:{cat})")
        print("-" * 40)

        query = build_query(cat, keywords, DATE_FROM, DATE_TO)
        print(f"   查询: {query[:80]}...")

        try:
            xml_data = fetch_arxiv(query, max_results=50)
            papers = parse_entries(xml_data)
            direction_counts[name] = direction_counts.get(name, 0) + len(papers)
            all_papers.extend(papers)
            print(f"   ✅ 获取 {len(papers)} 篇")
        except Exception as e:
            direction_counts[name] = 0
            print(f"   ❌ 失败: {e}")

        # 方向之间间隔10秒（仅在不同方向之间，不在同一方向的多个子查询之间）
        if i + 1 < len(CATEGORIES) and CATEGORIES[i + 1][1] != name:
            print(f"   ⏳ 等待 {QUERY_DELAY}s 防限流...")
            time.sleep(QUERY_DELAY)

    # 去重
    print(f"\n{'=' * 60}")
    print(f"📦 总论文数（去重前）: {len(all_papers)}")
    unique_papers = deduplicate_by_arxiv_id(all_papers)
    print(f"📦 总论文数（去重后）: {len(unique_papers)}")

    # 按方向统计（去重后重新计算，因为同一篇论文可能同时出现在多个方向）
    # 实际上每个方向是独立查询的，去重只在最终结果里处理
    for name, count in direction_counts.items():
        print(f"   {name}: {count} 篇")

    # 保存
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(unique_papers, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已保存: {output_path}")
    print(f"✅ Phase1a 完成：{len(unique_papers)} 篇论文（5方向，24小时内，已去重）")

    return unique_papers

if __name__ == "__main__":
    main()
