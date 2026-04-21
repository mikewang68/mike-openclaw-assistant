"""
批量处理论文知识抽取 v2
=========================

核心逻辑：
1. PDF → MD（MinerU pdftext，~1秒/篇）
2. documents 索引（1条/篇，path 优先指向评阅MD，无则指向原始MD）
3. triples 双路抽取：
   - 有评阅 → 评阅regex + 原文LLM 双路
   - 无评阅 → 仅原文LLM

索引规则（documents）：
- doc_id = PDF文件名（不含扩展名）
- path = 评阅MD路径（有评阅时）/ 原始MD路径（无评阅时）
- has_review = True/False
"""

import os
import sys
import glob
import re
from datetime import datetime

sys.path.insert(0, "/program/knowledge")
from config import PDF_DIR, MD_DIR
from mongo_writer import MongoWriter
from neo4j_writer import Neo4jWriter, NEO4J_AVAILABLE
from review_extractor import extract_review_properties
from pdf_to_md import pdf_to_md

PDF_DIR = "/obsidian/01_Input/04_PDF"
MD_DIR = "/obsidian/01_Input/05_PDF2MD"
REVIEW_DIR = "/obsidian/02_Output/04_论文"


def fuzzy_match_review(base: str) -> str:
    """
    模糊匹配评阅文件
    评阅文件名格式：YYYY-MM-DD_Title-评阅意见.md
    MD文件名格式：YYYY-MM-DD_Title（可能更长或更短）
    """
    # 精确匹配
    exact = os.path.join(REVIEW_DIR, base + "-评阅意见.md")
    if os.path.exists(exact):
        return exact

    # 模糊匹配：日期前缀 + 核心标题
    match = re.match(r"^(\d{4}-\d{2}-\d{2})-(.{10,30})", base)
    if not match:
        return None

    date_prefix = match.group(1)
    core_title = match.group(2)[:25]

    for f in os.listdir(REVIEW_DIR):
        if not f.endswith("-评阅意见.md"):
            continue
        if f.startswith(date_prefix):
            rest = f[len(date_prefix)+1:].replace("-评阅意见.md", "")
            if core_title[:20] in rest or rest[:20] in core_title:
                return os.path.join(REVIEW_DIR, f)
    return None


def find_paper_md(base: str) -> tuple:
    """
    返回 (md_path, has_review)
    优先返回评阅MD路径，无则返回原始MD路径
    """
    review_md = fuzzy_match_review(base)
    if review_md:
        return review_md, True
    md_path = os.path.join(MD_DIR, base + ".md")
    return md_path, False


def process_single_paper(pdf_path: str, force_reconvert: bool = False) -> dict:
    """
    处理单篇论文
    返回: {"paper": str, "steps": {}, "llm_count": int, "review_count": int}
    """
    filename = os.path.basename(pdf_path)
    base = filename.replace(".pdf", "")
    result = {"paper": base, "steps": {}, "llm_count": 0, "review_count": 0}

    # ── Step 1: PDF → MD（MinerU pdftext）─────────────────
    pdf_md_path = os.path.join(MD_DIR, base + ".md")
    if not os.path.exists(pdf_md_path) or force_reconvert:
        try:
            md_text, _ = pdf_to_md(pdf_path)
            result["steps"]["pdf_to_md"] = f"ok ({len(md_text)} chars)"
        except Exception as e:
            result["steps"]["pdf_to_md"] = f"error: {e}"
            return result
    else:
        result["steps"]["pdf_to_md"] = "skipped"

    # ── Step 2: 文档索引（1条/篇，path优先评阅MD）─────────
    md_path, has_review = find_paper_md(base)
    try:
        writer = MongoWriter()
        existing = writer.get_document(base)
        doc = {
            "doc_id": base,
            "path": md_path,
            "pdf_path": pdf_path,
            "has_review": has_review,
            "title": base,
            "tags": [],
            "summary": ""
        }
        if existing:
            writer.db.documents.update_one({"doc_id": base}, {"$set": doc})
        else:
            writer.write_document(doc)
        writer.close()
        result["steps"]["document_index"] = "ok" if has_review else "ok_no_review"
    except Exception as e:
        result["steps"]["document_index"] = f"error: {e}"

    # ── Step 3: 提取评论属性 ──────────────────────────
    review_props = None
    review_md = fuzzy_match_review(base)

    if review_md and os.path.exists(review_md):
        try:
            review_props = extract_review_properties(review_md, paper_title=base)
            result["review_count"] = 1
            result["steps"]["review_extraction"] = f"ok"
        except Exception as e:
            result["steps"]["review_extraction"] = f"error: {e}"
    else:
        result["steps"]["review_extraction"] = "not_found"

    # LLM 抽取标记（需 subagent 处理）
    if os.path.exists(pdf_md_path):
        result["steps"]["llm_extraction"] = "pending_subagent"
    else:
        result["steps"]["llm_extraction"] = "no_md"

    # ── Step 4: MongoDB 写入（评阅属性放入 document）───────────────
    writer = MongoWriter()
    total_written = 0
    if review_props:
        try:
            writer.db.documents.update_one(
                {"doc_id": base},
                {"$set": {"review_properties": review_props}},
                upsert=True
            )
            total_written = 1
        except Exception as e:
            result["steps"]["mongodb"] = f"error: {e}"
            writer.close()
            return result

    result["steps"]["mongodb"] = f"ok ({total_written} written)"
    result["total_written"] = total_written
    writer.close()
    return result


def batch_convert_pdfs() -> dict:
    """
    批量转换 PDF → MD（全部PDF，无评阅也转换）
    """
    print(f"\n{'='*60}")
    print(f"Step 1: 批量 PDF → MD（MinerU pdftext）")

    pdfs = [f for f in os.listdir(PDF_DIR) if f.endswith(".pdf")]
    to_convert = []
    for pdf_file in pdfs:
        base = pdf_file.replace(".pdf", "")
        md_path = os.path.join(MD_DIR, base + ".md")
        if not os.path.exists(md_path):
            to_convert.append((os.path.join(PDF_DIR, pdf_file), base))

    print(f"  需要转换: {len(to_convert)} 篇")

    converted, failed = 0, []
    for pdf_path, base in to_convert:
        try:
            md_text, _ = pdf_to_md(pdf_path)
            converted += 1
            if converted % 50 == 0:
                print(f"  {converted}/{len(to_convert)}")
        except Exception as e:
            failed.append((base, str(e)))

    print(f"  转换完成: {converted}/{len(to_convert)}")
    if failed:
        for b, e in failed[:5]:
            print(f"    {b}: {e}")

    return {"converted": converted, "failed": len(failed), "total": len(to_convert)}


def batch_review_extraction() -> dict:
    """
    批量评阅抽取属性
    """
    print(f"\n{'='*60}")
    print(f"Step 2: 批量评阅 → 属性抽取")

    writer = MongoWriter()
    processed, total_props, failed = 0, 0, []

    for review_file in sorted(os.listdir(REVIEW_DIR)):
        if not review_file.endswith("-评阅意见.md"):
            continue
        review_path = os.path.join(REVIEW_DIR, review_file)
        try:
            props = extract_review_properties(review_path)
            if props:
                base = review_file.replace("-评阅意见.md", "")
                match = re.match(r"^(\d{4}-\d{2}-\d{2})-(.*)", base)
                search_base = match.group(2) if match else base
                doc = writer.db.documents.find_one({"doc_id": {"$regex": search_base}})
                doc_id = doc["doc_id"] if doc else base

                writer.db.documents.update_one(
                    {"doc_id": doc_id},
                    {"$set": {"review_properties": props}},
                    upsert=True
                )
                total_props += 1
            processed += 1
        except Exception as e:
            failed.append((review_file, str(e)))

    writer.close()
    print(f"  处理: {processed} 篇, 抽取属性: {total_props} 篇")
    if failed:
        for f, e in failed[:3]:
            print(f"    {f}: {e}")

    return {"processed": processed, "properties": total_props, "failed": len(failed)}


def show_mongo_stats():
    """显示MongoDB当前状态"""
    writer = MongoWriter()
    stats = writer.stats()
    print(f"\n📊 MongoDB状态:")
    for k, v in stats.items():
        print(f"   {k}: {v}")
    writer.close()


def main():
    print(f"📋 批量论文知识抽取 v2")
    print(f"  PDF目录: {PDF_DIR}")
    print(f"  MD目录: {MD_DIR}")
    print(f"  评阅目录: {REVIEW_DIR}")

    # Step 1: PDF → MD
    conv_result = batch_convert_pdfs()

    # Step 2: 重建 documents 索引
    print(f"\n{'='*60}")
    print(f"Step 2: 重建 documents 索引（377条）")
    writer = MongoWriter()
    writer.db.documents.delete_many({})
    pdfs = sorted(glob.glob(os.path.join(PDF_DIR, "*.pdf")))
    indexed = 0
    for pdf_path in pdfs:
        base = os.path.basename(pdf_path).replace(".pdf", "")
        md_path, has_review = find_paper_md(base)
        doc = {
            "doc_id": base,
            "path": md_path,
            "pdf_path": pdf_path,
            "has_review": has_review,
            "title": base,
            "tags": [],
            "summary": ""
        }
        writer.write_document(doc)
        indexed += 1
    writer.close()
    print(f"  已索引: {indexed} 篇")

    # Step 3: 评阅regex抽取
    review_result = batch_review_extraction()

    # Step 4: 同步Neo4j
    if NEO4J_AVAILABLE:
        print(f"\n{'='*60}")
        print(f"Step 3: 同步 Neo4j")
        try:
            import sync_neo4j
            sync_neo4j.rebuild_neo4j_from_mongodb(clear=False)
        except Exception as e:
            print(f"  ⚠️ Neo4j同步失败: {e}")

    # Step 5: Reasoner
    print(f"\n{'='*60}")
    print(f"Step 4: 运行 Reasoner v2")
    try:
        import reasoner_v2
        r = reasoner_v2.ReasonerV2()
        results = r.run()
        r.close()
        print(f"  推理结果: {len(results)} 条")
    except Exception as e:
        print(f"  ⚠️ Reasoner失败: {e}")

    show_mongo_stats()
    print(f"\n{'='*60}")
    print(f"✅ 批量处理完成")
    print(f"  PDF转换: {conv_result['converted']}/{conv_result['total']}")
    print(f"  评阅抽取: {review_result['processed']} 篇, {review_result['properties']} properties")


if __name__ == "__main__":
    main()
