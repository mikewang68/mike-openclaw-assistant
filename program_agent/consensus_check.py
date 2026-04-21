#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
consensus_check.py - 每日共识确认问题生成

从 agent_memory + paper KG 提取不确定的推理
生成1-3个确认问题，推送给Mike
将pending问题保存到 /tmp/consensus_pending.json

Cron: 12:30 CST 每天
"""

import sys, os, json, random
from datetime import datetime
from pymongo import MongoClient

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')
PENDING_FILE = '/tmp/consensus_pending.json'

def get_mongo_client():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)


def get_persona_and_predictions():
    """从 agent_memory 获取画像和预测准确率"""
    db = get_mongo_client()['agent_memory']
    
    persona = db.persona.find_one({'_id': 'mike'})
    predictions = db.predictions.find_one({'_id': 'mike-predictions'})
    
    if not persona:
        return {}, {}
    
    patterns = predictions.get('patterns', {}) if predictions else {}
    accuracy = {}
    for k, v in patterns.items():
        if isinstance(v, dict) and 'completion_rate' in v:
            accuracy[k] = v['completion_rate']
    
    return persona, accuracy


def get_low_confidence_kg(limit=10):
    """从 paper KG 获取置信度中等的不确定推理"""
    kg_db = get_mongo_client()['knowledge_graph']
    
    # validated='pending' 且 confidence 在 0.55-0.8 之间
    triples = list(kg_db.inferred_triples.find(
        {
            'validated': 'pending',
            'confidence': {'$gte': 0.55, '$lte': 0.8}
        },
        {'_id': 0, 'subject': 1, 'relation': 1, 'object': 1, 'confidence': 1}
    ).limit(limit))
    
    return triples


def get_knowledge_gaps(persona):
    """从persona获取知识缺口，生成相关确认问题"""
    gaps = persona.get('knowledge_gaps', [])
    expertise = persona.get('expertise', {})
    goals = persona.get('goals', {})
    
    questions = []
    
    # 知识缺口相关
    if '量化交易' in gaps or expertise.get('量化交易') == 'beginner':
        questions.append({
            'type': 'B',
            'topic': '量化交易',
            'question': '你提到想深入量化交易。当前最缺的是哪一块？',
            'options': [
                '选股策略（如何找好股票）',
                '择时判断（何时买/卖）',
                '风险管理（仓位/止损）',
                '回测验证（验证策略有效性）'
            ],
            'source': 'knowledge_gap'
        })
    
    if '知识构建/推演' in gaps:
        questions.append({
            'type': 'A',
            'topic': '知识推演',
            'question': '关于"知识推演"，我理解为你希望AI能主动发现隐藏联系？这和传统知识图谱有什么区别？',
            'options': [
                '对，我希望AI能推理出我不知道的关联',
                '不完全是，我更想要XX'
            ],
            'source': 'knowledge_gap'
        })
    
    # 目标相关
    short_goals = goals.get('short_term', [])
    if short_goals:
        questions.append({
            'type': 'B',
            'topic': '目标确认',
            'question': f'你的短期目标之一是"{short_goals[0]}"——现在进展如何？',
            'options': [
                '正在进行，有具体计划',
                '刚开始，还在摸索',
                '暂停了，因为遇到困难',
                '目标变了'
            ],
            'source': 'goals'
        })
    
    return questions


def get_kg_cross_domain_questions(kg_triples):
    """从KG不确定推理生成跨领域确认问题"""
    questions = []
    
    # 跨领域关键词
    cross_keywords = {
        'LLM': ['量化', '金融', '安全', '交易'],
        '区块链': ['AI', 'LLM', '安全'],
        '量化': ['LLM', 'AI', '机器学习']
    }
    
    for t in kg_triples[:5]:
        s, o = t['subject'], t['object']
        
        # 检查是否跨领域
        for domain, related in cross_keywords.items():
            if domain in s or domain in o:
                other = o if domain in s else s
                if any(r.lower() in other.lower() for r in related):
                    # 找到跨领域连接，生成问题
                    questions.append({
                        'type': 'B',
                        'topic': '跨领域推理',
                        'question': f'我从论文KG发现"{s}"和"{o}"有关联。你认为这个联系有价值吗？',
                        'options': [
                            '有价值，值得深入研究',
                            '有一定道理，但不确定',
                            '不相关，可能是噪音'
                        ],
                        'source': 'kg_inference',
                        'evidence': f'{s} --{t["relation"]}--> {o} (conf={t["confidence"]})'
                    })
                    break
    
    return questions


def get_prediction_questions(accuracy):
    """从预测准确率生成确认问题"""
    questions = []
    
    # 准确率低的类别
    low_acc = [(k, v) for k, v in accuracy.items() if 0 < v < 0.7]
    
    for cat, rate in low_acc[:1]:  # 最多1个
        if cat == 'mike_intent':
            questions.append({
                'type': 'B',
                'topic': '沟通风格',
                'question': '我发现有时猜不准你的意图。你发消息时，更希望我：',
                'options': [
                    '先确认一次再执行',
                    '直接给结论，不用问',
                    '给多个选项让我选'
                ],
                'source': 'prediction_low_accuracy',
                'current_rate': f'{rate*100:.0f}%'
            })
    
    return questions


def select_questions(all_questions, max_q=3):
    """随机选1-max_q个问题"""
    if not all_questions:
        return []
    
    random.shuffle(all_questions)
    selected = all_questions[:max_q]
    
    # 按topic去重，最多max_q个
    seen = set()
    deduped = []
    for q in selected:
        if q['topic'] not in seen:
            seen.add(q['topic'])
            deduped.append(q)
    
    return deduped[:max_q]


def format_telegram_message(questions, pending_file):
    """格式化Telegram消息"""
    if not questions:
        return None, []
    
    lines = ['🤝 AI共识确认\n']
    lines.append('基于今天的分析，我有{}个问题想确认：\n'.format(len(questions)))
    lines.append('---')
    
    for i, q in enumerate(questions, 1):
        lines.append(f'\n**[{i}] {q["topic"]}**')
        lines.append(f'\n{q["question"]}\n')
        
        if q['type'] == 'B' and 'options' in q:
            for j, opt in enumerate(q['options'], ord('A')):
                lines.append(f'  {chr(j)}) {opt}')
        
        lines.append('')
    
    lines.append('\n---\n')
    lines.append('回复格式：1A 2B 或直接文字回复\n')
    
    # 保存pending问题
    pending = {
        'generated_at': datetime.now().isoformat(),
        'questions': [
            {
                'id': i,
                'topic': q['topic'],
                'type': q['type'],
                'question': q['question'],
                'options': q.get('options', []),
                'source': q.get('source', ''),
                'evidence': q.get('evidence', '')
            }
            for i, q in enumerate(questions, 1)
        ]
    }
    
    with open(pending_file, 'w', encoding='utf-8') as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)
    
    return ''.join(lines), pending['questions']


def send_telegram(message):
    """推送到Telegram"""
    import urllib.request
    
    token = os.environ.get('TG_BOT_TOKEN', '')
    chat_id = os.environ.get('TG_CHAT_ID', '')
    
    if not token or not chat_id:
        print('⚠️ 未设置TG_BOT_TOKEN或TG_CHAT_ID，跳过推送')
        print('消息内容：')
        print(message)
        return False
    
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = json.dumps({
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    })
    
    req = urllib.request.Request(
        url,
        data=payload.encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print('✅ Telegram推送成功')
            return True
    except Exception as e:
        print(f'⚠️ Telegram推送失败: {e}')
        return False


def main():
    print(f'\n🤝 共识确认问题生成 — {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 50)
    
    # 1. 收集问题来源
    print('\n📊 收集问题来源...')
    
    persona, accuracy = get_persona_and_predictions()
    print(f'  画像: {list(persona.get("knowledge_gaps", []))}')
    print(f'  预测准确率: {accuracy}')
    
    kg_triples = get_low_confidence_kg(limit=10)
    print(f'  KG不确定推理: {len(kg_triples)} 条')
    
    # 2. 生成问题
    all_questions = []
    
    gap_qs = get_knowledge_gaps(persona)
    all_questions.extend(gap_qs)
    print(f'  知识缺口问题: {len(gap_qs)} 个')
    
    kg_qs = get_kg_cross_domain_questions(kg_triples)
    all_questions.extend(kg_qs)
    print(f'  KG跨领域问题: {len(kg_qs)} 个')
    
    pred_qs = get_prediction_questions(accuracy)
    all_questions.extend(pred_qs)
    print(f'  预测确认问题: {len(pred_qs)} 个')
    
    # 3. 选问题
    selected = select_questions(all_questions, max_q=3)
    print(f'\n  选中问题: {len(selected)} 个')
    
    for i, q in enumerate(selected, 1):
        print(f'    [{i}] {q["topic"]}: {q["question"][:40]}...')
    
    if not selected:
        print('  无问题可问，跳过')
        return
    
    # 4. 格式化并推送
    message, questions = format_telegram_message(selected, PENDING_FILE)
    if message:
        send_telegram(message)
        print(f'\n✅ 共识确认已推送，{len(questions)} 个问题')
        print(f'   Pending文件: {PENDING_FILE}')
    else:
        print('  生成消息失败')


if __name__ == '__main__':
    main()