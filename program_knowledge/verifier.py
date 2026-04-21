"""
Verifier - 推理验证器
用 LLM 验证 inferred triples 的可靠性
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))
from config import *


def build_verify_prompt(triple: dict, context_triples: list = None) -> str:
    """构建验证 prompt"""
    
    context_text = ""
    if context_triples:
        context_text = "\n\n相关已有知识：\n"
        for ct in context_triples[:5]:
            context_text += f"- {ct['subject']} --{ct['relation']}--> {ct['object']}\n"
    
    prompt = f"""你是一个知识验证系统。请判断以下推理是否合理。

## 待验证推理

主体（Subject）: {triple['subject']}
关系（Relation）: {triple['relation']}
客体（Object）: {triple['object']}

推理来源: {json.dumps(triple.get('source', []), ensure_ascii=False)}
置信度: {triple.get('confidence', '未知')}{context_text}

## 判断标准

- 如果推理**逻辑上合理**，且与已知知识**不矛盾**，回答：YES
- 如果推理**有明显错误**，或与已知知识**矛盾**，回答：NO
- 如果**不确定**，回答：MAYBE

## 输出格式

直接回答（不要有其他文字）：
YES 或 NO 或 MAYBE

理由：（一句话说明）"""
    return prompt


def parse_verifier_response(response: str) -> tuple:
    """从 LLM 响应解析验证结果"""
    text = response.strip().upper()
    
    if text.startswith("YES"):
        return True, response
    elif text.startswith("NO"):
        return False, response
    else:
        # MAYBE 或无法判断，算作通过但降低置信度
        return True, response


def verify_triple(triple: dict, llm_response: str) -> dict:
    """
    验证单个 triple
    返回验证结果（修改 confidence）
    """
    valid, reason = parse_verifier_response(llm_response)
    
    result = triple.copy()
    result["verified"] = valid
    result["verify_reason"] = reason
    
    if not valid:
        result["confidence"] = round(triple.get("confidence", 1.0) * 0.3, 3)
    elif "MAYBE" in reason.upper():
        result["confidence"] = round(triple.get("confidence", 1.0) * 0.7, 3)
    
    return result


if __name__ == "__main__":
    # 测试
    test_triple = {
        "subject": "Transformer",
        "relation": "USED_IN",
        "object": "NLP",
        "source": ["paper_001"],
        "confidence": 0.81
    }
    prompt = build_verify_prompt(test_triple)
    print("Prompt:")
    print(prompt)
