"""
Reasoner - 知识推理引擎
基于已有 triples 做链式推理，生成 inferred_triples
"""

import sys
import os
import json
from typing import List

sys.path.insert(0, os.path.dirname(__file__))
from config import MAX_INFERRED, REASONER_HOPS
from mongo_writer import MongoWriter


# 传递性规则定义
# IF A REL1 B AND B REL2 C THEN A REL3 C
TRANSITIVITY_RULES = {
    ("REPLACES", "USED_IN"): "USED_IN",
    ("REPLACES", "APPLIES_TO"): "APPLIES_TO",
    ("BASED_ON", "ENABLES"): "ENABLED_BY",
    ("PROPOSES", "USED_IN"): "USED_IN",
    ("IMPROVES", "USED_IN"): "IMPROVED_BY",
    ("USES", "ENABLES"): "ENABLES",
    ("USES", "USED_IN"): "USED_IN",
}


def infer(triples: List[dict], max_hops: int = REASONER_HOPS, max_results: int = MAX_INFERRED) -> List[dict]:
    """
    核心推理逻辑
    
    Args:
        triples: 所有原始 triples
        max_hops: 最大推理深度
        max_results: 最多返回多少条推理结果
    
    Returns:
        inferred triples 列表
    """
    # 构建 (subject, object) -> triple 的索引
    # 用于快速查找 B 作为 subject 的所有 triples
    by_subject = {}
    for t in triples:
        s = t["subject"]
        if s not in by_subject:
            by_subject[s] = []
        by_subject[s].append(t)
    
    inferred = []
    seen = set()  # 用于去重 (subject, relation, object)
    
    def add_inferred(subj, rel, obj, sources, confidence):
        key = (subj, rel, obj)
        if key not in seen:
            seen.add(key)
            inferred.append({
                "subject": subj,
                "relation": rel,
                "object": obj,
                "source": sources,
                "confidence": confidence,
                "type": "inferred"
            })
    
    # 1跳推理
    for t1 in triples:
        s1, r1, o1 = t1["subject"], t1["relation"], t1["object"]
        
        # 查找 o1 作为 subject 的 triples
        if o1 not in by_subject:
            continue
        
        for t2 in by_subject[o1]:
            s2, r2, o2 = t2["subject"], t2["relation"], t2["object"]
            
            # 应用传递性规则
            rule_key = (r1, r2)
            if rule_key in TRANSITIVITY_RULES:
                new_rel = TRANSITIVITY_RULES[rule_key]
                new_conf = t1.get("confidence", 0.8) * t2.get("confidence", 0.8)
                add_inferred(
                    s1, new_rel, o2,
                    [t1.get("source", ""), t2.get("source", "")],
                    round(new_conf, 3)
                )
            
            # 直接传递（同类关系）
            if r1 == r2 and s1 != o2:
                add_inferred(
                    s1, r1, o2,
                    [t1.get("source", ""), t2.get("source", "")],
                    round(t1.get("confidence", 0.8) * t2.get("confidence", 0.8), 3)
                )
    
    # 2跳推理（可选）
    if max_hops >= 2:
        # 基于已有的 inferred 做第二轮
        for inf in list(inferred):
            if inf["object"] not in by_subject:
                continue
            for t2 in by_subject[inf["object"]]:
                rule_key = (inf["relation"], t2["relation"])
                if rule_key in TRANSITIVITY_RULES:
                    new_rel = TRANSITIVITY_RULES[rule_key]
                    new_conf = inf.get("confidence", 0.8) * t2.get("confidence", 0.8)
                    add_inferred(
                        inf["subject"], new_rel, t2["object"],
                        list(set(inf.get("source", []) + [t2.get("source", "")])),
                        round(new_conf, 3)
                    )
    
    # 按置信度排序并截断
    inferred.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return inferred[:max_results]


def run_reasoner() -> List[dict]:
    """从 MongoDB 读取 triples，运行推理，结果存回 MongoDB"""
    writer = MongoWriter()
    
    # 读取所有 triples
    triples = writer.get_all_triples()
    print(f"Reasoner: loaded {len(triples)} triples")
    
    if not triples:
        writer.close()
        return []
    
    # 运行推理
    inferred = infer(triples)
    print(f"Reasoner: generated {len(inferred)} inferred triples")
    
    if inferred:
        count = writer.write_inferred(inferred)
        print(f"Reasoner: wrote {count} inferred triples to MongoDB")
    
    writer.close()
    return inferred


if __name__ == "__main__":
    results = run_reasoner()
    print(f"\nTop 5 inferred:")
    for t in results[:5]:
        print(f"  {t['subject']} --{t['relation']}--> {t['object']} (conf={t['confidence']})")
