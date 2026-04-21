#!/usr/bin/env python3
"""
Phase1b 评分脚本 - LLM 4维评分（直接API调用，修复 openclaw agent --local 挂起问题）
版本：V8.24（2026-04-08）：修复核心bug - 改用直接API调用替代 openclaw agent --local

功能：
- 读取 all_raw_papers.json
- 分批次（每批20篇）直接调用 MiniMax API 逐篇评分
- 输出 passed.json（≥8分）
- 输出详细评分证据

4维评分标准：
| 维度 | 分值 | 说明 |
|------|------|------|
| Novelty（创新性） | 0-3 | 实质新观点/方法 |
| Significance（重要性） | 0-2 | 问题重要性 |
| Soundness（严谨性） | 0-3 | 方法逻辑自洽 |
| Clarity & Results（清晰度） | 0-2 | 数据支撑 |
| 总分 | 0-10 | ≥8分通过 |
"""

import json, sys, os, time, re, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone

try:
    import urllib.request
    import urllib.error
except ImportError:
    import urllib.request as urllib_request
    import urllib.error

# ========== 配置 ==========
# 动态计算 CST 00:00 UTC 日期（与 search_arxiv_24h.py 一致）
utc_now = datetime.utcnow()
# CST = UTC+8，所以 CST 00:00 = UTC 前一天 16:00
cst_offset = timezone(timedelta(hours=8))
if len(sys.argv) > 1:
    DATE = sys.argv[1]
else:
    # 计算当前 CST 日期（pipeline 运行日）
    today_00_cst_utc = utc_now.replace(hour=16, minute=0, second=0, microsecond=0) - timedelta(days=1)
    DATE = today_00_cst_utc.strftime("%Y-%m-%d")
BASE = f"/home/node/.openclaw/workspace/workareas/shared/papers/{DATE}"
IN_FILE = f"{BASE}/all_raw_papers.json"
OUT_FILE = f"{BASE}/passed.json"
EVIDENCE_FILE = f"{BASE}/phase1b_evidence.json"

BATCH_SIZE = 20
MAX_PARALLEL = 5

# MiniMax API 配置
API_KEY = "sk-cp-CBUQm3M8PXAsAa9zgaNI_zvnsFtXgirPGgOmBF1cYM6fwykMG01aGC-bcouLyWA-SrHtn-Wt87FmqHcRi4NN_it72uqBGEo1grkgyVCYzbqyCgiUUO-wXzw"
API_URL = "https://api.minimaxi.com/anthropic/v1/messages"
MODEL = "MiniMax-M2.7"

LLM_SCORE_PROMPT = """你是一个严格的学术论文评审专家。请对以下论文进行快速初筛评分。

评分标准（4维，总分0-10，≥8分通过）：
- Novelty（创新性）: 0-3分 | 实质新观点/方法
- Significance（重要性）: 0-2分 | 问题重要性
- Soundness（严谨性）: 0-3分 | 方法逻辑自洽
- Clarity & Results（清晰度）: 0-2分 | 数据支撑

论文信息：
标题：{title}
摘要：{summary}
arXiv ID：{arxiv_id}

请对每篇论文输出以下JSON格式（必须严格JSON，无其他文字）：
{{
  "arxiv_id": "{arxiv_id}",
  "title": "{title}",
  "llm_scores": {{
    "novelty": <0-3整数>,
    "significance": <0-2整数>,
    "soundness": <0-3整数>,
    "clarity": <0-2整数>,
    "total": <0-10整数>,
    "reasoning": "<评分理由，20-50字>"
  }},
  "passed": <true或false，total≥8为true>
}}

注意：
1. 必须对每篇论文单独评分，不能批量处理
2. reasoning必须用中文，20-50字
3. 输出必须是可以被json.loads()解析的纯JSON，不要有```json等标记
4. novelty+significance+soundness+clarity的最大值分别为3,2,3,2，总分最大10
"""


def call_minimax_api(prompt, timeout=60, max_retries=3):
    """直接调用 MiniMax API，绕过 openclaw agent --local"""
    payload = {
        "model": MODEL,
        "max_tokens": 800,
        "messages": [{"role": "user", "content": prompt}]
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                API_URL,
                data=json.dumps(payload).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8')
                data = json.loads(raw)
                if data.get('type') == 'error':
                    err = data.get('error', {})
                    print(f"  [API Error] {err.get('type')}: {err.get('message', '')[:100]}", file=sys.stderr)
                    continue
                content_list = data.get('content', [])
                text = ''
                for c in content_list:
                    if c.get('type') == 'text':
                        text = c['text']
                        break
                return text.strip() if text else None

        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8')[:200]
            print(f"  [HTTP {e.code}] {body}", file=sys.stderr)
            if e.code == 429 and attempt < max_retries - 1:
                wait = 30 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
        except Exception as e:
            print(f"  [Attempt {attempt+1}/{max_retries}] {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(10 * (attempt + 1))

    return None


def parse_llm_response(response, arxiv_id):
    """解析 LLM 返回的 JSON 评分结果"""
    if response is None:
        return None

    try:
        text = re.sub(r'```json\s*', '', response)
        text = re.sub(r'```\s*', '', text)
        text = text.strip()
        data = json.loads(text)
        if 'arxiv_id' in data and 'llm_scores' in data:
            return data
    except json.JSONDecodeError:
        pass

    # Fallback：正则提取
    try:
        scores = {}
        for key, pattern in [
            ('novelty', r'novelty["\s:]+([0-9])'),
            ('significance', r'significance["\s:]+([0-9])'),
            ('soundness', r'soundness["\s:]+([0-9])'),
            ('clarity', r'clarity["\s:]+([0-9])'),
            ('total', r'total["\s:]+([0-9]{1,2})'),
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                scores[key] = int(m.group(1))
        if len(scores) >= 4 and 'total' in scores:
            result = {
                'arxiv_id': arxiv_id,
                'title': '',
                'llm_scores': {
                    'novelty': scores.get('novelty', 0),
                    'significance': scores.get('significance', 0),
                    'soundness': scores.get('soundness', 0),
                    'clarity': scores.get('clarity', 0),
                    'total': scores.get('total', 0),
                    'reasoning': f'正则提取: {scores}'
                },
                'passed': scores.get('total', 0) >= 8
            }
            print(f"  [FALLBACK] arXiv {arxiv_id}: 正则提取分数 {scores}", file=sys.stderr)
            return result
    except Exception:
        pass

    print(f"  [WARN] arXiv {arxiv_id}: 响应解析失败", file=sys.stderr)
    return None


def score_single_paper(paper):
    """对单篇论文调用 LLM 评分"""
    arxiv_id = paper.get('arxiv_id', '')
    title = paper.get('title', '')
    summary = paper.get('summary', '')[:1000]

    prompt = LLM_SCORE_PROMPT.format(
        title=title,
        summary=summary,
        arxiv_id=arxiv_id
    )

    response = call_minimax_api(prompt, timeout=60)
    result = parse_llm_response(response, arxiv_id)

    if result:
        return {
            **paper,
            'llm_scores': result['llm_scores'],
            'passed': result['passed']
        }
    else:
        return {
            **paper,
            'llm_scores': {
                'novelty': 0, 'significance': 0,
                'soundness': 0, 'clarity': 0,
                'total': 0, 'reasoning': 'LLM评分失败'
            },
            'passed': False
        }


# ========== 主流程 ==========
def main():
    print(f"=" * 60)
    print(f"Phase1b LLM 评分开始（V8.24 - 直接API调用）")
    sep = "=" * 60; print(sep)

    # 读取论文
    with open(IN_FILE, encoding='utf-8') as f:
        all_papers = json.load(f)
    print(f"📖 读取论文：{len(all_papers)} 篇")

    # 分批
    batches = []
    for i in range(0, len(all_papers), BATCH_SIZE):
        batches.append(all_papers[i:i+BATCH_SIZE])
    total_batches = len(batches)
    print(f"📦 分批：{total_batches} 批，每批 {BATCH_SIZE} 篇")

    all_results = []

    print(f"\n🔄 开始评分（最多 {MAX_PARALLEL} 线程并行）...")
    sep = "=" * 60; print(sep)

    for batch_idx, batch in enumerate(batches):
        print(f"\n📤 批次 [{batch_idx+1}/{total_batches}]，共 {len(batch)} 篇...")

        results = [None] * len(batch)
        completed_count = [0]

        def worker(args):
            idx, paper = args
            scored = score_single_paper(paper)
            completed_count[0] += 1
            done = completed_count[0]
            total = scored.get('llm_scores', {}).get('total', 0)
            passed = scored.get('passed', False)
            status = "✅" if passed else "❌"
            arxiv_id = paper.get('arxiv_id', '?')
            title_short = paper.get('title', '?')[:60]
            print(f"  [{batch_idx+1}/{total_batches}][{done}/{len(batch)}] {arxiv_id}: {total}分 {status} - {title_short}...")
            return idx, scored

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            futures = {executor.submit(worker, (i, p)): i for i, p in enumerate(batch)}
            for future in as_completed(futures):
                try:
                    idx, scored = future.result(timeout=120)
                    results[idx] = scored
                except Exception as e:
                    print(f"  [ERROR] 批次{batch_idx+1}任务失败: {e}", file=sys.stderr)

        # 增量保存
        result_file = f"/tmp/phase1b_batch_{batch_idx}_results.json"
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        passed_in_batch = len([r for r in results if r and r.get('passed')])
        print(f"  💾 批次{batch_idx+1}完成：{passed_in_batch}/{len(results)} 通过")

        all_results.extend(results)

    # 汇总
    passed_papers = [r for r in all_results if r and r.get('passed', False)]
    failed_papers = [r for r in all_results if r and not r.get('passed', False)]

    print(f"\n{'=' * 60}")
    print(f"📊 Phase1b 汇总")
    print(f"   总论文：{len(all_results)} 篇")
    print(f"   通过（≥8分）：{len(passed_papers)} 篇")
    print(f"   未通过（<8分）：{len(failed_papers)} 篇")

    # 保存
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(passed_papers, f, ensure_ascii=False, indent=2)
    print(f"💾 通过论文：{OUT_FILE}")

    with open(EVIDENCE_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"💾 评分证据：{EVIDENCE_FILE}")

    print(f"\n✅ Phase1b 完成：{len(passed_papers)} 篇论文通过初筛（≥8分）")
    return passed_papers


if __name__ == "__main__":
    main()
