"""
批量LLM抽取脚本
直接调用 MiniMax API (Anthropic格式)，不依赖subagent
"""
import os
import sys
import json
import re
import time
import requests
from datetime import datetime

sys.path.insert(0, "/program/knowledge")
from config import MD_DIR, REVIEW_DIR
from extractor import build_extraction_prompt, extract_triples_from_md
from mongo_writer import MongoWriter

# MiniMax API 配置
MINIMAX_API_KEY = "sk-cp-CBUQm3M8PXAsAa9zgaNI_zvnsFtXgirPGgOmBF1cYM6fwykMG01aGC-bcouLyWA-SrHtn-Wt87FmqHcRi4NN_it72uqBGEo1grkgyVCYzbqyCgiUUO-wXzw"
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
MODEL = "MiniMax-M2.7"


def call_llm_api(prompt: str, max_tokens: int = 4096, max_retries: int = 5) -> str:
    """调用 MiniMax Native API，带指数退避重试"""
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    for attempt in range(max_retries):
        try:
            resp = requests.post(MINIMAX_BASE_URL, headers=headers, json=payload, timeout=120)
            
            if resp.status_code == 529:
                # Server overloaded - retry with exponential backoff
                wait = (attempt + 1) * 5  # 5, 10, 15, 20, 25 seconds
                print(f" [529等待{wait}s...]", end="", flush=True)
                time.sleep(wait)
                continue
            
            resp.raise_for_status()
            data = resp.json()
            
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""
            
        except requests.exceptions.HTTPError as e:
            if resp.status_code in (429, 529):
                wait = (attempt + 1) * 10
                print(f" [HTTP {resp.status_code}等待{wait}s...]", end="", flush=True)
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = (attempt + 1) * 3
            print(f" [错误等待{wait}s: {e}]", end="", flush=True)
            time.sleep(wait)
    
    return ""


def get_processed_papers():
    """获取已处理的论文（MongoDB中有LLM triple的）"""
    writer = MongoWriter()
    # 找source为paper的triples
    processed = set()
    for doc in writer.db.triples.find({"type": "extraction"}, {"source": 1}):
        src = doc.get("source", "")
        if src:
            # source格式: "paper:xxxx"
            if src.startswith("paper:"):
                processed.add(src[6:])
            else:
                processed.add(src)
    writer.close()
    return processed


def extract_paper_triples(md_path: str, paper_title: str) -> list:
    """调用LLM对单篇论文进行抽取"""
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    
    prompt = build_extraction_prompt(md_text, paper_title)
    
    llm_output = call_llm_api(prompt)
    if not llm_output:
        return []
    
    triples = extract_triples_from_md(md_path, llm_output)
    
    # 标准化triples
    for t in triples:
        if "source" not in t:
            t["source"] = f"paper:{paper_title}"
        t["type"] = "extraction"
        t["paper_id"] = paper_title
    
    return triples


def main():
    md_dir = MD_DIR
    review_dir = REVIEW_DIR
    
    # 找有评阅的MD文件列表（优先处理有评阅的）
    reviews = {f.replace("-评阅意见.md","") for f in os.listdir(review_dir) if f.endswith("-评阅意见.md")}
    
    md_files = []
    for f in sorted(os.listdir(md_dir)):
        if not f.endswith(".md"):
            continue
        base = f.replace(".md", "")
        if base in reviews:
            md_files.append((os.path.join(md_dir, f), base))
    
    print(f"找到 {len(md_files)} 篇有评阅的MD文件")
    
    # 找已处理的
    processed = get_processed_papers()
    print(f"已处理: {len(processed)} 篇")
    
    to_process = [(p, b) for p, b in md_files if b not in processed]
    print(f"待处理: {len(to_process)} 篇")
    
    if not to_process:
        print("所有论文已处理完毕！")
        return
    
    writer = MongoWriter()
    
    success = 0
    failed = 0
    total_triples = 0
    
    for i, (md_path, paper_title) in enumerate(to_process):
        try:
            print(f"[{i+1}/{len(to_process)}] {paper_title[:60]}...", end="", flush=True)
            
            triples = extract_paper_triples(md_path, paper_title)
            
            if triples:
                n = writer.write_triples(triples, source="extraction")
                total_triples += len(triples)
                print(f" → {len(triples)} triples ({n} new)")
            else:
                print(f" → 0 triples")
            
            success += 1
            
            # 进度报告
            if (i+1) % 20 == 0:
                print(f"\n--- 进度: {i+1}/{len(to_process)}, 总triples: {total_triples} ---\n")
            
            # 避免API速率限制
            time.sleep(0.5)
            
        except Exception as e:
            print(f" ❌ {e}")
            failed += 1
            # 失败后等待更长时间
            time.sleep(2)
    
    writer.close()
    
    print(f"\n✅ 完成!")
    print(f"  成功: {success} 篇")
    print(f"  失败: {failed} 篇")
    print(f"  总triples: {total_triples}")
    
    # 显示最终MongoDB状态
    writer2 = MongoWriter()
    stats = writer2.stats()
    print(f"\n📊 MongoDB最终状态: triples={stats['triples']}")
    writer2.close()


if __name__ == "__main__":
    main()
