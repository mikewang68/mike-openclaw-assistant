#!/usr/bin/env python3
"""
Execute Code phase for papers that have passed V1+V2.

【原子性保证】：PDF → MD → V3验证 → 飞书，四步中途任何一步失败，
立即删除已生成文件（PDF + MD），保持要么全有、要么全无。

文件名算法：re.sub(r'[^a-zA-Z0-9]', '_', title[:30])
—— 与 markdown_generator.py 的 safe_name() 完全一致。
"""
import subprocess
import json
import os
import sys
import re
import time
from pathlib import Path
from datetime import datetime, timedelta

# 输出文件名日期：今天（pipeline 运行日期）
OUTPUT_DATE = datetime.now().strftime('%Y-%m-%d')

# 论文目录：从命令行参数或环境变量获取
PAPER_DIR_DATE = sys.argv[1] if len(sys.argv) > 1 else (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
BASE = f"/home/node/.openclaw/workspace/workareas/shared/papers/{PAPER_DIR_DATE}"
PAPERS_DIR = "/obsidian/02_Output/04_论文"
PDF_DIR = "/obsidian/01_Input/04_PDF"
VERIFY_SCRIPT = "/home/node/.openclaw/workspace/skills/verify_v3.py"
MD_GENERATOR = "/home/node/.openclaw/workspace/workareas/code/markdown_generator.py"
SYNC_SCRIPT = "/program/paper/scripts/sync_feishu.py"


def safe_name(title):
    """与 markdown_generator.py 一致：先截断30字符，再替换所有非字母数字为下划线"""
    return re.sub(r'[^a-zA-Z0-9]', '_', title[:30])


def is_rate_limit(err):
    """检测是否是 429 速率限制错误"""
    if not err:
        return False
    err_lower = err.lower()
    return '429' in err or 'rate limit' in err_lower or 'rate_limit' in err_lower or 'usage limit exceeded' in err_lower


def run_cmd(cmd, timeout=120, max_retries=5, step_name=""):
    """
    执行命令，带 429 指数退避重试。
    - 检测 stderr/stdout 中的 429 错误
    - 429 时清理已生成文件后重试（保持原子性）
    - 其他错误直接失败
    """
    for attempt in range(max_retries):
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        rc, stdout, stderr = result.returncode, result.stdout, result.stderr

        # 非429错误，或者成功，直接返回
        if rc == 0 or not is_rate_limit(stderr + stdout):
            return rc, stdout, stderr

        # 429 速率限制
        wait = 30 * (2 ** attempt)  # 30s → 60s → 120s → 240s → 480s
        print(f"     [RETRY-{attempt+1}/{max_retries}] 429 rate limit, waiting {wait}s...", file=sys.stderr)
        time.sleep(wait)

    # 超过最大重试次数
    return rc, stdout, stderr


def cleanup_files(pdf_path, md_path):
    """原子清理：删除已生成的 PDF 和 MD"""
    for f in [pdf_path, md_path]:
        if f and os.path.exists(f):
            os.remove(f)
            print(f"     [CLEANUP] Removed: {f}")


def process_paper(arxiv_num):
    """原子化处理单篇论文"""
    print(f"\n{'='*60}")
    print(f"Processing: {arxiv_num}")

    reviewer_path = f"{BASE}/{arxiv_num}/reviewer.json"
    coach_path = f"{BASE}/{arxiv_num}/coach.json"

    if not os.path.exists(reviewer_path):
        print(f"  SKIP: reviewer.json not found")
        return "SKIP", None

    # Get title and compute safe filename
    with open(reviewer_path) as f:
        d = json.load(f)
    title = d.get('part1', {}).get('title', 'untitled')
    title_safe = safe_name(title)
    print(f"  Title: {title[:50]}...")
    print(f"  SafeName: {title_safe}")

    # 预生成所有输出文件路径
    pdf_name = f"{OUTPUT_DATE}-{title_safe}.pdf"
    md_name = f"{OUTPUT_DATE}-{title_safe}-评阅意见.md"
    pdf_path = f"{PDF_DIR}/{pdf_name}"
    md_path = f"{PAPERS_DIR}/{md_name}"

    # 跟踪已生成文件（用于失败时清理）
    generated = []

    try:
        # Step 1: PDF download
        print(f"  [1/4] PDF download...")
        rc, out, err = run_cmd(
            f'curl -L -o "{pdf_path}" "https://arxiv.org/pdf/{arxiv_num}.pdf"',
            timeout=60
        )
        if rc != 0 or not os.path.exists(pdf_path):
            print(f"  [1/4] PDF: FAILED (curl returned {rc})")
            return "FAIL", "PDF download failed"
        size = os.path.getsize(pdf_path)
        print(f"  [1/4] PDF: OK ({size} bytes)")
        generated.append(pdf_path)

        # Step 2: Markdown generation
        print(f"  [2/4] Markdown generation...")
        rc, out, err = run_cmd(
            f'python3 {MD_GENERATOR} '
            f'"{reviewer_path}" "{coach_path}" "{PAPERS_DIR}/" '
            f'"{pdf_path}" "{OUTPUT_DATE}"',
            timeout=120,
            step_name="markdown_generator"
        )
        if rc != 0 or not os.path.exists(md_path):
            print(f"  [2/4] MD: FAILED ({err[-200:] if err else 'file not created'})")
            cleanup_files(pdf_path, None)
            return "FAIL", "MD generation failed"
        print(f"  [2/4] MD: OK")
        generated.append(md_path)

        # Step 3: V3 verification
        print(f"  [3/4] V3 verification...")
        rc, out, err = run_cmd(
            f'python3 {VERIFY_SCRIPT} "{md_path}" "{arxiv_num}"',
            timeout=60
        )
        if rc != 0:
            print(f"  [3/4] V3: FAILED - {err[-200:] if err else 'verification failed'}")
            cleanup_files(pdf_path, md_path)
            return "FAIL", "V3 verification failed"
        print(f"  [3/4] V3: PASS")

        # Step 4: Feishu sync
        print(f"  [4/4] Feishu sync...")
        rc, out, err = run_cmd(
            f'python3 {SYNC_SCRIPT} "{md_path}" "{pdf_path}"',
            timeout=60,
            step_name="feishu_sync"
        )
        if rc != 0:
            print(f"  [4/4] Feishu: FAILED ({err[-200:] if err else 'sync failed'})")
            cleanup_files(pdf_path, md_path)
            return "FAIL", "Feishu sync failed"
        print(f"  [4/4] Feishu: OK")

        print(f"  ✅ ALL COMPLETE")
        return "PASS", md_name

    except Exception as e:
        print(f"  ❌ EXCEPTION: {e}")
        cleanup_files(pdf_path, md_path)
        return "FAIL", str(e)


def get_score(reviewer_path):
    """从 reviewer.json 读取总分"""
    try:
        with open(reviewer_path) as f:
            d = json.load(f)
        scores = d.get('part2', {}).get('scores', [])
        if scores and isinstance(scores, list):
            return scores[-1].get('total_score', 0)
    except:
        pass
    return 0


def main():
    # 扫描论文目录，找 score>=80 且 coach.json 存在的论文
    if not os.path.exists(BASE):
        print(f"ERROR: {BASE} not found")
        sys.exit(1)

    dirs = [d for d in os.listdir(BASE) if d.startswith('2604')]

    score80_papers = []
    for d in dirs:
        rj_path = f"{BASE}/{d}/reviewer.json"
        cj_path = f"{BASE}/{d}/coach.json"
        if not os.path.exists(cj_path):
            continue  # coach not ready, skip
        score = get_score(rj_path)
        if score >= 80:
            score80_papers.append(d)

    print(f"Found {len(score80_papers)} papers with score >= 80 and coach ready")

    results = {"PASS": [], "FAIL": [], "SKIP": []}
    for arxiv_num in sorted(score80_papers):
        status, msg = process_paper(arxiv_num)
        results[status].append((arxiv_num, msg))

    print(f"\n{'='*60}")
    print(f"SUMMARY:")
    print(f"  PASS: {len(results['PASS'])}")
    print(f"  FAIL: {len(results['FAIL'])}")
    print(f"  SKIP: {len(results['SKIP'])}")

    if results['FAIL']:
        print(f"\nFailed papers:")
        for arxiv, msg in results['FAIL']:
            print(f"  {arxiv}: {msg}")

    return 0 if not results['FAIL'] else 1


if __name__ == "__main__":
    sys.exit(main())
