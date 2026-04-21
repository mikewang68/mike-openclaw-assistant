"""
Neo4j 同步脚本
将 MongoDB 中的 triples 和 inferred_triples 同步到 Neo4j
支持增量同步和全量重建
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from mongo_writer import MongoWriter
from neo4j_writer import Neo4jWriter, NEO4J_AVAILABLE


def sync_raw_triples_to_neo4j(mongo, neo4j, batch_size=50):
    """将 MongoDB raw triples 同步到 Neo4j"""
    triples = mongo.get_all_triples()
    print(f"同步 {len(triples)} 条 raw triples 到 Neo4j...")

    for i in range(0, len(triples), batch_size):
        batch = triples[i:i+batch_size]
        for t in batch:
            try:
                neo4j.write_triple(
                    subject=t["subject"],
                    relation=t.get("relation", "RELATES_TO"),
                    obj=t["object"],
                    confidence=t.get("confidence", 1.0),
                    inferred=False,
                    source=t.get("source"),
                    evidence=t.get("evidence"),
                )
            except Exception as e:
                print(f"  ⚠️ 写入失败 {t['subject']}: {e}")
        print(f"  进度 {min(i+batch_size, len(triples))}/{len(triples)}")

    print(f"✅ raw triples 同步完成")


def sync_inferred_triples_to_neo4j(mongo, neo4j):
    """将 MongoDB inferred_triples 同步到 Neo4j"""
    inferred = mongo.get_all_inferred()
    print(f"同步 {len(inferred)} 条 inferred triples 到 Neo4j...")

    for t in inferred:
        try:
            # 从 provenance 第一个 triple 获取 mongo_id
            provenance = t.get("inference_metadata", {}).get("provenance", [])
            source_triple_id = None
            if provenance and isinstance(provenance[0], dict):
                source_triple_id = str(provenance[0].get("triple", {}).get("_id", ""))

            meta = t.get("inference_metadata", {})
            neo4j.write_triple(
                subject=t["subject"],
                relation=t.get("relation", "RELATES_TO"),
                obj=t["object"],
                confidence=t.get("confidence", 0.5),
                inferred=True,
                mongo_id=t.get("_id"),
                source="reasoner_v2",
                hop_count=meta.get("hop_count", 0),
                rule_tag=meta.get("rule_tag", ""),
            )
        except Exception as e:
            print(f"  ⚠️ 写入失败 {t['subject']}: {e}")

    print(f"✅ inferred triples 同步完成")


def rebuild_neo4j_from_mongodb(clear=True):
    """
    从 MongoDB 重建 Neo4j
    用于初始化或 Neo4j 数据丢失后重建
    """
    if not NEO4J_AVAILABLE:
        print("❌ Neo4j 不可用")
        return

    mongo = MongoWriter()
    neo4j = Neo4jWriter()

    print("=== 从 MongoDB 重建 Neo4j ===\n")

    if clear:
        print("清空 Neo4j...")
        neo4j.clear_all()
        print("✅ Neo4j 已清空\n")

    # 同步 raw triples
    sync_raw_triples_to_neo4j(mongo, neo4j)
    print()

    # 同步 inferred triples
    sync_inferred_triples_to_neo4j(mongo, neo4j)
    print()

    # 验证
    with neo4j.driver.session() as session:
        node_count = session.run("MATCH (n:Entity) RETURN count(n) as cnt").single()["cnt"]
        edge_count = session.run("MATCH ()-[r]->() RETURN count(r) as cnt").single()["cnt"]
        print(f"=== Neo4j 统计 ===")
        print(f"  节点数: {node_count}")
        print(f"  边数: {edge_count}")

    mongo.close()
    neo4j.close()
    print("\n✅ Neo4j 重建完成")


if __name__ == "__main__":
    rebuild_neo4j_from_mongodb(clear=True)
