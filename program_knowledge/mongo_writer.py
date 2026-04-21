"""
MongoDB 写入模块 — Reasoner v2 版本
支持：raw triples + inferred triples + inference conflicts + 双写标记
"""

import sys
import os
from datetime import datetime
from typing import Optional
from dataclasses import dataclass
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

sys.path.insert(0, os.path.dirname(__file__))
from config import MONGO_URI, MONGO_DB


@dataclass
class InferredTriple:
    """推理三元组（v2 schema）"""
    subject: str
    relation: str
    object: str
    confidence: float
    hop_count: int = 0
    provenance: list = None          # 推理链溯源
    rule_tag: str = ""
    alternative_paths: int = 1
    is_conflict: bool = False
    conflict_id: str = None
    inferred_at: datetime = None
    reasoner_version: str = "v2.0"
    synced_to_neo4j: bool = False
    sync_error: str = None

    def to_mongo_doc(self) -> dict:
        return {
            "subject": self.subject,
            "relation": self.relation,
            "object": self.object,
            "confidence": self.confidence,
            "inference_metadata": {
                "hop_count": self.hop_count,
                "rule_tag": self.rule_tag,
                "alternative_paths": self.alternative_paths,
                "provenance": self.provenance or [],
            },
            "has_conflict": self.is_conflict,
            "conflict_id": self.conflict_id,
            "inferred_at": self.inferred_at or datetime.utcnow(),
            "reasoner_version": self.reasoner_version,
            "synced_to_neo4j": self.synced_to_neo4j,
            "sync_error": self.sync_error,
        }


@dataclass
class ConflictRecord:
    """冲突记录"""
    triple_a_id: str
    triple_b_id: str
    conflict_type: str  # direct / inferred / cross_source
    resolution: dict = None  # {level, resolved, winner, explanation, resolved_at, resolved_by}
    created_at: datetime = None

    def to_mongo_doc(self) -> dict:
        return {
            "triple_a_id": self.triple_a_id,
            "triple_b_id": self.triple_b_id,
            "conflict_type": self.conflict_type,
            "resolution": self.resolution,
            "created_at": self.created_at or datetime.utcnow(),
        }


class MongoWriter:
    def __init__(self):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client[MONGO_DB]
        self._ensure_indexes()

    def _ensure_indexes(self):
        """建立索引"""
        # triples
        self.db.triples.create_index(
            [("subject", 1), ("relation", 1), ("object", 1)],
            unique=True, name="triple_unique"
        )
        self.db.triples.create_index("paper_id")
        self.db.triples.create_index("entity_pair")

        # inferred_triples
        self.db.inferred_triples.create_index(
            [("subject", 1), ("relation", 1), ("object", 1)],
            unique=True, name="inferred_unique"
        )
        self.db.inferred_triples.create_index("inferred_at")
        self.db.inferred_triples.create_index("has_conflict")
        self.db.inferred_triples.create_index("synced_to_neo4j")

        # inference_conflicts
        self.db.inference_conflicts.create_index("triple_a_id")
        self.db.inference_conflicts.create_index("triple_b_id")
        self.db.inference_conflicts.create_index("resolution.resolved")

    def write_triples(self, triples: list, source: str = "extraction") -> int:
        """写入原始 triples"""
        count = 0
        for t in triples:
            t["created_at"] = datetime.utcnow()
            t["type"] = source
            if "entity_pair" not in t:
                t["entity_pair"] = [t.get("subject", ""), t.get("object", "")]
            if "extractor_version" not in t:
                t["extractor_version"] = "v2.0"
            # 确保 confidence 是浮点数
            if "confidence" in t and not isinstance(t["confidence"], (int, float)):
                try:
                    t["confidence"] = float(t["confidence"])
                except (ValueError, TypeError):
                    t["confidence"] = 1.0
            # 验证必须字段
            if not all(t.get(k) for k in ("subject", "relation", "object")):
                continue
            try:
                self.db.triples.insert_one(t)
                count += 1
            except DuplicateKeyError:
                pass
        return count

    def write_inferred(self, inferred: list) -> list:
        """
        写入推理结果，返回插入的文档 ID 列表
        用于 Neo4j 回链
        """
        if not inferred:
            return []

        results = []
        for t in inferred:
            if isinstance(t, dict):
                doc = t.copy()
            else:
                doc = t.to_mongo_doc() if hasattr(t, 'to_mongo_doc') else t

            doc["inferred_at"] = datetime.utcnow()
            doc["has_conflict"] = doc.get("has_conflict", False)
            doc["synced_to_neo4j"] = False

            try:
                result = self.db.inferred_triples.insert_one(doc)
                results.append(str(result.inserted_id))
            except DuplicateKeyError:
                pass

        return results

    def update_synced_status(self, mongo_id: str, success: bool, error: str = None):
        """更新同步状态"""
        update = {"$set": {"synced_to_neo4j": success}}
        if not success and error:
            update["$set"]["sync_error"] = error

        self.db.inferred_triples.update_one(
            {"_id": ObjectId(mongo_id)},
            update
        )

    def mark_conflict_resolved(self, conflict_id: str, resolution: dict):
        """标记冲突已解决"""
        self.db.inference_conflicts.update_one(
            {"_id": ObjectId(conflict_id)},
            {"$set": {"resolution": resolution}}
        )

    def write_conflict(self, conflict: ConflictRecord) -> str:
        """写入冲突记录，返回 ID"""
        doc = conflict.to_mongo_doc()
        result = self.db.inference_conflicts.insert_one(doc)
        return str(result.inserted_id)

    def write_document(self, doc: dict) -> bool:
        """写入文档索引"""
        doc = doc.copy()
        doc["created_at"] = datetime.utcnow()
        doc_id_val = doc.get("doc_id")
        try:
            self.db.documents.insert_one(doc)
            return True
        except DuplicateKeyError:
            # 更新时排除 _id（不可修改）
            update_doc = {k: v for k, v in doc.items() if k != "_id"}
            self.db.documents.update_one(
                {"doc_id": doc_id_val}, {"$set": update_doc}
            )
            return False

    def get_all_triples(self) -> list:
        """获取所有 raw triples"""
        return list(self.db.triples.find({}, {"_id": 0}))

    def get_all_inferred(self) -> list:
        """获取所有 inferred triples"""
        return list(self.db.inferred_triples.find({}, {"_id": 0}))

    def get_inferred_by_id(self, mongo_id: str) -> dict:
        """通过 _id 获取单条 inferred"""
        return self.db.inferred_triples.find_one({"_id": ObjectId(mongo_id)}, {"_id": 0})

    def get_raw_by_id(self, mongo_id: str) -> dict:
        """通过 _id 获取单条 raw triple"""
        return self.db.triples.find_one({"_id": ObjectId(mongo_id)}, {"_id": 0})

    def get_document(self, doc_id: str) -> dict:
        return self.db.documents.find_one({"doc_id": doc_id}, {"_id": 0})

    def get_pending_sync(self) -> list:
        """获取待同步到 Neo4j 的 inferred"""
        return list(self.db.inferred_triples.find(
            {"synced_to_neo4j": False},
            {"_id": 1}
        ))

    def stats(self) -> dict:
        return {
            "triples": self.db.triples.count_documents({}),
            "inferred_triples": self.db.inferred_triples.count_documents({}),
            "inference_conflicts": self.db.inference_conflicts.count_documents({}),
            "documents": self.db.documents.count_documents({}),
        }

    def close(self):
        self.client.close()


def write_document_index(pdf_path: str, md_path: str, title: str = None):
    """快捷函数：写入文档索引"""
    writer = MongoWriter()
    doc_id = os.path.basename(md_path).replace(".md", "")
    doc = {
        "doc_id": doc_id,
        "path": md_path,
        "pdf_path": pdf_path,
        "title": title or doc_id,
        "tags": [],
        "summary": ""
    }
    writer.write_document(doc)
    writer.close()
    return doc_id


if __name__ == "__main__":
    writer = MongoWriter()
    print("MongoDB stats:", writer.stats())
    writer.close()
