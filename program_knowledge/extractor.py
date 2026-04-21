"""
Triple 抽取器
通过 OpenClaw subagent 调用 LLM 从 Markdown 文本中抽取三元组
"""

import os
import json
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from config import MD_DIR

# 提取 prompt 模板
PROMPT_FILE = "/obsidian/00_Auxiliary/03_Prompt/extract-prompt.md"

def load_prompt_template() -> str:
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read()


def extract_triples_from_md(md_path: str, llm_output: str) -> list:
    """
    从 LLM 输出中解析 JSON triples
    支持：```json 代码块 / 裸 JSON 数组 / JSON within text
    """
    text = llm_output.strip()
    
    # 去掉 markdown 代码块
    text_no_fence = re.sub(r"(?:^|\n)```json\s*", "", text)
    text_no_fence = re.sub(r"(?:^|\n)```\s*", "", text_no_fence)
    text_no_fence = re.sub(r"\s*```(?:$|\n)", "", text_no_fence)
    text_no_fence = text_no_fence.strip()
    
    # 方法1：直接解析（处理裸JSON数组）
    if text_no_fence.startswith("["):
        try:
            return json.loads(text_no_fence)
        except json.JSONDecodeError:
            pass
    
    # 方法2：处理 ```json ... ``` 包裹的情况
    try:
        return json.loads(text_no_fence)
    except json.JSONDecodeError:
        pass
    
    # 方法3：查找 JSON 数组（带换行/缩进的原始格式）
    # 匹配 [ { ... } ] 形式
    match = re.search(r'\[\s*\{', text)
    if match:
        start = match.start()
        # 从第一个 [{ 往后找到匹配的 ]
        bracket_count = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                bracket_count += 1
            elif text[i] == '}':
                bracket_count -= 1
                if bracket_count == 0:
                    candidate = text[start:i+1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
    
    print(f"⚠️ JSON 解析失败，原始输出前200字符: {text[:200]}")
    return []


def build_extraction_prompt(md_text: str, paper_title: str) -> str:
    """构建给 LLM 的抽取 prompt"""
    template = load_prompt_template()
    
    # 截断过长的文本（LLM context limit）
    max_chars = 80000
    if len(md_text) > max_chars:
        md_text = md_text[:max_chars] + "\n\n[... 内容截断 ...]"
    
    prompt = f"""{template}

## 待抽取文本

文件名：{paper_title}

---
{md_text}
---
"""
    return prompt


def main():
    """
    手动测试用：从 MD_DIR 读取所有 md 文件并调用 LLM 抽取
    （实际运行时由 pipeline.py 调用 extract_triples_via_subagent）
    """
    import glob
    
    md_files = glob.glob(os.path.join(MD_DIR, "*.md"))
    if not md_files:
        print("No MD files found in", MD_DIR)
        return
    
    print(f"Found {len(md_files)} MD files")
    for md_path in md_files:
        print(f"\n{md_path}")
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
        print(f"  Content length: {len(content)} chars")


if __name__ == "__main__":
    main()
