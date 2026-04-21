"""
PDF → Markdown 转换
使用 MinerU pdftext 提取文本，保存为 .md 文件
速度快（~1s/篇），保留字体/段落结构，权限固化为 644
"""

import sys
import os
import subprocess
import glob
import stat

sys.path.insert(0, os.path.dirname(__file__))
from config import PDF_DIR, MD_DIR

PDFTEXT_BIN = "/home/node/.local/bin/pdftext"


def pdf_to_md(pdf_path: str) -> tuple[str, str]:
    """
    将 PDF 转换为 Markdown 文本（使用 MinerU pdftext）
    
    Returns:
        (md_text, md_path) - markdown内容和保存路径
    """
    filename = os.path.basename(pdf_path)
    base = os.path.splitext(filename)[0]
    md_path = os.path.join(MD_DIR, base + ".md")

    # 使用 pdftext 提取纯文本
    result = subprocess.run(
        [PDFTEXT_BIN, pdf_path],
        capture_output=True,
        text=True,
        timeout=30
    )
    text = result.stdout

    # 基本清理：合并断行，保留段落结构
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            cleaned.append(stripped)

    # 用双换行合并（保留段落概念）
    md_text = "\n\n".join(cleaned)

    # Obsidian wiki link frontmatter
    wiki_link = f"[[{filename}]]"
    frontmatter = f"---\nsource: \"{wiki_link}\"\ntype: text\n---\n\n"
    md_text = frontmatter + md_text

    # 保存
    os.makedirs(MD_DIR, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text)

    # 固化权限为 644（ Obsidian 可读）
    os.chmod(md_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

    return md_text, md_path


def main():
    pdfs = glob.glob(os.path.join(PDF_DIR, "*.pdf"))
    if not pdfs:
        print("No PDFs found in", PDF_DIR)
        return

    print(f"Found {len(pdfs)} PDFs, converting...")

    for pdf_path in pdfs:
        filename = os.path.basename(pdf_path)
        try:
            md_text, md_path = pdf_to_md(pdf_path)
            print(f"✅ {filename} → {os.path.basename(md_path)} ({len(md_text)} chars)")
        except Exception as e:
            print(f"❌ {filename}: {e}")


if __name__ == "__main__":
    main()
