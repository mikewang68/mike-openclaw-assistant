#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_unified.py - 统一记忆检索
AGENTS.md 规定的 memory_search 走这里
优先查 MongoDB agent_memory，结果精准且快
文件记忆作为 fallback（向后兼容）
"""

import sys, os
from datetime import datetime, timedelta

sys.path.insert(0, '/program/agent')
from agent_memory_manager import (
    memory_search as mongo_search,
    log_interaction,
    get_persona,
    update_persona,
    record_prediction,
    get_mike_alerts,
    detect_knowledge_gap,
)
import pymongo

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')
WORKSPACE = '/home/node/.openclaw/workspace/workareas/main'
MEMORY_FILE = f'{WORKSPACE}/MEMORY.md'
MEMORY_DIR = f'{WORKSPACE}/memory'

# ─── 1. 统一搜索入口 ──────────────────────────────────────

def memory_search(query, max_results=5):
    """
    统一记忆搜索：MongoDB优先 + 文件兜底
    返回结构化结果列表
    """
    results = []

    # A. MongoDB 优先（精准、毫秒级）
    try:
        mongo_results = mongo_search(query, max_results=max_results)
        for r in mongo_results:
            results.append({
                'source': 'mongodb',
                'source_db': 'agent_memory',
                'id': r['id'],
                'date': r.get('date', ''),
                'type': r.get('type', ''),
                'summary': r['summary'][:300],
                'score': r.get('score', 0),
            })
    except Exception as e:
        pass

    # B. 文件兜底（向后兼容旧系统）
    try:
        file_results = _file_memory_search(query, max_results=max_results)
        results.extend(file_results)
    except Exception:
        pass

    # 去重 + 排序
    seen = set()
    unique = []
    for r in results:
        key = r.get('id', r.get('summary', ''))
        if key not in seen:
            seen.add(key)
            unique.append(r)

    unique.sort(key=lambda x: -x.get('score', 0))
    return unique[:max_results]


def _file_memory_search(query, max_results=5):
    """
    文件记忆兜底搜索（兼容旧逻辑）
    """
    results = []
    query_lower = query.lower()

    # 搜索 MEMORY.md
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
        if query_lower in content.lower():
            results.append({
                'source': 'file',
                'id': 'MEMORY.md',
                'date': '',
                'type': 'memory',
                'summary': content[:500],
                'score': 1.0,
            })
    except:
        pass

    # 搜索 memory/ 目录
    try:
        import glob
        for path in glob.glob(f'{MEMORY_DIR}/*.md'):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                if query_lower in content.lower():
                    results.append({
                        'source': 'file',
                        'id': os.path.basename(path),
                        'date': '',
                        'type': 'daily_memory',
                        'summary': content[:300],
                        'score': 0.5,
                    })
            except:
                pass
    except:
        pass

    return results[:max_results]


# ─── 2. 对话洞察自动记录 ─────────────────────────────────

def auto_log_conversation(trigger, mike_goal, key_decisions=None,
                         outcome=None, insight=None, tags=None):
    """
    重要对话后自动记录到 MongoDB
    被 heartbeat 调用
    """
    try:
        log_interaction(
            trigger=trigger,
            mike_goal=mike_goal,
            key_decisions=key_decisions or [],
            outcome=outcome,
            insight_for_ai=insight,
            tags=tags or [],
        )
        return True
    except Exception as e:
        print(f"⚠️ auto_log_conversation failed: {e}")
        return False


# ─── 3. 预测追踪 ─────────────────────────────────────────

def auto_record_prediction(category, predicted_text, mike_actual=None):
    """
    记录一次预测
    - 如果 mike_actual 为 None：表示待验证，预测被记录为 pending
    - 如果 mike_actual 有值：表示验证完成，记录对错
    """
    try:
        if mike_actual is None:
            # 预测未验证，仅记录
            record_prediction(category, predicted, correct=None)
        else:
            # 验证结果
            correct = (mike_actual.strip() == predicted_text.strip())
            rate = record_prediction(category, predicted_text, mike_actual, correct)
            return correct, rate
    except Exception as e:
        print(f"⚠️ auto_record_prediction failed: {e}")
    return None, None


# ─── 4. 主动提醒（对话开始时检查）─────────────────────────

def get_reminders_for_mike():
    """
    对话开始时检查是否有要提醒 Mike 的事
    返回提醒列表（供输出）
    """
    try:
        alerts = get_mike_alerts()
        reminders = []
        for a in alerts:
            msg = a.get('message', '')
            if 'knowledge_gap' in a.get('type', ''):
                reminders.append(f'📚 {msg}')
                if a.get('suggestion'):
                    reminders.append(f'   → {a["suggestion"]}')
            elif 'prediction_accuracy' in a.get('type', ''):
                reminders.append(f'🤔 {msg}')
        return reminders
    except:
        return []


# ─── 5. 画像更新（对话中增量学习）─────────────────────────

def update_from_conversation(field_path, value):
    """
    从对话中增量更新画像
    field_path: 'expertise.量化交易' / 'knowledge_gaps' / 'preferences.xxx'
    """
    try:
        update_persona({field_path: value})
        return True
    except Exception as e:
        print(f"⚠️ update_persona failed: {e}")
        return False


# ─── CLI 入口 ──────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--search', help='搜索记忆')
    parser.add_argument('--alerts', action='store_true', help='显示主动提醒')
    parser.add_argument('--persona', action='store_true', help='显示画像')
    args = parser.parse_args()

    if args.search:
        results = memory_search(args.search)
        print(f"\n🔍 搜索「{args.search}」: {len(results)} 条")
        for r in results:
            print(f"  [{r['source']}] {r['summary'][:100]}...")

    if args.alerts:
        reminders = get_reminders_for_mike()
        if reminders:
            print("\n🔔 主动提醒:")
            for r in reminders:
                print(f"  {r}")
        else:
            print("\n✅ 无待处理提醒")

    if args.persona:
        p = get_persona()
        if p:
            print(f"\n👤 Mike 画像（更新于 {p.get('updated_at','')[:10]}）:")
            print(f"  专家领域: {p.get('expertise')}")
            print(f"  当前项目: {list(p.get('current_projects',{}).keys())}")
            print(f"  知识缺口: {p.get('knowledge_gaps', [])}")
