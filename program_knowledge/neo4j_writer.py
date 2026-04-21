"""
Neo4j 写入模块 — Reasoner v2 版本
支持：原始 triples + 推理 triples + mongo_id 回链
"""

import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# 关系白名单 — 只允许标准学术关系，防止噪声关系污染图谱
ALLOWED_RELATIONS = {
    # ── 学术研究关系 ──
    "PROPOSES", "INTRODUCES", "USES", "BASED_ON", "IMPROVES",
    "OUTPERFORMS", "REPLACES", "ENABLES", "APPLIES_TO", "RELATES_TO",
    "INCLUDES", "PROVIDES", "ACHIEVES", "PROVES", "EXHIBITS",
    "IS", "LACKS", "PRODUCE", "PRODUCES", "SUFFER_FROM", "SUFFERS_FROM",
    "INDICATES", "PREDICTS", "AFFECTS", "CORRELATES_WITH", "RESULTS_IN",
    "LEADS_TO", "TRADED_AT", "BELONGS_TO", "PART_OF", "COMPOSED_OF",
    # ── 医疗健康关系 ──
    "TREATS",           # 治疗
    "PREVENTS",         # 预防
    "CAUSES",          # 致病原因
    "ASSOCIATED_WITH",  # 关联
}

try:
    from neo4j import GraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False


class Neo4jWriter:
    def __init__(self):
        if not NEO4J_AVAILABLE:
            raise RuntimeError("neo4j driver not installed")
        self.driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD)
        )

    def close(self):
        self.driver.close()

    def write_triple(
        self,
        subject: str,
        relation: str,
        obj: str,
        confidence: float = 1.0,
        inferred: bool = False,
        mongo_id: str = None,
        source: str = None,
        hop_count: int = 0,
        rule_tag: str = None,
        evidence: str = None,
    ):
        """写入单个 triple 到 Neo4j"""
        with self.driver.session() as session:
            session.execute_write(
                self._create_relation,
                subject=subject,
                relation=relation,
                obj=obj,
                confidence=confidence,
                inferred=inferred,
                mongo_id=mongo_id,
                source=source,
                hop_count=hop_count,
                rule_tag=rule_tag,
                evidence=evidence,
            )

    @staticmethod
    def _create_relation(tx, **kwargs):
        """创建或更新节点和关系（白名单过滤）"""
        # 清理关系类型中的特殊字符
        rel_type = kwargs["relation"].upper().replace("-", "_").replace(" ", "_")

        # 白名单过滤：不在白名单的关系直接跳过
        if rel_type not in ALLOWED_RELATIONS:
            return

        # 构建关系属性
        props = {
            "confidence": kwargs.get("confidence", 1.0),
            "inferred": kwargs.get("inferred", False),
        }

        if kwargs.get("mongo_id"):
            props["mongo_id"] = kwargs["mongo_id"]
        if kwargs.get("source"):
            props["source"] = kwargs["source"]
        if kwargs.get("hop_count", 0) > 0:
            props["hop_count"] = kwargs["hop_count"]
        if kwargs.get("rule_tag"):
            props["rule_tag"] = kwargs["rule_tag"]
        if kwargs.get("evidence"):
            # 截断过长的 evidence
            evidence = kwargs["evidence"][:200] if len(kwargs["evidence"]) > 200 else kwargs["evidence"]
            props["evidence"] = evidence

        query = f"""
        MERGE (a:Entity {{name: $subject}})
        MERGE (b:Entity {{name: $object}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r.confidence = $confidence,
            r.inferred = $inferred
        """

        # 动态添加可选属性
        set_clauses = ["r.confidence = $confidence", "r.inferred = $inferred"]
        for key in ["mongo_id", "source", "hop_count", "rule_tag", "evidence"]:
            if kwargs.get(key):
                set_clauses.append(f"r.{key} = ${key}")

        query = f"""
        MERGE (a:Entity {{name: $subject}})
        MERGE (b:Entity {{name: $object}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET {", ".join(set_clauses)}
        """

        params = {
            "subject": kwargs["subject"],
            "object": kwargs["obj"],
            "confidence": kwargs.get("confidence", 1.0),
            "inferred": kwargs.get("inferred", False),
        }

        for key in ["mongo_id", "source", "hop_count", "rule_tag", "evidence"]:
            if kwargs.get(key):
                params[key] = kwargs[key]

        tx.run(query, **params)

    def write_triples(self, triples: list):
        """批量写入 triples（自动区分 raw vs inferred）"""
        if not triples:
            return

        for t in triples:
            if isinstance(t, dict):
                self.write_triple(
                    subject=t["subject"],
                    relation=t.get("relation", "RELATES_TO"),
                    obj=t["object"],
                    confidence=t.get("confidence", 1.0),
                    inferred=t.get("inferred", False),
                    mongo_id=t.get("mongo_id"),
                    source=t.get("source"),
                    hop_count=t.get("hop_count", 0),
                    rule_tag=t.get("rule_tag"),
                    evidence=t.get("evidence"),
                )

    def clear_all(self):
        """清空所有节点和关系"""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def get_node(self, name: str) -> dict:
        """获取节点"""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (n:Entity {name: $name}) RETURN n",
                name=name
            )
            record = result.single()
            return dict(record["n"]) if record else None

    def get_triple_by_mongo_id(self, mongo_id: str) -> dict:
        """通过 mongo_id 回链查询"""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE r.mongo_id = $mongo_id
                RETURN a.name as subject, type(r) as relation, b.name as object,
                       r.confidence as confidence, r.inferred as inferred,
                       r.hop_count as hop_count, r.rule_tag as rule_tag
                """,
                mongo_id=mongo_id
            )
            record = result.single()
            return dict(record) if record else None

    def query_path(self, subject: str, obj: str, hops: int = 2) -> list:
        """查询两点之间的路径"""
        with self.driver.session() as session:
            result = session.run(
                f"MATCH path = (a:{{name: '{subject}'}})-[*1..{hops}]->(b:{{name: '{obj}'}}) "
                "RETURN path LIMIT 10"
            )
            return [dict(record["path"]) for record in result]


def write_triples_placeholder(triples: list):
    """Neo4j 未配置时的占位函数"""
    log_path = os.path.join(os.path.dirname(__file__), "neo4j_pending.log")
    with open(log_path, "a", encoding="utf-8") as f:
        from datetime import datetime
        f.write(f"\n--- {datetime.utcnow().isoformat()} ---\n")
        import json
        for t in triples:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"⚠️ Neo4j 未配置，{len(triples)} 条 triples 写入 pending log")


if __name__ == "__main__":
    if NEO4J_AVAILABLE:
        writer = Neo4jWriter()
        print("Neo4j connection OK")
        writer.close()
    else:
        print("neo4j driver not installed")
