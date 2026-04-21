"""
kg_query.py — 知识图谱定向查询与洞察生成

用法:
    python3 kg_query.py "LLM安全"
    python3 kg_query.py "量化交易" --depth 3
    python3 kg_query.py "区块链" --topN 20
"""

import sys
import os
import json
import argparse
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(__file__))
from mongo_writer import MongoWriter


# ============================================================
# 图谱查询
# ============================================================

def query_topic_triples(topic: str, topN: int = 30) -> dict:
    """
    查询与 topic 相关的 triples（模糊匹配 subject 或 object）
    """
    writer = MongoWriter()
    topic_lower = topic.lower()

    # 从 triples 查
    triples_raw = list(writer.db.triples.find({
        "$or": [
            {"subject": {"$regex": topic, "$options": "i"}},
            {"object": {"$regex": topic, "$options": "i"}},
        ]
    }, {"_id": 0}))

    # 从 inferred 查
    inferred_raw = list(writer.db.inferred_triples.find({
        "$or": [
            {"subject": {"$regex": topic, "$options": "i"}},
            {"object": {"$regex": topic, "$options": "i"}},
        ]
    }, {"_id": 0}))

    writer.close()

    return {
        "triples": triples_raw,
        "inferred": inferred_raw,
        "topic": topic,
        "raw_count": len(triples_raw),
        "inferred_count": len(inferred_raw),
    }


def find_replacement_chains(triples: list, topic: str) -> list:
    """
    找技术替代链：A REPLACES B REPLACES C
    只保留与 topic 相关的链
    """
    # 构建 REPLACES 图
    replaces_graph = defaultdict(list)
    for t in triples:
        if t.get("relation") == "REPLACES":
            replaces_graph[t["subject"]].append(t["object"])

    chains = []
    def dfs(node, path):
        if len(path) >= 2:
            chains.append(path[:])
        if len(path) > 5:  # 最多6跳
            return
        for next_n in replaces_graph.get(node, []):
            if next_n not in path:
                path.append(next_n)
                dfs(next_n, path)
                path.pop()

    for start in replaces_graph:
        dfs(start, [start])

    # 过滤：链中任意节点匹配 topic
    topic_lower = topic.lower()
    relevant = []
    for chain in chains:
        if any(topic_lower in n.lower() for n in chain):
            relevant.append(chain)

    return relevant


def find_broken_chains(triples: list, topN: int = 20) -> list:
    """
    找断裂的传递链：A→B 存在，B→C 存在，但 A→C 不存在
    这是"隐式关联"——可能代表被忽视的替代关系或依赖传递
    """
    # 构建邻接表
    direct_edges = set()
    by_subject = defaultdict(set)
    by_object = defaultdict(set)

    for t in triples:
        s, r, o = t["subject"], t["relation"], t["object"]
        edge = (s, o)
        direct_edges.add(edge)
        by_subject[s].add((r, o))
        by_object[o].add((s, r))

    broken_chains = []
    for a, a_relations in by_subject.items():
        for rel1, b in a_relations:
            for rel2, c in by_subject.get(b, set()):
                if a == c or a == b:
                    continue
                # 检查 a → c 是否直接存在
                if (a, c) not in direct_edges:
                    broken_chains.append({
                        "a": a,
                        "rel1": rel1,
                        "b": b,
                        "rel2": rel2,
                        "c": c,
                        "explanation": f"{a} 通过 {b} 间接 {rel2} {c}，但图中无直接边",
                    })

    # 按节点知名度排序（出度+入度）
    degree = defaultdict(int)
    for t in triples:
        degree[t["subject"]] += 1
        degree[t["object"]] += 1

    broken_chains.sort(key=lambda x: degree[x["a"]] + degree[x["c"]], reverse=True)
    return broken_chains[:topN]


def find_conflicts(triples: list) -> list:
    """
    找矛盾关系：同一个 (A, B) 对上有 IMPROVES 也有 DEGRADES/UNDERPERFORMS
    """
    pair_relations = defaultdict(list)
    for t in triples:
        key = (t["subject"], t["object"])
        pair_relations[key].append(t["relation"])

    conflicts = []
    for (s, o), rels in pair_relations.items():
        rel_set = set(rels)
        has_improves = "IMPROVES" in rel_set or "OUTPERFORMS" in rel_set or "ENABLES" in rel_set
        has_degrades = any(r in rel_set for r in ["DEGRADES", "UNDERPERFORMS", "PREVENTS"])
        if has_improves and has_degrades:
            conflicts.append({
                "a": s,
                "b": o,
                "positive": [r for r in rels if r in ["IMPROVES", "OUTPERFORMS", "ENABLES"]],
                "negative": [r for r in rels if r in ["DEGRADES", "UNDERPERFORMS", "PREVENTS"]],
            })

    return conflicts


def find_cross_domain(triples: list, topic: str) -> list:
    """
    找跨领域连接：与 topic 相关的技术被用于其他领域
    """
    topic_lower = topic.lower()
    # 找 topic 相关节点的 USES/APPLIES_TO 关系
    cross = []
    for t in triples:
        s = t["subject"].lower()
        o = t["object"].lower()
        rel = t.get("relation", "")
        # subject 包含 topic，object 不包含 → 从 topic 出发用于其他领域
        if topic_lower in s and topic_lower not in o and rel in ["USES", "ENABLES", "APPLIES_TO", "PROPOSES"]:
            cross.append({
                "from": t["subject"],
                "relation": rel,
                "to": t["object"],
                "type": "outbound",
                "source": t.get("source", ""),
            })
        # object 包含 topic，subject 不包含 → 其他领域用到 topic
        elif topic_lower in o and topic_lower not in s and rel in ["USES", "ENABLES", "APPLIES_TO", "PROPOSES"]:
            cross.append({
                "from": t["subject"],
                "relation": rel,
                "to": t["object"],
                "type": "inbound",
                "source": t.get("source", ""),
            })

    return cross[:20]


def get_top_models(triples: list, topic: str, topN: int = 10) -> list:
    """按出度找出与 topic 相关最活跃的模型/方法"""
    topic_lower = topic.lower()
    degree = Counter()
    for t in triples:
        s, o = t["subject"], t["object"]
        if topic_lower in s.lower():
            degree[s] += 1
        if topic_lower in o.lower():
            degree[s] += 1

    return degree.most_common(topN)


def build_prompt_for_llm(topic: str, data: dict) -> str:
    """构建 LLM 分析 prompt"""
    chains = data.get("replacement_chains", [])
    broken = data.get("broken_chains", [])
    conflicts = data.get("conflicts", [])
    cross = data.get("cross_domain", [])
    top_models = data.get("top_models", [])
    all_triples = data.get("triples", []) + data.get("inferred", [])

    prompt = f"""你是一个学术研究顾问。研究领域：{topic}。

我刚从知识图谱中查询到以下数据，请基于这些真实数据，给出深刻、有建设性的分析。

## 1. 技术替代链（{len(chains)} 条）
"""
    for i, chain in enumerate(chains[:10], 1):
        arrows = " → ".join(chain)
        prompt += f"{i}. {arrows}\n"

    prompt += f"""
## 2. 断裂的传递链（{len(broken)} 条）— 隐式关联，可能是被忽视的重要关系
"""
    for item in broken[:8]:
        prompt += f"- **{item['a']}** --{item['rel1']}--> **{item['b']}** --{item['rel2']}--> **{item['c']}**\n"
        prompt += f"  → {item['explanation']}\n"

    prompt += f"""
## 3. 矛盾与争议（{len(conflicts)} 对）
"""
    for c in conflicts[:5]:
        prompt += f"- **{c['a']}** vs **{c['b']}**：有人说 {c['positive']}，有人说 {c['negative']}\n"

    prompt += f"""
## 4. 跨领域应用（{len(cross)} 条）
"""
    for c in cross[:10]:
        direction = "从该领域出发用于" if c["type"] == "outbound" else "被其他领域引入用于"
        prompt += f"- **{c['from']}** {c['relation']} **{c['to']}**（{direction}）\n"

    prompt += f"""
## 5. 最活跃的方法/模型
"""
    for name, cnt in top_models[:10]:
        prompt += f"- {name}（{cnt} 条关系）\n"

    prompt += """
请给出：
1. **技术进化洞察**：这个领域正在向什么方向收敛？哪些旧技术正在被淘汰？
2. **被忽视的创新点**：基于断裂链，哪些隐式关联可能是全新的研究机会？
3. **具体建议**：基于替代链，你应该关注哪个方向、跳过哪个方向？
4. **最值得读的论文**：基于出度和关系质量，哪个方法最值得深入研究？

请用中文回答，语言精炼，直接给结论，避免废话。每个部分不超过100字。
"""
    return prompt


def call_llm(prompt: str) -> str:
    """占位函数 - LLM 调用由主 agent 完成，此处返回 None 由调用方处理"""
    return None


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="知识图谱查询与洞察生成")
    parser.add_argument("topic", help="研究方向关键词")
    parser.add_argument("--depth", type=int, default=2, help="替代链最大深度")
    parser.add_argument("--topN", type=int, default=20, help="返回结果数量")
    parser.add_argument("--no-llm", action="store_true", help="跳过LLM分析，只输出原始数据")
    parser.add_argument("--json", action="store_true", help="输出JSON格式，供主agent调用LLM分析")
    args = parser.parse_args()

    topic = args.topic.strip()
    print(f"\n🔍 查询知识图谱：{topic}")
    print("=" * 60)

    # 1. 查 triples
    data = query_topic_triples(topic, topN=args.topN)
    triples = data["triples"]
    inferred = data["inferred"]
    all_triples = triples + inferred

    print(f"📊 原始 triples: {len(triples)} 条")
    print(f"📊 推理 triples: {len(inferred)} 条")

    if not triples:
        print("❓ 未找到相关数据，尝试 broader 匹配？")
        return

    # 2. 分析
    print("\n🧠 执行图谱分析...")

    replacement_chains = find_replacement_chains(all_triples, topic)
    broken_chains = find_broken_chains(all_triples, topN=args.topN)
    conflicts = find_conflicts(all_triples)
    cross_domain = find_cross_domain(all_triples, topic)
    top_models = get_top_models(all_triples, topic, topN=args.topN)

    analysis_data = {
        "topic": topic,
        "triples": triples,
        "inferred": inferred,
        "replacement_chains": replacement_chains,
        "broken_chains": broken_chains,
        "conflicts": conflicts,
        "cross_domain": cross_domain,
        "top_models": top_models,
    }

    # 3. 输出原始数据摘要
    print(f"\n📌 替代链: {len(replacement_chains)} 条")
    for chain in replacement_chains[:5]:
        print(f"   {' → '.join(chain)}")

    print(f"\n📌 断裂链: {len(broken_chains)} 条")
    for item in broken_chains[:3]:
        print(f"   {item['a']} --{item['rel1']}--> {item['b']} --{item['rel2']}--> {item['c']}")

    print(f"\n📌 矛盾对: {len(conflicts)} 对")
    for c in conflicts[:3]:
        print(f"   {c['a']} vs {c['b']}: {c['positive']} vs {c['negative']}")

    print(f"\n📌 跨领域连接: {len(cross_domain)} 条")
    for c in cross_domain[:3]:
        print(f"   {c['from']} --{c['relation']}--> {c['to']}")

    # 4. JSON 输出模式（供主 agent 调用 LLM）
    if args.json:
        safe_topic = topic.replace(" ", "_")[:20]
        json_path = f"/tmp/kg_query_{safe_topic}.json"
        output = {
            "topic": topic,
            "raw_count": len(triples),
            "inferred_count": len(inferred),
            "replacement_chains": [[n for n in c] for c in replacement_chains[:10]],
            "broken_chains": broken_chains[:10],
            "conflicts": conflicts[:5],
            "cross_domain": cross_domain[:10],
            "top_models": top_models[:10],
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(json_path)
        return output

    # 5. 标准输出模式
    if not args.no_llm:
        print("\n🤖 调用 LLM 生成洞察...")
        prompt = build_prompt_for_llm(topic, analysis_data)
        llm_result = call_llm(prompt)
        print("\n" + "=" * 60)
        print("💡 LLM 洞察分析：")
        if llm_result:
            print(llm_result)
        return llm_result


if __name__ == "__main__":
    result = main()
    if result:
        print(result)
