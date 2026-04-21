"""
MongoDB 三元组清洗脚本
1. "This Paper" → 替换为论文短名（从 doc_id 派生）
2. 长 subject（>50字符）→ 替换为 paper_id 清理名，或删除无法映射的
3. 已在 Neo4j 的数据同步时已加白名单，新数据不会再有这些问题
"""
import sys, re
sys.path.insert(0, '/program/knowledge')
from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB

def clean_short_name(name):
    """清理为 ≤50 字符的短名"""
    name = name.replace('_', ' ').strip()
    if len(name) <= 50:
        return name
    words = name.split()
    result = ''
    for w in words:
        if len(result) + len(w) + 1 <= 50:
            result += (' ' + w if result else w)
        else:
            break
    return result

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]

# Step 1: Build title → clean name mapping
docs = list(db.documents.find({}, {'title': 1, 'doc_id': 1}))
title_to_clean = {}
for d in docs:
    title = d.get('title', '')
    doc_id = d.get('doc_id', '')
    if not title or not doc_id:
        continue
    clean = re.sub(r'^[\d]{4}[-_][\d]{2}[-_][\d]{2}[-_]', '', doc_id)
    title_to_clean[title] = clean_short_name(clean)

print(f'Loaded {len(title_to_clean)} title→clean mappings')

# Step 2: Fix "This Paper" subjects
this_papers = list(db.triples.find({'subject': 'This Paper'}, {'source': 1, '_id': 1}))
fixed_count = 0
for tp in this_papers:
    source = tp.get('source', '')
    new_subj = None
    for title, clean in title_to_clean.items():
        if source.startswith(title[:30]) or title.startswith(source[:30]):
            new_subj = clean
            break
    if new_subj:
        db.triples.update_one({'_id': tp['_id']}, {'$set': {'subject': new_subj}})
        fixed_count += 1

print(f'"This Paper" fixed: {fixed_count}/{len(this_papers)}')

# Step 3: Fix long subjects (>50 chars) via paper_id mapping
long_subs = [s for s in db.triples.distinct('subject') if len(s) > 50]
fixed_long = 0
deleted_long = 0
for subj in long_subs:
    # Find paper_id from existing triple
    sample = db.triples.find_one({'subject': subj}, {'paper_id': 1})
    paper_id = sample.get('paper_id') if sample else None
    
    new_subj = None
    if paper_id:
        clean = re.sub(r'^[\d]{4}[-_][\d]{2}[-_][\d]{2}[-_]', '', paper_id)
        new_subj = clean_short_name(clean)
    
    if new_subj and len(new_subj) <= 50:
        db.triples.update_many({'subject': subj}, {'$set': {'subject': new_subj}})
        fixed_long += 1
    else:
        # No mappable paper_id → delete these triples (孤岛数据)
        result = db.triples.delete_many({'subject': subj})
        deleted_long += result.deleted_count

print(f'Long subjects fixed: {fixed_long}, deleted (no mapping): {deleted_long}')

# Summary
total = db.triples.count_documents({})
print(f'\nMongoDB triples after cleanup: {total}')
client.close()
