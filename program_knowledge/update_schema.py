"""
更新 MongoDB Schema 到 Reasoner v2 版本
- triples: 新增 paper_id, entity_pair 字段
- inferred_triples: 重建为完整 v2 schema
- inference_conflicts: 新建 collection
"""

import sys
sys.path.insert(0, '/program/knowledge')
from pymongo import MongoClient
from pymongo.errors import OperationFailure
from datetime import datetime
from config import MONGO_URI, MONGO_DB

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]

def update_triples_schema():
    """更新 triples collection：新增 paper_id, entity_pair"""
    # 为没有 paper_id 的文档补充字段
    result = db.triples.update_many(
        {"paper_id": {"$exists": False}},
        {
            "$set": {
                "paper_id": "$source",  # 用 source 字段的值
                "entity_pair": ["$subject", "$object"],
                "extractor_version": "v2.0"
            }
        }
    )
    print(f"✅ triples: 更新 {result.modified_count} 条文档")

    # 确保索引存在
    try:
        db.triples.create_index("paper_id")
        db.triples.create_index("entity_pair")
        print("✅ triples: 索引已创建")
    except OperationFailure as e:
        print(f"⚠️ triples: 索引可能已存在 - {e}")

def setup_inferred_triples_v2():
    """重建 inferred_triples 为 v2 schema"""
    # 检查是否已有数据
    count = db.inferred_triples.count_documents({})
    if count > 0:
        print(f"⚠️ inferred_triples 已有 {count} 条数据，跳过清空")
        return

    # inferred_triples 目前为空，定义新 validator
    # 由于 MongoDB validator 不能直接修改，需要 drop 再重建
    try:
        db.inferred_triples.drop()
        print("✅ inferred_triples: 已清空旧 collection")
    except:
        pass

    # 重新创建（validator 在应用层保证）
    db.inferred_triples.create_index([("subject", 1), ("relation", 1), ("object", 1)], unique=True, name="inferred_unique")
    db.inferred_triples.create_index("inferred_at")
    db.inferred_triples.create_index("has_conflict")
    print("✅ inferred_triples: v2 schema 就绪（当前为空）")

def setup_inference_conflicts():
    """新建 inference_conflicts collection"""
    try:
        existing = db.inference_conflicts.find_one()
        if existing:
            print(f"✅ inference_conflicts: 已存在 ({db.inference_conflicts.count_documents({})} 条)")
            return
    except:
        pass

    # 尝试创建 validator（MongoDB 5.0+ 支持 JSON schema validator）
    try:
        db.create_collection("inference_conflicts", validator={
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["triple_a_id", "triple_b_id", "conflict_type"],
                "properties": {
                    "triple_a_id": {"bsonType": "string"},
                    "triple_b_id": {"bsonType": "string"},
                    "conflict_type": {"bsonType": "string", "enum": ["direct", "inferred", "cross_source"]},
                    "resolution": {
                        "bsonType": ["object", "null"],
                        "properties": {
                            "level": {"bsonType": ["int"]},
                            "resolved": {"bsonType": ["bool"]},
                            "winner": {"bsonType": ["string"]},
                            "explanation": {"bsonType": ["string"]},
                            "resolved_at": {"bsonType": ["date"]},
                            "resolved_by": {"bsonType": ["string"]}
                        }
                    },
                    "created_at": {"bsonType": "date"}
                }
            }
        })
        print("✅ inference_conflicts: 新建完成（带 validator）")
    except OperationFailure as e:
        # 如果 validator 不支持，手动创建
        print(f"⚠️ validator 不支持，创建普通 collection - {e}")
        db.create_collection("inference_conflicts")
        print("✅ inference_conflicts: 新建完成（无 validator）")

    # 创建索引
    db.inference_conflicts.create_index("triple_a_id")
    db.inference_conflicts.create_index("triple_b_id")
    db.inference_conflicts.create_index("conflict_type")
    db.inference_conflicts.create_index("resolution.resolved")
    print("✅ inference_conflicts: 索引已创建")

def verify_schemas():
    """验证所有 collection"""
    print("\n=== Schema 验证 ===")
    collections = db.list_collection_names()
    for col in ['triples', 'inferred_triples', 'inference_conflicts']:
        if col in collections:
            count = db[col].count_documents({})
            print(f"  {col}: {count} 条")
            # 打印一条样本的字段
            sample = db[col].find_one()
            if sample:
                fields = [k for k in sample.keys() if not k.startswith('_')]
                print(f"    字段: {fields}")
        else:
            print(f"  {col}: ❌ 不存在")

if __name__ == "__main__":
    print("=== 更新 MongoDB Schema 到 v2 ===\n")
    update_triples_schema()
    print()
    setup_inferred_triples_v2()
    print()
    setup_inference_conflicts()
    print()
    verify_schemas()
    print("\n✅ Schema 更新完成")
