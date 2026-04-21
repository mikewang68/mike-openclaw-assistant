"""
Knowledge Pipeline 主程序
=======================

职责划分（Plan A）：
  - pipeline.py：非 LLM 步骤（PDF转换、MongoDB写入、Neo4j同步、推理）
  - Main Agent：通过 sessions_spawn 调用 LLM 做 extraction 和 discovery

用法：
  python3 /program/knowledge/pipeline.py --date 2026-04-13      # PDF转换 + 文档索引
  python3 /program/knowledge/pipeline.py --write-triples       # 从已转换的MD抽取triples写入MongoDB
  python3 /program/knowledge/pipeline.py --align                # 实体归一化
  python3 /program/knowledge/pipeline.py --sync                 # Neo4j同步
  python3 /program/knowledge/pipeline.py --reasoner             # 推理
  python3 /program/knowledge/pipeline.py --status              # 状态
  python3 /program/knowledge/pipeline.py --full                # 完整流程（非LLM步骤）
"""

import sys
import os
import json
import glob
import argparse
import subprocess
from datetime import datetime, timedelta

# /program/knowledge 优先
sys.path.insert(0, "/program/knowledge")

from config import PDF_DIR, MD_DIR, REVIEW_DIR
from mongo_writer import MongoWriter, write_document_index
from neo4j_writer import Neo4jWriter, write_triples_placeholder, NEO4J_AVAILABLE
from reasoner_v2 import forward_chain as infer

# entity modules
sys.path.insert(0, "/program/knowledge")
from entity_discovery import extract_candidates, build_discovery_prompt, append_judgments, YAML_PATH
from entity_aligner import align_entities as entity_aligner_align

# ─────────────────────────────────────────────────────────
# PDF → MD（使用 pdftext）
# ─────────────────────────────────────────────────────────

def pdf_to_md(pdf_path: str, force: bool = False) -> tuple[str, int]:
    """
    使用 pdftext 将 PDF 转换为 MD
    Returns: (md_path, char_count)
    """
    filename = os.path.basename(pdf_path)
    doc_id = os.path.splitext(filename)[0]
    md_path = os.path.join(MD_DIR, doc_id + ".md")

    if os.path.exists(md_path) and not force:
        with open(md_path, encoding="utf-8") as f:
            text = f.read()
        return md_path, len(text)

    cmd = ["/home/node/.local/bin/pdftext", pdf_path, "--out_path", md_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise Exception(f"pdftext failed: {proc.stderr}")

    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    return md_path, len(text)


def process_pdfs_by_date(target_date: str, force: bool = False) -> list:
    """
    扫描并转换目标日期的 PDF
    Returns: [(doc_id, pdf_path, md_path), ...]
    """
    pdfs = glob.glob(os.path.join(PDF_DIR, "*.pdf"))
    target = datetime.strptime(target_date, "%Y-%m-%d").date()

    results = []
    for pdf_path in pdfs:
        mtime = datetime.fromtimestamp(os.path.getmtime(pdf_path)).date()
        if mtime != target:
            continue

        filename = os.path.basename(pdf_path)
        doc_id = os.path.splitext(filename)[0]
        print(f"\n📄 {filename}")

        try:
            md_path, chars = pdf_to_md(pdf_path, force=force)
            print(f"  ✅ MD: {chars} chars → {os.path.basename(md_path)}")
        except Exception as e:
            print(f"  ❌ PDF → MD 失败: {e}")
            continue

        # 文档索引
        try:
            write_document_index(pdf_path, md_path, title=doc_id)
            print(f"  ✅ 文档索引写入成功")
        except Exception as e:
            print(f"  ⚠️ 文档索引失败: {e}")

        results.append((doc_id, pdf_path, md_path))

    return results


def write_triples_from_md(doc_id: str, md_path: str, triples: list) -> int:
    """将 LLM 抽取的 triples 写入 MongoDB"""
    if not triples:
        return 0
    writer = MongoWriter()
    count = writer.write_triples(triples, source=doc_id)
    writer.close()
    return count


# ─────────────────────────────────────────────────────────
# Entity Discovery（纯函数，供 Main Agent 调用）
# ─────────────────────────────────────────────────────────

def extract_title_abstract(md_path: str) -> tuple[str, str]:
    """从 MD 文件提取 title 和 abstract"""
    with open(md_path, encoding="utf-8") as f:
        md_text = f.read()

    # title
    title = os.path.basename(md_path).replace(".md", "")
    if md_text.startswith("---"):
        parts = md_text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].split("\n"):
                if line.startswith("title:"):
                    title = line.split("title:", 1)[1].strip()
                    break

    # abstract
    abstract_lines = []
    found_empty_line = False
    for line in md_text.split("\n"):
        if not line.strip():
            found_empty_line = True
            continue
        if found_empty_line and len(line) > 30:
            abstract_lines.append(line)
            if len(abstract_lines) >= 6:
                break
        elif found_empty_line and abstract_lines:
            break

    abstract = " ".join(abstract_lines)[:600]
    return title, abstract


def discovery_for_paper(doc_id: str, md_path: str) -> dict:
    """
    对单篇论文运行 entity discovery
    返回: {"candidates": int, "added": int}
    """
    title, abstract = extract_title_abstract(md_path)

    with open(md_path, encoding="utf-8") as f:
        md_text = f.read()

    candidates = extract_candidates(title=title, abstract=abstract, keywords=[], body_text=md_text)
    if not candidates:
        return {"candidates": 0, "added": 0}

    prompt = build_discovery_prompt(title, abstract, candidates, YAML_PATH)
    return {
        "doc_id": doc_id,
        "title": title,
        "candidates": len(candidates),
        "prompt": prompt,  # Main Agent 取走这个 prompt 去调 LLM
    }


# ─────────────────────────────────────────────────────────
# Neo4j Sync
# ─────────────────────────────────────────────────────────

def sync_neo4j() -> int:
    """将 MongoDB 中所有 triples 同步到 Neo4j"""
    writer = MongoWriter()
    triples = writer.get_all_triples()
    writer.close()

    if not triples:
        print("  ⚠️ MongoDB 无 triples")
        return 0

    print(f"  📊 加载 {len(triples)} triples")

    if NEO4J_AVAILABLE:
        neo4j = Neo4jWriter()
        neo4j.write_triples(triples)
        neo4j.close()
        print(f"  ✅ 同步 {len(triples)} 到 Neo4j")
        return len(triples)
    else:
        write_triples_placeholder(triples)
        print(f"  ⏭️  Neo4j 不可用，写入 placeholder")
        return 0


# ─────────────────────────────────────────────────────────
# Reasoner
# ─────────────────────────────────────────────────────────

def run_reasoner() -> list:
    """运行 Reasoner v2 推理"""
    writer = MongoWriter()
    triples = writer.get_all_triples()
    writer.close()

    if not triples:
        print("  ⚠️ 无 triples 可推理")
        return []

    from reasoner_v2 import forward_chain, COMPOSITION_TABLE, REASONER_HOPS, REASONER_MIN_CONF
    inferred_results = forward_chain(triples, COMPOSITION_TABLE, max_hops=REASONER_HOPS, min_confidence=REASONER_MIN_CONF)
    inferred = [r.to_dict() for r in inferred_results]
    print(f"  🧠 生成 {len(inferred)} inferred triples")

    if inferred:
        writer = MongoWriter()
        count = writer.write_inferred(inferred)
        writer.close()
        print(f"  ✅ 写入 {count} inferred_triples 到 MongoDB")

        if NEO4J_AVAILABLE:
            neo4j = Neo4jWriter()
            neo4j.write_triples(inferred)
            neo4j.close()
            print(f"  ✅ 同步 {len(inferred)} 到 Neo4j")

    return inferred


# ─────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────

def show_status():
    writer = MongoWriter()
    stats = writer.stats()
    print(f"\n📊 Knowledge Pipeline 状态")
    print(f"{'='*40}")
    print(f"  triples:          {stats['triples']}")
    print(f"  inferred_triples: {stats['inferred_triples']}")
    print(f"  documents:        {stats['documents']}")
    pdfs = glob.glob(os.path.join(PDF_DIR, "*.pdf"))
    print(f"\n📁 PDF:  {PDF_DIR}  ({len(pdfs)} files)")
    print(f"📁 MD:   {MD_DIR}")
    writer.close()


# ─────────────────────────────────────────────────────────
# Follow Builders 处理
# ─────────────────────────────────────────────────────────

FOLLOW_DIR = "/obsidian/01_Input/06_follow"


def process_follow_builders() -> list:
    """
    处理 follow-builders 目录下最新的 digest 文件，提取三元组写入 MongoDB
    与 PDF 流水线的"前一天"逻辑不同：直接取最新生成的文件
    （因为 follow-builders cron 每天 06:05 生成，pipeline 06:30 处理同一个早晨的文件）
    Returns: [(doc_id, md_path), ...]
    """
    if not os.path.exists(FOLLOW_DIR):
        print(f"  ⚠️ {FOLLOW_DIR} 不存在")
        return []

    files = glob.glob(os.path.join(FOLLOW_DIR, "*-follow-builders.md"))
    if not files:
        print(f"  ⚠️ 没有找到 follow-builders digest 文件")
        return []

    # 取最新生成的文件（mtime 最新的）
    latest = max(files, key=os.path.getmtime)
    doc_id = os.path.basename(latest).replace(".md", "")

    print(f"\n📰 处理 Follow Builders: {doc_id}")

    with open(latest, encoding="utf-8") as f:
        content = f.read()

    print(f"  ✅ 读取 {len(content)} chars")
    return [(doc_id, latest)]


# ─────────────────────────────────────────────────────────
# Main CLI
# ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Knowledge Pipeline（Plan A）")
    parser.add_argument("--date", type=str, help="处理指定日期的 PDF（YYYY-MM-DD）")
    parser.add_argument("--paper", type=str, help="处理单个 PDF")
    parser.add_argument("--status", action="store_true", help="显示状态")
    parser.add_argument("--follow", action="store_true", help="处理 follow-builders digest")
    parser.add_argument("--full", action="store_true", help="完整流程（转换+归一化+同步+推理）")
    parser.add_argument("--align", action="store_true", help="只跑实体归一化")
    parser.add_argument("--sync", action="store_true", help="只跑 Neo4j 同步")
    parser.add_argument("--reasoner", action="store_true", help="只跑推理")
    parser.add_argument("--force", action="store_true", help="强制重新处理")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.follow:
        results = process_follow_builders()
        if not results:
            return
        print(f"\n📋 接下来由 Main Agent 执行 LLM 步骤：")
        for doc_id, md_path in results:
            print(f"  - {doc_id}: {md_path}")
        print(f"\n💡 使用 sessions_spawn 派发 LLM 抽取三元组，source='follow_builders'")
        return

    if args.reasoner:
        print("\n🧠 运行 Reasoner...")
        run_reasoner()
        return

    if args.align:
        print("\n🔧 运行 Entity Alignment...")
        entity_aligner_align()
        return

    if args.sync:
        print("\n🔄 运行 Neo4j Sync...")
        sync_neo4j()
        return

    if args.paper:
        # 单个 PDF
        doc_id = os.path.splitext(os.path.basename(args.paper))[0]
        print(f"\n📄 处理: {os.path.basename(args.paper)}")
        try:
            md_path, chars = pdf_to_md(args.paper, force=args.force)
            print(f"  ✅ MD: {chars} chars → {os.path.basename(md_path)}")
            write_document_index(args.paper, md_path, title=doc_id)
            print(f"  ✅ 文档索引完成")
        except Exception as e:
            print(f"  ❌ 失败: {e}")
        return

    if args.date:
        target_date = args.date
    else:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"\n📅 目标日期: {target_date}")
    results = process_pdfs_by_date(target_date, force=args.force)

    if not results:
        print(f"⚠️  没有找到 {target_date} 的 PDF")
        return

    print(f"\n✅ 转换完成：{len(results)} 篇")
    print(f"\n📋 接下来由 Main Agent 执行 LLM 步骤：")
    for doc_id, _, md_path in results:
        print(f"  - {doc_id}")

    if args.full:
        print(f"\n{'='*60}")
        print(f"🔧 运行 Entity Alignment...")
        entity_aligner_align()
        print(f"\n🔄 运行 Neo4j Sync...")
        sync_neo4j()
        print(f"\n🧠 运行 Reasoner...")
        run_reasoner()


if __name__ == "__main__":
    main()
