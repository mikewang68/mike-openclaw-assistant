#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_memory_manager.py - AI-Mike 双向学习记忆系统
用 MongoDB 替代文件做记忆存储，支持全文检索 + 结构化画像

核心思路：
  - 所有记忆完整存 MongoDB
  - 每次搜索只取最相关的几条进上下文
  - 对话后自动提取洞察写入 interactions
  - 预测准确率自动追踪
"""

import sys, os
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import pymongo
from pymongo import UpdateOne

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['agent_memory']

# ─── 1. 记忆搜索（替代 memory_search）──────────────────────

def memory_search(query, max_results=5, collection_filter=None):
    """
    搜索 agent_memory，返回最相关的记忆
    - text search on content + summary
    - 按相关度排序
    - 只返回 top_k 条，不全部塞进上下文
    """
    db = get_db()

    results = []
    if collection_filter is None or 'interactions' in collection_filter:
        # 搜索 interactions
        docs = db['interactions'].find(
            {'$text': {'$search': query}},
            {'score': {'$meta': 'textScore'}, 'limit': max_results}
        ).sort([('score', {'$meta': 'textScore'})])

        for d in docs:
            results.append({
                'source': 'interactions',
                'id': d['_id'],
                'date': d.get('date', ''),
                'type': d.get('type', ''),
                'summary': d.get('insight_for_ai', '')[:200],
                'content': d.get('mike_goal', '') + ' ' + d.get('key_decisions', ''),
                'score': d.get('score', 0),
            })

    if collection_filter is None or 'persona' in collection_filter:
        # 搜索 persona
        docs = db['persona'].find(
            {'$text': {'$search': query}},
            {'score': {'$meta': 'textScore'}}
        ).sort([('score', {'$meta': 'textScore'})])

        for d in docs:
            results.append({
                'source': 'persona',
                'id': d['_id'],
                'date': d.get('updated_at', '')[:10],
                'type': 'persona',
                'summary': str(d.get('expertise', ''))[:200],
                'content': str(d),
                'score': d.get('score', 0),
            })

    # 去重 + 排序
    seen = set()
    unique = []
    for r in results:
        if r['id'] not in seen:
            seen.add(r['id'])
            unique.append(r)

    unique.sort(key=lambda x: -x['score'])
    return unique[:max_results]


# ─── 2. 记忆写入 ──────────────────────────────────────────

def memory_save(content, type='general', tags=None, mike_id=None, summary=None, metadata=None):
    """
    写入一条记忆到 agent_memory
    """
    db = get_db()
    doc_id = f"{type}-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}"

    doc = {
        '_id': doc_id,
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': type,
        'tags': tags or [],
        'content': content,
        'summary': summary or content[:200],
        'mike_id': mike_id,
        'metadata': metadata or {},
        'created_at': datetime.now().isoformat(),
    }

    db['interactions'].replace_one({'_id': doc_id}, doc, upsert=True)
    return doc_id


# ─── 3. 画像查询 ─────────────────────────────────────────

def get_persona():
    return get_db()['persona'].find_one({'_id': 'mike'})

def get_persona_field(field_path):
    """读取画像某个字段，field_path 如 'expertise.量化交易' """
    persona = get_persona()
    if not persona:
        return None
    keys = field_path.split('.')
    val = persona
    for k in keys:
        val = val.get(k, None) if isinstance(val, dict) else None
        if val is None:
            return None
    return val

def update_persona(updates):
    """
    更新画像字段，updates 是 dict
    如 update_persona({'expertise.量化交易': 'intermediate', 'knowledge_gaps': ['时序预测']})
    """
    db = get_db()
    set_ops = {}
    unset_ops = {}
    for k, v in updates.items():
        if v is None:
            unset_ops[k] = ''
        else:
            set_ops[k] = v
    op = {'$set': set_ops, '$unset': unset_ops}
    op['$set']['updated_at'] = datetime.now().isoformat()
    db['persona'].update_one({'_id': 'mike'}, op)


# ─── 4. 预测追踪 ─────────────────────────────────────────

def record_prediction(category, predicted, actual=None, correct=None):
    """
    记录一次预测
    - category: 如 'mike_intent', 'stock_topic'
    - predicted: AI 预测的内容（字符串描述）
    - actual: Mike 实际说的/做的（仅供参考记录）
    - correct: 是否猜对了（bool）
      - None → 仅记录预测（待验证）
      - True → 猜对了
      - False → 猜错了
    返回：当前该category的准确率（float）
    """
    db = get_db()
    pred_id = f"pred-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    doc = {
        '_id': pred_id,
        'category': category,
        'predicted': predicted,
        'actual': actual,
        'correct': correct,
        'recorded_at': datetime.now().isoformat(),
    }
    db['predictions'].update_one(
        {'_id': 'mike-predictions'},
        {'$push': {f'history.{category}': doc}},
        upsert=True
    )

    # 更新准确率
    if correct is not None:
        # 重新计算该 category 的准确率
        history = db['predictions'].find_one({'_id': 'mike-predictions'})
        h = history.get('history', {}).get(category, [])
        total = len([x for x in h if x.get('correct') is not None])
        correct_cnt = len([x for x in h if x.get('correct') is True])
        rate = correct_cnt / total if total > 0 else 0.0

        db['predictions'].update_one(
            {'_id': 'mike-predictions'},
            {'$set': {f'patterns.{category}.completion_rate': rate}}
        )
        return rate
    return None


def get_prediction_accuracy(category=None):
    """查询预测准确率"""
    db = get_db()
    doc = db['predictions'].find_one({'_id': 'mike-predictions'})
    if not doc:
        return {}
    if category:
        return doc.get('patterns', {}).get(category, {}).get('completion_rate', 0.0)
    return {k: v.get('completion_rate', 0.0) for k, v in doc.get('patterns', {}).items()}


# ─── 5. 对话洞察自动记录 ─────────────────────────────────

def log_interaction(trigger, mike_goal, key_decisions=None, mike_feedback=None,
                   outcome=None, insight_for_ai=None, tags=None):
    """
    自动记录对话洞察到 interactions
    每次重要对话后调用
    """
    db = get_db()
    doc_id = f"intx-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}"

    doc = {
        '_id': doc_id,
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': 'conversation_insight',
        'trigger': trigger,
        'mike_goal': mike_goal,
        'key_decisions': key_decisions or [],
        'mike_feedback': mike_feedback,
        'outcome': outcome,
        'insight_for_ai': insight_for_ai,
        'tags': tags or [],
        'created_at': datetime.now().isoformat(),
    }

    db['interactions'].replace_one({'_id': doc_id}, doc, upsert=True)

    # 同时更新 growth_log
    if outcome:
        db['growth_log'].update_one(
            {'_id': 'mike-growth'},
            {'$push': {'milestones': {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'event': outcome,
                'state': '对话记录',
                'note': trigger[:50],
            }}},
            upsert=True
        )

    return doc_id


# ─── 6. 知识缺口检测与提醒 ─────────────────────────────────

def detect_knowledge_gap(topic):
    """
    检测 Mike 对某个 topic 的知识缺口
    返回：gap_level (0-3) + 建议
    """
    expertise = get_persona_field('expertise') or {}
    current_level = expertise.get(topic, 'unknown')

    level_map = {'beginner': 1, 'intermediate': 2, 'advanced': 3, 'expert': 4, 'unknown': 0}
    level = level_map.get(current_level, 0)

    gap = max(0, 3 - level)  # 假设目标水平是 advanced

    suggestions = {
        3: f'建议系统学习{topic}，从基础概念开始',
        2: f'可通过论文+项目深入{topic}',
        1: f'已有基础，继续关注{topic}最新研究',
        0: f'{topic}不在当前研究范围，确认是否需要',
    }

    return {
        'topic': topic,
        'current_level': current_level,
        'gap': gap,
        'suggestion': suggestions.get(gap, ''),
    }


# ─── 7. 画像主动提醒 ─────────────────────────────────────

def get_mike_alerts():
    """
    获取 Mike 需要知道的主动提醒
    - 知识缺口提醒
    - 预测准确率变化
    - 待验证的预测
    """
    alerts = []

    # 检查量化交易知识缺口
    gap = detect_knowledge_gap('量化交易')
    if gap['gap'] >= 2:
        alerts.append({
            'type': 'knowledge_gap',
            'topic': '量化交易',
            'message': f'📚 提醒：量化交易知识缺口较大（{gap["current_level"]}），建议优先补充',
            'suggestion': gap['suggestion'],
        })

    # 检查知识构建/推演缺口
    gaps = detect_knowledge_gap('知识构建/推演')
    if gaps['gap'] >= 1:
        alerts.append({
            'type': 'knowledge_gap',
            'topic': '知识构建与推演',
            'message': f'📚 提醒：你提到最欠缺知识构建与推演理论，当前{gaps["current_level"]}',
            'suggestion': gaps['suggestion'],
        })

    # 预测准确率
    acc = get_prediction_accuracy()
    low_acc = [(k, v) for k, v in acc.items() if v > 0 and v < 0.6]
    for cat, rate in low_acc:
        alerts.append({
            'type': 'prediction_accuracy',
            'topic': cat,
            'message': f'🤔 我对「{cat}」的预测准确率偏低（{rate:.0%}），请多纠正我',
        })

    return alerts


# ─── 主入口 ─────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Agent Memory Manager')
    parser.add_argument('--search', help='搜索记忆关键词')
    parser.add_argument('--save', help='写入记忆内容')
    parser.add_argument('--type', default='general', help='记忆类型')
    parser.add_argument('--tags', help='标签，逗号分隔')
    parser.add_argument('--persona', action='store_true', help='显示当前画像')
    parser.add_argument('--alerts', action='store_true', help='显示主动提醒')
    args = parser.parse_args()

    if args.search:
        results = memory_search(args.search)
        print(f"\n🔍 搜索「{args.search}」, {len(results)} 条结果:")
        for r in results:
            print(f"  [{r['source']}] {r['date']} {r['summary'][:80]}")

    if args.save:
        tags = args.tags.split(',') if args.tags else []
        memory_save(args.save, type=args.type, tags=tags)
        print(f"✅ 记忆已保存")

    if args.persona:
        p = get_persona()
        if p:
            print(f"\n👤 Mike 画像（更新于 {p.get('updated_at','')[:10]}）:")
            print(f"  专业领域: {p.get('expertise')}")
            print(f"  当前项目: {list(p.get('current_projects',{}).keys())}")
            print(f"  知识缺口: {p.get('knowledge_gaps', [])}")

    if args.alerts:
        alerts = get_mike_alerts()
        if alerts:
            print("\n🔔 主动提醒:")
            for a in alerts:
                print(f"  {a['message']}")
                if 'suggestion' in a:
                    print(f"    → {a['suggestion']}")
        else:
            print("\n✅ 无待处理提醒")
