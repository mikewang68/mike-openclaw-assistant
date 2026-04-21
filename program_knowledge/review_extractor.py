"""
review_extractor.py
从论文评阅意见（md）中解析出结构化的属性（Properties）
不再提取为 triples，而是直接提取为 JSON Object 挂载于论文节点上
"""

import re
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

def extract_review_properties(review_md_path: str, paper_title: str = None) -> dict:
    """
    从评阅意见 md 文件中提取结构化属性字典
    """
    with open(review_md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 去掉 frontmatter
    content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)

    # 提取论文标题
    if not paper_title:
        title_match = re.search(r"\*\*Title\*\*:\s*(.+)", content)
        if title_match:
            paper_title = title_match.group(1).strip()

    if not paper_title:
        paper_title = os.path.basename(review_md_path).replace("-评阅意见.md", "")

    props = {
        "overall_quality": "",
        "research_topic": "",
        "research_field": "",
        "affiliations": "",
        "authors": "",
        "abstract": "",
        "focus_area": "",
        "focus_problem": "",
        "solution": "",
        "contributions": [],
        "weaknesses": [],
        "keywords": [],
        "scores": {}
    }

    # 1. 提取基础元信息（**Key**: value 格式）
    mapping = {
        "整体质量": "overall_quality",
        "研究主题": "research_topic",
        "研究方向": "research_field",
        "单位": "affiliations",
        "作者": "authors",
        "论文摘要": "abstract",
        "聚焦领域": "focus_area",
        "聚焦问题": "focus_problem",
        "解决方法和技术路线": "solution"
    }

    for key_zh, prop_key in mapping.items():
        pattern = rf"\*\*{re.escape(key_zh)}\*\*:\*?\s*(.+?)(?=\n\*\*|\n##|$)"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            val = match.group(1).strip()
            # 去掉 markdown 链接格式
            val = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', val)
            props[prop_key] = val

    # 2. 关键词
    keywords_match = re.search(r"\*\*Keywords\*\*:\s*(.+?)(?=\n|$)", content)
    if keywords_match:
        kws = [k.strip().rstrip(",，") for k in keywords_match.group(1).split(";")]
        props["keywords"] = [kw for kw in kws if kw]

    # 3. 创新贡献
    contrib_match = re.search(r"\*\*创新贡献\*\*:\s*(.*?)(?=\n\*\*|\n##|$)", content, re.DOTALL)
    if contrib_match:
        raw_contrib = contrib_match.group(1).strip()
        for line in raw_contrib.split("\n"):
            m = re.match(r"^\s*(\d+)[.、、]\s+(.+?)(?:\n|$)", line)
            if m:
                props["contributions"].append(m.group(2).strip().rstrip("："))

    # 4. 不足之处
    weak_match = re.search(r"\*\*不足之处\*\*:\s*(.*?)(?=\n\*\*|\n##|$)", content, re.DOTALL)
    if weak_match:
        raw_weak = weak_match.group(1).strip()
        for line in raw_weak.split("\n"):
            m = re.match(r"^\s*(\d+)[.、]\s+(.+)", line)
            if m:
                props["weaknesses"].append(m.group(2).strip())

    # 5. 五要素评分汇总表格
    # 找形如 | 科学突破性 | 25 | ... 的行
    score_lines = re.findall(r"\|\s*(科学突破性|实验严谨性|理论重构|实用性/风险|逻辑自洽|总分)\s*\|\s*(\d+)\s*\|", content)
    for cat, score in score_lines:
        props["scores"][cat] = int(score)

    return props

def extract_review_triples(review_md_path: str, paper_title: str = None) -> list:
    """旧接口，返回空列表，彻底阻断产生的污染三元组写入知识图谱边缘"""
    return []

if __name__ == "__main__":
    import json
    import glob
    review_dir = "/obsidian/02_Output/04_论文"
    reviews = glob.glob(os.path.join(review_dir, "*评阅意见.md"))
    if reviews:
        print(f"Test extracting from {reviews[0]}:")
        props = extract_review_properties(reviews[0])
        print(json.dumps(props, indent=2, ensure_ascii=False))
