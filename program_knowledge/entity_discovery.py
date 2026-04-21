"""
entity_discovery.py — 新实体 LLM 自动发现与分类

职责：
  1. 从论文文本提取候选实体（摘要高频词 + 关键字 + 正文多次提及）
  2. 生成 LLM 消歧 prompt（JSON格式）
  3. 将 LLM 判断结果写入 YAML pending_review

用法（由 Main Agent 调用）：
  from entity_discovery import extract_candidates, append_judgments
  candidates = extract_candidates(title, abstract, keywords, body_text)
  prompt = build_discovery_prompt(title, abstract, candidates, yaml_path)
  # → Main Agent 将 prompt 发给 LLM
  # → 获取 LLM 响应后：
  append_judgments(paper_id, llm_response_json, yaml_path, dry_run=False)
"""

import re
import sys
import yaml
import json
from typing import Optional

YAML_PATH = "/obsidian/00_Auxiliary/06_YAML/entity_map.yaml"

# ─────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    return re.sub(r"[-_\s]+", " ", text.lower().strip())

def load_yaml(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None

def extract_entities_from_text(text: str, min_occurrences: int = 3) -> list[dict]:
    """
    从正文中提取高频实体候选。
    返回：[{"entity": str, "source": str, "count": int}, ...]
    """
    if not text:
        return []
    
    # 提取 CamelCase 连续短语
    camel = re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', text)
    
    # 提取 Title Case 词组（2-4个单词）
    title_phrases = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b', text)
    
    freq = {}
    for e in camel + title_phrases:
        freq[e] = freq.get(e, 0) + 1
    
    return sorted(
        [{"entity": e, "source": "body", "count": c} for e, c in freq.items() if c >= min_occurrences],
        key=lambda x: -x["count"]
    )

def extract_entities_from_abstract(abstract: str) -> list[dict]:
    """
    从摘要提取实体（1次即可，优先采集）。
    """
    if not abstract:
        return []
    
    camel = re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', abstract)
    title_phrases = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b', abstract)
    
    stopwords = {
        'Method', 'Model', 'System', 'Framework', 'Approach',
        'Algorithm', 'Network', 'Learning', 'Training',
        'Data', 'Results', 'Performance', 'Evaluation',
        'Related Work', 'Previous Work', 'Our Work', 'This Paper',
        'The Results', 'In This Paper', 'We Propose', 'Paper',
        'Table', 'Figure', 'Section', 'Equation', 'Problem',
        'Introduction', 'Conclusion', 'References', 'Abstract',
        # 摘要常见误提取
        'Unlike', 'Unlike Traditional', 'Unlike Other',
        'Unlike Previous', 'Unlike Prior', 'Unlike Existing',
        'Unlike Conventional', 'Unlike Standard',
        'Compared To', 'Compared With', 'Different From',
        'In Contrast', 'However', 'Moreover', 'Furthermore',
        'Therefore', 'Thus', 'Hence', 'Consequently',
        'Specifically', 'Particularly', 'Especially', 'Notably',
        'Indeed', 'Actually', 'Essentially', 'Basically',
        'Traditionally', 'Conventionally', 'Previously', 'Currently',
        'Recently', 'Traditionally', 'Initially', 'Finally',
        'We Propose', 'We Present', 'We Describe', 'We Introduce',
        'We Show', 'We Demonstrate', 'We Evaluate', 'We Compare',
        'We Introduce', 'We Develop', 'We Design', 'We Build',
        'We Find', 'We Observe', 'We Note', 'We Believe',
        'One Challenge', 'Key Challenge', 'Main Challenge',
        'Major Challenge', 'Central Challenge',
        'First', 'Second', 'Third', 'Finally',
        'New', 'Novel', 'Efficient', 'Effective',
        'Improved', 'Advanced', 'State-of-the-art',
        'Benchmark', 'Baseline', 'Standard',
    }
    
    entities = []
    for e in set(camel + title_phrases):
        if e not in stopwords and len(e) > 3:
            entities.append({"entity": e, "source": "abstract", "count": 1})
    
    return entities

def extract_entities_from_keywords(keywords: list[str]) -> list[dict]:
    """从关键字列表提取实体"""
    if not keywords:
        return []
    stopwords = {'method', 'model', 'system', 'framework', 'approach',
                 'algorithm', 'network', 'learning', 'training'}
    entities = []
    for kw in keywords:
        kw = kw.strip()
        if kw.lower() not in stopwords and len(kw) > 2:
            entities.append({"entity": kw, "source": "keywords", "count": 999})
    return entities

def build_canonical_index(yaml_config: dict) -> dict:
    """构建方向 → canonicals 索引"""
    index = {}
    for direction in ["ML", "LLM", "Blockchain", "Quant", "DASecurity"]:
        if direction in yaml_config:
            entries = yaml_config[direction].get("canonical_aliases", [])
            index[direction] = [e["canonical"] for e in entries]
    return index

def build_discovery_prompt(
    paper_title: str,
    paper_abstract: str,
    candidates: list[dict],
    yaml_path: str = YAML_PATH
) -> str:
    """
    构造 LLM 批量消歧 prompt。
    candidates: [{"entity": str, "source": str, "count": int}, ...]
    """
    yaml_config = load_yaml(yaml_path) or {}
    canonical_index = build_canonical_index(yaml_config)
    
    direction_descs = []
    for direction, canonicals in canonical_index.items():
        preview = ', '.join(canonicals[:12])
        suffix = ' ...' if len(canonicals) > 12 else ''
        direction_descs.append(f"  [{direction}]: {preview}{suffix}")
    
    entity_lines = []
    for i, c in enumerate(candidates[:30], 1):  # 最多30个
        src_tag = c["source"]
        if src_tag == "keywords":
            entity_lines.append(f"  {i}. \"{c['entity']}\" ⭐(关键字)")
        elif src_tag == "abstract":
            entity_lines.append(f"  {i}. \"{c['entity']}\" (摘要)")
        else:
            entity_lines.append(f"  {i}. \"{c['entity']}\" (正文提及{c['count']}次)")
    
    prompt = f"""## 任务：学术论文新实体消歧

你是一个知识图谱专家。判断以下论文中的未知学术术语最可能属于哪个研究方向。

### 论文信息
- 标题：{paper_title}
- 摘要：{paper_abstract[:600] if paper_abstract else '(无)'}

### 研究方向定义（共5个）
{chr(10).join(direction_descs)}

### 待消歧术语（共 {len(candidates)} 个，按重要性排序）
{chr(10).join(entity_lines)}

### 判断规则
1. 术语已在上方研究方向中定义 → 填写对应 canonical 名称
2. 术语语义与某 canonical 高度相关（≥0.9相似）→ 填写最接近的 canonical
3. 术语是某论文特有的新方法名（如 "MagicFF", "FastAttention"）→ 填写 "PAPER_SPECIFIC"
4. 术语是未知缩写（FHE/CKKS/xxx）但能从上下文推断 → 填写推断的 canonical
5. 术语完全陌生、无法判断 → 填写 "UNKNOWN"

注意：
- "FHE", "CKKS", "MPC", "RLHF" 等已知缩写 → 应匹配到对应 canonical（如 "Homomorphic Encryption"）
- 大小写/标点不同但语义相同 → 应合并（如 "Image Classification" → "Image Classification"）
- 通用词（Privacy, Security, Distributed）→ 结合上下文判断方向

### 输出格式（JSON array，严格包含30条）：
```json
[
  {{"entity": "原始术语", "canonical": "判断结果", "confidence": 0.0-1.0, "reasoning": "1句话原因"}},
  ...
]
```
JSON array:
"""
    return prompt

def parse_llm_response(raw: str) -> list[dict]:
    """解析 LLM JSON 响应"""
    # 提取 ```json ... ``` 块
    match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", raw)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    # 尝试直接解析
    try:
        return json.loads(raw)
    except Exception:
        return []

# ─────────────────────────────────────────────────────────
# 候选实体提取（纯函数，Main Agent 调用）
# ─────────────────────────────────────────────────────────

def extract_candidates(
    title: str,
    abstract: str,
    keywords: list[str],
    body_text: str
) -> list[dict]:
    """
    从论文各部分提取候选实体，合并去重。
    返回：[{"entity": str, "source": str, "count": int}, ...]
    按 source 优先级排序（keywords > abstract > body）。
    """
    kw_entities = extract_entities_from_keywords(keywords)
    abs_entities = extract_entities_from_abstract(abstract)
    body_entities = extract_entities_from_text(body_text, min_occurrences=3)[:10]
    
    seen = {}
    for e in kw_entities:
        seen[e["entity"]] = e
    for e in abs_entities:
        if e["entity"] not in seen:
            seen[e["entity"]] = e
    for e in body_entities:
        if e["entity"] not in seen:
            seen[e["entity"]] = e
    
    # 按优先级和频率排序
    priority = {"keywords": 0, "abstract": 1, "body": 2}
    result = list(seen.values())
    result.sort(key=lambda x: (priority.get(x["source"], 3), -x["count"]))
    return result

# ─────────────────────────────────────────────────────────
# LLM 判断结果写入 YAML（Main Agent 调用）
# ─────────────────────────────────────────────────────────

def append_judgments(
    paper_id: str,
    llm_response,  # str 或 list
    yaml_path: str = YAML_PATH,
    dry_run: bool = False
) -> dict:
    """
    解析 LLM 响应，将结果写入 YAML pending_review 区。
    
    llm_response: LLM 返回的原始文本（或已解析的 list）
    返回：{"added": int, "duplicates": int, "errors": int}
    """
    if isinstance(llm_response, str):
        judgments = parse_llm_response(llm_response)
    elif isinstance(llm_response, list):
        judgments = llm_response
    else:
        judgments = []
    
    if not judgments:
        return {"added": 0, "duplicates": 0, "errors": 1, "note": "no judgments parsed"}
    
    config = load_yaml(yaml_path) or {}
    if "pending_review" not in config:
        config["pending_review"] = []
    
    existing_entities = {e["entity"] for e in config["pending_review"]}
    
    added = 0
    duplicates = 0
    
    new_entries = []
    for j in judgments:
        entity = j.get("entity", "").strip()
        canonical = j.get("canonical", "").strip()
        confidence = float(j.get("confidence", 0.0))
        reasoning = j.get("reasoning", "")[:100]
        
        if not entity or not canonical:
            continue
        if entity in existing_entities:
            duplicates += 1
            continue
        
        new_entries.append({
            "entity": entity,
            "suggested_canonical": canonical,
            "confidence": round(confidence, 2),
            "reasoning": reasoning,
            "paper_id": paper_id,
            "status": "pending"
        })
        existing_entities.add(entity)
        added += 1
    
    if new_entries and not dry_run:
        config["pending_review"].extend(new_entries)
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, sort_keys=False)
    
    return {"added": added, "duplicates": duplicates, "entries": new_entries}

# ─────────────────────────────────────────────────────────
# CLI 测试入口
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="新实体发现（CLI测试）")
    parser.add_argument("--title", required=True)
    parser.add_argument("--abstract", default="")
    parser.add_argument("--keywords", default="", help="逗号分隔")
    parser.add_argument("--body", default="")
    parser.add_argument("--yaml", default=YAML_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    
    print(f"📄 论文：{args.title}")
    print(f"🔍 提取候选实体...")
    
    candidates = extract_candidates(args.title, args.abstract, keywords, args.body)
    print(f"   发现 {len(candidates)} 个候选实体：")
    for c in candidates[:10]:
        print(f"   - [{c['source']}] {c['entity']} (count={c['count']})")
    
    print(f"\n📝 生成 LLM prompt...")
    prompt = build_discovery_prompt(args.title, args.abstract, candidates, args.yaml)
    print(prompt[:500])
    print("...(truncated)")
