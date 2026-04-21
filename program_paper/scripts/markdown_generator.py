#!/usr/bin/env python3
"""Markdown Generator for Paper Review - 严格按 paper-prompt.md 模板"""

import json
import os
import re
import sys
from pathlib import Path

def load_json(path):
    with open(path) as f:
        return json.load(f)

def generate_markdown(rj_path, cj_path, out_dir, pdf_override=None, date_override=None, arxiv_id_override=None, author_override=None):
    """生成 Markdown
    
    Args:
        author_override: 个人论文时使用作者姓名作为文件名（如 "北方工业"），
                        不使用title生成的文件名
    """
    r = load_json(rj_path)
    c = load_json(cj_path)
    
    # reviewer JSON 可能直接是 {part1, part2, part3} 或包装在 reviewer key 下
    reviewer_data = r.get('reviewer', r)
    coach_data = c.get('coach', c)
    
    # ===== Part1 - 论文基础信息 =====
    p1 = reviewer_data.get('part1', {})
    title = p1.get('title', '未知标题')
    tags = p1.get('tags', [])
    # date_for_filename：输出文件名用 date_override（pipeline运行日期）
    # date_for_markdown：markdown 内部的 **Date** 用论文提交日期（p1.date）
    p1_date = p1.get('date', '2026-03-28')
    date_for_markdown = p1_date  # **Date** 字段 = 论文提交日期
    date_dashed = (date_override if date_override else p1_date).replace('/', '-').replace('_', '-')
    date_for_source = date_dashed + '-'  # source 字段用 YYYY-MM-DD-（日期后有trailing hyphen）
    keywords = p1.get('keywords', [])
    
    # PDF 文件名：优先从pdf_override提取date和title（确保三处一致：PDF名、Markdown名、source字段）
    # 算法：先截断30字符，再统一替换所有非字母数字为下划线
    def safe_name(s):
        return re.sub(r'[^a-zA-Z0-9]', '_', s[:30])

    # 统一使用 reviewer JSON 的 title 生成标准文件名
    # date 格式：YYYY-MM-DD（用连字符），title 截断30字符后转下划线
    # 注意：pdf_file 必须与 curl -o 保存的文件名完全一致（date-title之间用 hyphen）
    title_safe = safe_name(title)
    
    # 个人论文（author_override）使用作者姓名作为文件名基础，不使用title
    if author_override:
        filename_base = author_override  # 如 "北方工业"
        pdf_file = f"{date_dashed}-{author_override}.pdf"
    else:
        filename_base = title_safe
        pdf_file = f"{date_dashed}-{title_safe}.pdf"
    
    # source 字段：日期后用 trailing hyphen（2026-03-14-Title.pdf），与 PDF 文件名格式（2026-03-14_Title.pdf）区分
    if author_override:
        source_pdf = f"{date_dashed}-{author_override}.pdf"  # 个人论文：日期+author.pdf
    else:
        source_pdf = f"{date_for_source}{title_safe}.pdf"   # arXiv论文：日期-标题.pdf
    tags_str = ', '.join(tags) if tags else ''
    keywords_str = '; '.join(keywords) if keywords else ''
    
    part1 = f"""## 第一部分：基础信息

**Tags**: {tags_str}

**Title**: {title}

**Keywords**: {keywords_str}

**Date**: {date_for_markdown}

**source**: [[{source_pdf}]]
"""

    # ===== Part2 - 整体评价 =====
    p2 = reviewer_data.get('part2', {})
    
    overall_quality = p2.get('overall_quality', 'N/A')
    research_topic = p2.get('research_topic', 'N/A')
    research_direction = p2.get('research_direction', 'N/A')
    institution = p2.get('institution', 'N/A')
    authors = p2.get('authors', 'N/A')
    paper_abstract = p2.get('paper_abstract', 'N/A')
    focused_area = p2.get('focused_area', 'N/A')
    focused_problem = p2.get('focused_problem', 'N/A')
    technical_route = p2.get('technical_route', 'N/A')
    experimental_design = p2.get('experimental_design', 'N/A')
    experimental_results = p2.get('experimental_results', 'N/A')
    journal_suggestion = p2.get('journal_suggestion', 'N/A')
    summary = p2.get('summary', 'N/A')
    suitable_journal_level = p2.get('suitable_journal_level', 'N/A')
    
    # 创新贡献 - 列表格式
    innovations = p2.get('innovation_contributions', [])
    if innovations:
        if isinstance(innovations, list):
            innovations_str = '\n'.join([f" {i+1}. {inn}" for i, inn in enumerate(innovations)])
        else:
            innovations_str = str(innovations)
    else:
        innovations_str = '无'
    
    # 不足之处 - 列表格式
    weaknesses = p2.get('weaknesses', [])
    if weaknesses:
        if isinstance(weaknesses, list):
            weaknesses_str = '\n'.join([f" {i+1}. {w}" for i, w in enumerate(weaknesses)])
        else:
            weaknesses_str = str(weaknesses)
    else:
        weaknesses_str = '无'
    
    # 五要素评分 - 计算总分并生成表格
    scores = p2.get('scores', [])
    total = 0
    max_total = 0
    score_rows = []
    for s in scores:
        if isinstance(s, dict) and 'dimension' in s:
            dim = s.get('dimension', 'N/A')
            score = s.get('score', 0)
            max_s = s.get('max', 0)
            pct = s.get('percentage', 'N/A')
            reason = s.get('reason', '')
            score_rows.append(f"| {dim} | {score} | {max_s} | {pct} | {reason} |")
            total += score
            max_total += max_s
    
    # 找到 level
    level = overall_quality
    for s in scores:
        if isinstance(s, dict) and 'level' in s:
            level = s.get('level', overall_quality)
            break
    
    pct_total = f'{int(total/max_total*100)}%' if max_total else 'N/A'
    scores_table = '\n'.join(score_rows)
    total_row = f"| 总分 | {total} | {max_total} | {pct_total} | {level} |"
    
    part2 = f"""## 第二部分：整体评价

**整体质量**: {overall_quality}

**研究主题**: {research_topic}

**研究方向**: {research_direction}

**单位**: {institution}

**作者**: {authors}

**论文摘要**: {paper_abstract}

**聚焦领域**: {focused_area}

**聚焦问题**: {focused_problem}

**解决方法和技术路线**: {technical_route}

**实验设计**: {experimental_design}

**实验结果**: {experimental_results}

**创新贡献**: 
{innovations_str}

**不足之处**:
{weaknesses_str}

**适合投稿期刊的级别**: {suitable_journal_level}

**投稿期刊的建议**: {journal_suggestion}

**总结**: {summary}

### 五要素评分汇总

| 维度 | 得分 | 满分 | 百分比 | 得分理由 |
|:---:|:---:|:---:|:---:|:---|
{scores_table}
| 总分 | {total} | {max_total} | {pct_total} | {level} |
"""

    # ===== Part3 - 五维深层审讯 =====
    p3 = reviewer_data.get('part3', {})
    if p3:
        domain = p3.get('domain_criteria_match', 'N/A')
        logic = p3.get('logic_chain_audit', 'N/A')
        interpret = p3.get('interpretability_transparency', 'N/A')
        exp_eff = p3.get('experimental_efficiency_stress_test', 'N/A')
        gen_rob = p3.get('generalization_robustness', 'N/A')
        
        part3 = f"""## 第三部分：五维深层审讯

**领域动态准则匹配**: {domain}

**逻辑链条审计**: {logic}

**可解释性透明度**: {interpret}

**实验效能压力测试**: {exp_eff}

**泛化性与鲁棒性**: {gen_rob}
"""
    else:
        part3 = ""

    # ===== Part4 - 阶梯式修改建议 =====
    # coach_data 已处理 {'coach': {...}} 的情况，直接使用
    coach = coach_data if isinstance(coach_data, dict) else {}
    p4 = coach.get('part4', {}) if isinstance(coach, dict) else {}
    p5 = coach.get('part5', {}) if isinstance(coach, dict) else {}
    
    p0_list = p4.get('p0_hard_fixes_impossible_to_accept_without', []) or p4.get('p0_issues', []) or p4.get('p0', []) if isinstance(p4, dict) else []
    p1_list = p4.get('p1_logical_enhancements_needed_for_top_tier', []) or p4.get('p1_issues', []) or p4.get('p1', []) if isinstance(p4, dict) else []
    p2_list = p4.get('p2_polish_and_formatting_details', []) or p4.get('p2_issues', []) or p4.get('p2', []) if isinstance(p4, dict) else []
    rest_items = p5.get('restructure_items', []) if isinstance(p5, dict) else []
    
    def format_items(items):
        if not items:
            return '无'
        result = []
        for i, item in enumerate(items):
            if isinstance(item, dict):
                issue = item.get('issue', '')
                location = item.get('location', '')
                action = item.get('coach_action', '')
                if result:
                    result.append('')
                result.append(f"**【问题{i+1}】** {issue}")
                result.append(f"**【位置】** {location}")
                result.append(f"**【修改建议】** {action}")
            elif isinstance(item, str):
                if result:
                    result.append('')
                result.append(f"**【问题{i+1}】** {item}")
        return '\n'.join(result)
    
    p0_str = format_items(p0_list)
    p1_str = format_items(p1_list)
    p2_str = format_items(p2_list)
    
    compliance = p4.get('compliance_audit', {}) if isinstance(p4, dict) else {}
    if isinstance(coach_data, dict):
        cp4 = coach_data.get('part4', {})
        compliance = cp4.get('compliance_audit', {}) if isinstance(cp4, dict) else compliance
    
    if isinstance(compliance, dict):
        author_contribution = compliance.get('author_contribution', '无')
        gdpr_privacy = compliance.get('gdpr_privacy', '无')
        ethical_compliance = compliance.get('ethical_compliance', '无')
        data_availability = compliance.get('data_availability', '无')
        code_availability = compliance.get('code_availability', '无')
    else:
        author_contribution = gdpr_privacy = ethical_compliance = data_availability = code_availability = '无'
    
    part4 = f"""## 第四部分：阶梯式修改建议 (Coach 执行指令)

### 1. 【高优先级 / 硬伤】 (不改必拒)

{p0_str}

### 2. 【中优先级 / 逻辑增强】 (提升档次)

{p1_str}

### 3. 【低优先级 / 细节规范】 (印象加分)

{p2_str}

### 4. 【个人占比检查 (70%) & 合规审计】

**作者贡献占比**: {author_contribution}

**GDPR/隐私合规性**: {gdpr_privacy}

**伦理审查**: {ethical_compliance}

**数据可用性声明**: {data_availability}

**代码可用性声明**: {code_availability}
"""

    # ===== Part5 - 范式重构 =====
    p5 = coach.get('part5', {}) if isinstance(coach, dict) else {}
    rest_items = p5.get('restructure_items', []) if isinstance(p5, dict) else []
    
    if rest_items:
        rest_lines = []
        for i, item in enumerate(rest_items):
            if isinstance(item, dict):
                loc = item.get('location', 'N/A')
                orig = item.get('original_text', '')
                new_text = item.get('restructured_text', '')
                reason = item.get('reason', '')
                if rest_lines:
                    rest_lines.append('')
                rest_lines.append(f" {i+1}. **位置**: {loc}")
                rest_lines.append(f"    **原文**: {orig}...")
                rest_lines.append(f"    **重构**: {new_text}...")
                rest_lines.append(f"    **理由**: {reason}")
            elif isinstance(item, str):
                if rest_lines:
                    rest_lines.append('')
                rest_lines.append(f" {i+1}. {item}")
        rest_str = '\n'.join(rest_lines)
    else:
        rest_str = '无'
    
    part5 = f"""## 第五部分：范式重构 (Nature/TPAMI/CVPR 风格打磨)

{rest_str}
"""

    # ===== 组合并写入文件 =====
    parts = [part1, part2, part3, part4, part5]
    md = '\n\n'.join(parts)
    
    # 生成固定的文件名 - 与V8.0一致
    # Markdown文件名必须与PDF文件名一致（Mike要求：{PDF文件名}-评阅意见.md）
    # 个人论文使用 author_override 作为文件名基础
    out_path = Path(out_dir) / f"{date_dashed}-{filename_base}-评阅意见.md"
    
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(md)
    
    return str(out_path), pdf_file

if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: python3 markdown_generator.py <reviewer_json> <coach_json> <output_dir> [pdf_override] [date_override] [arxiv_id_override]")
        sys.exit(1)
    
    rj = sys.argv[1]
    cj = sys.argv[2]
    out_dir = sys.argv[3]
    pdf_override = sys.argv[4] if len(sys.argv) > 4 and os.path.isfile(sys.argv[4]) else None
    date_override = sys.argv[5] if len(sys.argv) > 5 else None
    arxiv_id_override = sys.argv[6] if len(sys.argv) > 6 else None
    author_override = sys.argv[7] if len(sys.argv) > 7 else None
    
    result, pdf = generate_markdown(rj, cj, out_dir, pdf_override, date_override, arxiv_id_override, author_override)
    print(f"Generated: {result}")
    print(f"PDF: {pdf}")
