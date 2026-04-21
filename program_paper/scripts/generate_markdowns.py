#!/usr/bin/env python3
import json
import os
import sys

TEMPLATE_PATH = "/obsidian/00_Auxiliary/03_Prompt/paper-prompt.md"
OUTPUT_DIR = "/obsidian/02_Output/04_论文"
SHARED_DIR = "/home/node/.openclaw/workspace/workareas/shared/papers/2026-03-24"
DATE = "2026-03-24"

with open(TEMPLATE_PATH, "r") as f:
    template = f.read()

papers = [
    ("2603.19061", "2026-03-24-2603.19061-评阅意见.pdf"),
    ("2603.21909", "2026-03-24-A_Novel_Method_for_Enforcing_Exactly.pdf"),
    ("2603.22075", "2026-03-24-2603.22075-评阅意见.pdf"),
    ("2603.22260", "2026-03-24-2603.22260-评阅意见.pdf"),
]

for arxiv_id, pdf_name in papers:
    reviewer_path = os.path.join(SHARED_DIR, f"{arxiv_id}_reviewer.json")
    coach_path = os.path.join(SHARED_DIR, f"{arxiv_id}_coach.json")

    with open(reviewer_path) as f:
        reviewer = json.load(f)
    with open(coach_path) as f:
        coach = json.load(f)

    part1 = reviewer.get("part1", {})
    part2 = reviewer.get("part2", {})
    part3 = reviewer.get("part3", {})
    part4 = coach.get("part4", {})
    part5 = coach.get("part5", {})

    tags = ", ".join(part1.get("tags", []))
    title = part1.get("title", "N/A")
    keywords = ", ".join(part1.get("keywords", []))
    source_wiki = f"[[{pdf_name}]]"

    # Build Part 1
    part1_text = f"""## 第一部分：基础信息
**Tags**: [{tags}]
**Title**: {title}
**Keywords**: [{keywords}]
**Date**: {part1.get('date', DATE)}
**source**: {source_wiki}"""

    # Build Part 2
    scores = part2.get("scores", {})
    total = scores.get("total_100", "N/A")
    oq = part2.get("overall_quality", "N/A")

    # Determine quality label
    if isinstance(total, int):
        if total >= 90:
            quality_label = "优秀/Excellent"
        elif total >= 80:
            quality_label = "良好/Good"
        elif total >= 70:
            quality_label = "可投稿/Acceptable"
        elif total >= 60:
            quality_label = "返修/Revision Required"
        else:
            quality_label = "拒稿/Reject"
    else:
        quality_label = "N/A"

    # Extract part2 fields
    research_topic = part2.get("research_topic", "N/A")
    affiliation = part2.get("affiliation", "N/A")
    authors = part2.get("authors", "N/A")
    paper_abstract = part2.get("paper_abstract", "N/A")
    focused_area = part2.get("focused_area", "N/A")
    focused_problem = part2.get("focused_problem", "N/A")
    technical_approach = part2.get("technical_approach", "N/A")
    experimental_design = part2.get("experimental_design", "N/A")
    experimental_results = part2.get("experimental_results", "N/A")
    innovations = part2.get("innovations", [])
    limitations = part2.get("limitations", [])
    journal_level = part2.get("journal_level", "N/A")
    journal_suggestion = part2.get("journal_suggestion", "N/A")
    summary = part2.get("summary", "N/A")

    innovations_text = "\n".join([f"- {i}" for i in innovations]) if innovations else "- N/A"
    limitations_text = "\n".join([f"- {i}" for i in limitations]) if limitations else "- N/A"

    breakthrough = scores.get("breakthrough", "N/A")
    rigor = scores.get("rigor", "N/A")
    theory = scores.get("theory", "N/A")
    practical = scores.get("practical", "N/A")
    logic = scores.get("logic", "N/A")
    total_50 = scores.get("total_50", "N/A")

    part2_text = f"""## 第二部分：整体评价
- **整体质量**：{oq}（{quality_label}）
- **研究主题**：{research_topic}
- **研究方向**：{part2.get('focused_area', 'N/A')}
- **单位**：{affiliation}
- **作者**：{authors}
- **论文摘要**：{paper_abstract}
- **聚焦领域**：{focused_area}
- **聚焦问题**：{focused_problem}
- **技术路线**：{technical_approach}
- **实验设计**：{experimental_design}
- **实验结果**：{experimental_results}
- **创新贡献**：{innovations_text}
- **不足之处**：{limitations_text}
- **适合投稿期刊的级别**：{journal_level}
- **投稿期刊的建议**：{journal_suggestion}
- **总结**：{summary}

### 五要素评分汇总

|   维度   |  得分  |   满分    | 百分比 | 得分理由         |
| :----: | :--: | :-----: | :--: | :----------- |
| 科学突破性  |  {breakthrough}  |   30    |     |              |
| 实验严谨性  |  {rigor}  |   25    |     |              |
|  理论重构  |  {theory}  |   15    |     |              |
| 实用性/风险 |  {practical}  |   15    |     |              |
|  逻辑自洽  |  {logic}  |   15    |     |              |
| **总分** |  {total_50}  | **100** |     | [{quality_label}] |"""

    # Build Part 3
    if part3:
        dcm = part3.get("domain_criteria_match", {})
        lca = part3.get("logic_chain_audit", {})
        iat = part3.get("interpretability_transparency", {})
        eest = part3.get("experimental_efficiency_stress_test", {})
        gr = part3.get("generalization_robustness", {})

        part3_text = f"""## 第三部分：五维深层审讯

- **领域动态准则匹配**：{dcm.get('analysis', 'N/A')}
    
- **逻辑链条审计**：{lca.get('analysis', 'N/A')}

- **可解释性透明度**：{iat.get('analysis', 'N/A')}
    
- **实验效能压力测试**：{eest.get('analysis', 'N/A')}
    
- **泛化性与鲁棒性验证**：{gr.get('analysis', 'N/A')}"""
    else:
        part3_text = "## 第三部分：五维深层审讯\n\n- **领域动态准则匹配**：N/A\n- **逻辑链条审计**：N/A\n- **可解释性透明度**：N/A\n- **实验效能压力测试**：N/A\n- **泛化性与鲁棒性验证**：N/A"

    # Build Part 4
    p0 = part4.get("p0_mandatory", [])
    p1 = part4.get("p1_enhancement", [])
    p2 = part4.get("p2_polishing", [])
    audit_conclusion = part4.get("audit_conclusion", "N/A")

    p0_text = ""
    if p0:
        for item in p0:
            p0_text += f"\n- **Coach 动作**: {item.get('coach_action', 'N/A')}\n"
    else:
        p0_text = "\n无"

    p1_text = ""
    if p1:
        for item in p1:
            p1_text += f"\n- **Coach 动作**: {item.get('coach_action', 'N/A')}\n"
    else:
        p1_text = "\n无"

    p2_text = ""
    if p2:
        for item in p2:
            p2_text += f"\n- **Coach 动作**: {item.get('coach_action', 'N/A')}\n"
    else:
        p2_text = "\n无"

    part4_text = f"""## 第四部分：阶梯式修改建议 (Coach 执行指令)
### 1. 【高优先级 / 硬伤】 (不改必拒)
{p0_text}
### 2. 【中优先级 / 逻辑增强】 (提升档次)
{p1_text}
### 3. 【低优先级 / 细节规范】 (印象加分)
{p2_text}
### 4. 【个人占比检查 (70%) & 合规审计】
- **审计结论**：{audit_conclusion}"""

    # Build Part 5
    restructure_items = part5.get("restructure_items", [])
    if restructure_items:
        ri_texts = []
        for item in restructure_items:
            loc = item.get("location", "N/A")
            orig = item.get("original_text", "N/A")
            hr = item.get("high_level_restructure", "N/A")
            rr = item.get("restructure_reason", "N/A")
            ri_texts.append(f"""- **重构位置**: {loc} (原文语种)\n  - **高级重构范式**: {hr}\n  - **重构理由**: {rr}""")
        part5_text = "## 第五部分：## 范式重构 (Nature/TPAMI/CVPR 风格打磨)\n\n" + "\n\n".join(ri_texts)
    else:
        part5_text = "## 第五部分：## 范式重构 (Nature/TPAMI/CVPR 风格打磨)\n\n无重构建议。"

    # Combine
    content = f"""# {title}

{part1_text}

---

{part2_text}

---

{part3_text}

---

{part4_text}

---

{part5_text}
"""

    # Output filename: date + first 30 chars of title + 评阅意见.md
    safe_title = title[:30].replace(" ", "_").replace("/", "_").replace("\\", "_")
    safe_title = "".join(c for c in safe_title if c.isalnum() or c in "._-")
    output_name = f"{DATE}-{safe_title}-评阅意见.md"
    output_path = os.path.join(OUTPUT_DIR, output_name)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Generated: {output_path}")
    print(f"  Size: {os.path.getsize(output_path)} bytes")
