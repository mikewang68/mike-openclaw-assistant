"""
entity_aligner.py
知识图谱实体对齐与消歧工具 (Entity Resolution & Alignment)
作用：清理 LLM 提取出来的过长、同义词不同名、大小写不一的杂乱实体。
"""

import sys
import re

sys.path.insert(0, "/home/mike/nas_backup/program/knowledge")
from mongo_writer import MongoWriter

# 同义词归一化词典 (模糊/正则匹配映射)
SYNONYM_MAP = {
    r"(?i)^(LLM|Large Language Model|Large Language Models|Large Language Mode|LLMs)$": "Large Language Models (LLMs)",
    r"(?i)^(Transformer|Transformers|Transformer Model|Transformer Architecture)$": "Transformer",
    r"(?i)^(CNN|CNNs|Convolutional Neural Network|Convolutional Neural Networks)$": "Convolutional Neural Network (CNN)",
    r"(?i)^(GAN|GANs|Generative Adversarial Network|Generative Adversarial Networks)$": "Generative Adversarial Networks (GANs)",
    r"(?i)^(RL|Reinforcement Learning|Reinforcement learning)$": "Reinforcement Learning (RL)",
    r"(?i)^(DRL|Deep Reinforcement Learning)$": "Deep Reinforcement Learning (DRL)",
    r"(?i)^Self[- ]Attention.*": "Self-Attention",
    r"(?i)^Cross[- ]Attention.*": "Cross-Attention",
    r"(?i)^GPT[-_]?(3|3\.5|4|4o|o1|o3)?.*$": "GPT Family Models",
    r"(?i)^BERT.*$": "BERT Family Models",
    r"(?i)^LlaMa[-_]?[123]?[a-zA-Z0-9]?.*$": "LLaMA Family Models",
    r"(?i)^LoRA.*$": "LoRA (Low-Rank Adaptation)",
    r"(?i)^(FL|Federated Learning)$": "Federated Learning (FL)",
    r"(?i)^Neural Network.*": "Neural Networks",
    r"(?i)^Deep Learning.*": "Deep Learning"
}

def clean_entity_name(name: str) -> str:
    if not isinstance(name, str):
        return str(name)
        
    original_name = name.strip()
    new_name = original_name

    # 1. 废弃词汇：代词
    lower_name = new_name.lower()
    if lower_name in ["this paper", "our model", "our method", "the proposed method", "the paper"]:
        return "DELETED_REDUNDANT"

    # 2. 如果过长，尝试提取缩写（比如 "Very Long Paper Name (VLPN)"）
    if len(new_name) > 30:
        match = re.search(r"\(([A-Z][A-Za-z0-9_-]{2,})\)", new_name)
        if match:
            new_name = match.group(1)
            
    # 3. 依然很长，强行截断，留前三个单词，防止孤岛匹配不上
    if len(new_name) > 40:
        words = new_name.split()
        if len(words) > 3:
            new_name = " ".join(words[:4]) + "..."
            
    # 4. 正则匹配聚类归一
    for pattern, official_name in SYNONYM_MAP.items():
        if re.match(pattern, new_name):
            new_name = official_name
            break
            
    # 5. 轻度规范：首字母大写
    if len(new_name) > 0 and new_name[0].islower():
        new_name = new_name[0].upper() + new_name[1:]

    return new_name

def align_entities():
    print("🤖 启动知识图谱实体对齐引擎...")
    writer = MongoWriter()
    
    triples = list(writer.db.triples.find({"type": "extraction"}))
    print(f"📦 载入 {len(triples)} 条 LLM 抽取的三元组边...")
    
    updates = 0
    deletes = 0
    
    for t in triples:
        subj = t.get("subject", "")
        obj = t.get("object", "")
        
        new_subj = clean_entity_name(subj)
        new_obj = clean_entity_name(obj)
        
        if new_subj == "DELETED_REDUNDANT" or new_obj == "DELETED_REDUNDANT":
            writer.db.triples.delete_one({"_id": t["_id"]})
            deletes += 1
            continue

        if new_subj == new_obj:
            writer.db.triples.delete_one({"_id": t["_id"]})
            deletes += 1
            continue

        if new_subj != subj or new_obj != obj:
            try:
                writer.db.triples.update_one(
                    {"_id": t["_id"]},
                    {"$set": {
                        "subject": new_subj,
                        "object": new_obj
                    }}
                )
                updates += 1
            except Exception as e:
                # 捕获 DuplicateKeyError，意味着归一化后产生了两条完全相同的边
                # 直接将冗余的当前边删除即可
                if "E11000 duplicate key error" in str(e) or "DuplicateKeyError" in str(type(e)):
                    writer.db.triples.delete_one({"_id": t["_id"]})
                    deletes += 1
                else:
                    print(f"Error updating {t['_id']}: {e}")
            
    writer.close()
    print("✅ 实体对齐完成！")
    print(f"   🔄 更新归一化了 {updates} 条三元组边！")
    print(f"   🗑️ 砍掉了 {deletes} 条含有诸如 'This Paper' 的无意义边！")

if __name__ == "__main__":
    align_entities()
