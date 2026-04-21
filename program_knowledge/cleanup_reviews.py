import os
import sys

sys.path.insert(0, "/home/mike/nas_backup/program/knowledge")
from mongo_writer import MongoWriter
from batch_process import batch_review_extraction

def clean_and_rebuild():
    print("🧹 正在清理 MongoDB 数据...")
    writer = MongoWriter()
    
    # 1. 彻底删除之前造出来的所有因为 review 生成的三元组
    res = writer.db.triples.delete_many({"type": "review_extraction"})
    print(f"🗑️ 成功删除了 {res.deleted_count} 条垃圾评论三元组边！")
    
    # 也可能有的没 type，但 source 包含 review
    res_fallback = writer.db.triples.delete_many({"source": {"$regex": "review.*"}})
    print(f"🗑️ 追加删除了 {res_fallback.deleted_count} 条可能的旧格式评论三元组边！")
    
    writer.close()
    
    # 2. 重新运行属性抽取，挂载到 papers节点上
    print("\n📦 重建论文的 Properties 属性中...")
    batch_review_extraction()
    print("\n✅ 清理和属性挂载大功告成！之后只需要重新运行 sync_neo4j 就能得到纯净的学术图谱了。")

if __name__ == "__main__":
    clean_and_rebuild()
