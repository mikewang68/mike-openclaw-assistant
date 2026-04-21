#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_signal_advisor.py - 每日精选信号（最终输出）
每次严格输出5只，附带置信度评级
结合：KG推理 + 自适应评分卡 + 分数段胜率
"""

import sys, os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import pymongo

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

def load_scorecard():
    db = get_db()
    card = db['stock_scorecard'].find_one({'_id': 'current'})
    if not card:
        return None
    return {
        'concept_quality': card.get('concept_quality', {}),
        'score_accuracy': card.get('score_accuracy', {}),
        'coverage_penalty': card.get('coverage_penalty', {}),
        'adapted_at': card.get('adapted_at', ''),
    }

def get_concept_weight(conc, scorecard):
    if not scorecard:
        return 1.0
    w = scorecard.get('concept_quality', {}).get(conc, {}).get('weight', 1.0)
    if conc in scorecard.get('coverage_penalty', {}):
        w *= 0.5
    return w

def get_min_score(scorecard):
    """根据历史胜率动态计算最低买入分"""
    if not scorecard:
        return 60  # 默认
    acc = scorecard.get('score_accuracy', {})
    # 找胜率最高的分段，返回其下限
    best = None
    best_wr = 0
    bucket_map = {'<60': 0, '60-80': 60, '80-100': 80, '100-120': 100, '120+': 120}
    for bucket, a in acc.items():
        if a['total'] >= 3 and a['win_rate'] > best_wr:
            best_wr = a['win_rate']
            best = bucket
    if best and best_wr >= 50:
        return bucket_map.get(best, 60)
    return 60

def compute_confidence(score, reasons, scorecard):
    """
    评级：
    - 120+ → ⭐⭐⭐ 高置信（预计胜率>65%）
    - 80-120 → ⭐⭐ 中置信（50-65%）
    - 60-80 → ⭐ 低置信（<50%，慎用）
    """
    if score >= 120:
        return '⭐⭐⭐ 高置信'
    elif score >= 80:
        return '⭐⭐ 中置信'
    else:
        return '⭐ 低置信（参考）'

def get_hot_concepts_from_news(days=3):
    """从NEWS_EVENT三元组获取真实热点概念"""
    db = get_db()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    heat = {}
    for r in db['stock_kg'].aggregate([
        {'$match': {
            'relation': 'NEWS_EVENT',
            'updated_at': {'$gte': cutoff},
        }},
        {'$group': {'_id': '$object', 'count': {'$sum': 1}}},
        {'$sort': {'count': -1}},
        {'$limit': 20}
    ]):
        heat[r['_id']] = r['count']
    return heat

def get_buy_signals_today():
    """读取今日推理信号"""
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    doc = db['stock_signals'].find_one({'_id': today})
    if not doc:
        return []
    return doc.get('buy_signals', [])

def get_outcome_stats():
    """读取近30天胜率统计"""
    db = get_db()
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    docs = list(db['stock_outcome_history'].find(
        {'generated_at': {'$gte': cutoff}}
    ))
    if not docs:
        return None
    total_w = sum(d['winners'] for d in docs)
    total = sum(d['total'] for d in docs)
    avg_pnl = sum(d['avg_pnl_1d'] for d in docs) / len(docs)
    return {
        'days': len(docs),
        'win_rate': round(total_w / total * 100, 1) if total > 0 else 0,
        'avg_pnl': round(avg_pnl, 2),
    }

def get_fallback_candidates():
    """当信号不足3个时，从周线选股池补充候选股（按3日涨幅排序）"""
    db = get_db()
    cutoff = (datetime.now() - timedelta(days=5)).isoformat()
    docs = list(db['stock_kg'].find({
        'is_weekly_pool': True,
        'updated_at': {'$gte': cutoff},
    }).sort('pnl_3d', -1).limit(20))
    result = []
    for d in docs:
        code = d.get('code', '')
        name = d.get('name', '')
        pnl_3d = d.get('pnl_3d', 0)
        result.append({
            'code': code,
            'name': name,
            'score': 70 + pnl_3d * 1.5,  # 基础分70 + 3日涨幅加成
            'pnl_3d': pnl_3d,
            'is_weekly': True,
            'is_timing': False,
            'reasons': [f'周线池候选，3日涨幅{pnl_3d:.2f}%'],
        })
    return result

def recommend():
    print(f"\n{'='*58}")
    print(f"🎯 每日精选信号  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*58}")

    # ── 1. 检查有没有NEWS_EVENT热点 ──────────────────────────
    news_heat = get_hot_concepts_from_news(days=3)
    has_news = bool(news_heat)

    if has_news:
        print(f"\n🔥 新闻热点概念（NEWS_EVENT）:")
        for c, n in list(news_heat.items())[:8]:
            print(f"  {c}: {n}条")
    else:
        print(f"\n💡 当前无NEWS_EVENT，用HAS_CONCEPT覆盖率代替（精度较低）")

    # ── 2. 加载自适应评分卡 ────────────────────────────────
    scorecard = load_scorecard()
    min_score = get_min_score(scorecard)

    if scorecard:
        print(f"\n📊 自适应评分卡（上次更新: {scorecard.get('adapted_at', '')[:10]}）")
        acc = scorecard.get('score_accuracy', {})
        for b in ['60-80', '80-100', '100-120', '120+']:
            if b in acc and acc[b]['total'] >= 2:
                a = acc[b]
                print(f"  {b:>8}: 胜率{a['win_rate']}%({a['wins']}/{a['total']}) 均收益{a['avg_pnl']:+.2f}%")
    else:
        print(f"\n📊 评分卡：冷启动（默认权重）")

    # ── 3. 读取今日信号 ────────────────────────────────────
    signals = get_buy_signals_today()
    if not signals:
        print(f"\n⚠️ 今日推理信号为空，请先运行 stock_kg_reasoner.py")
        return []

    print(f"\n📈 候选信号: {len(signals)} 只（最低{min_score}分）")

    # ── 4. 严格筛选TOP5 ─────────────────────────────────────
    filtered = [s for s in signals if s.get('score', 0) >= min_score]
    # 任务1：3日涨幅 > 0 时，score + pnl_3d * 1.5
    filtered.sort(key=lambda x: x.get('score', 0) + max(0, x.get('pnl_3d', 0)) * 1.5, reverse=True)

    # 任务2：信号不足3个时，从周线池补充
    if len(filtered) < 3:
        fallback = get_fallback_candidates()
        print(f"\n📌 信号不足3个，从周线池补充 {len(fallback)} 只候选")
        existing_codes = {s.get('code') for s in filtered}
        for fc in fallback:
            if fc['code'] not in existing_codes:
                filtered.append(fc)
        filtered.sort(key=lambda x: x.get('score', 0) + max(0, x.get('pnl_3d', 0)) * 1.5, reverse=True)

    top5 = filtered[:5]

    # ── 5. 读取近30天胜率 ────────────────────────────────────
    stats = get_outcome_stats()
    if stats:
        print(f"\n📅 近{stats['days']}天历史表现:")
        print(f"   胜率: {stats['win_rate']}%")
        print(f"   平均1日收益: {stats['avg_pnl']:+.2f}%")

    # ── 6. 输出精选 ─────────────────────────────────────────
    print(f"\n{'='*58}")
    print(f"✅ 每日精选（严格{len(top5)}只）")
    print(f"{'='*58}")

    RECOMMENDATIONS = []
    for i, s in enumerate(top5, 1):
        score = s.get('score', 0)
        confidence = compute_confidence(score, s.get('reasons', []), scorecard)
        name = s.get('name', '')
        code = s.get('code', '')
        pe = s.get('pe', 0)
        is_weekly = s.get('is_weekly', False)
        is_timing = s.get('is_timing', False)

        tags = []
        if is_weekly: tags.append('周线池')
        if is_timing: tags.append('择时持仓')
        tag_str = ' '.join(tags) if tags else ''

        print(f"\n  {i}. {code} {name}")
        print(f"     评分: {score}分 | {confidence}")
        print(f"     PE: {pe:.1f} | {tag_str}")
        print(f"     理由:")
        for r in s.get('reasons', [])[:4]:
            print(f"       └ {r}")

        RECOMMENDATIONS.append({
            'rank': i,
            'code': code,
            'name': name,
            'score': score,
            'confidence': confidence,
            'pe': pe,
            'is_weekly': is_weekly,
            'is_timing': is_timing,
            'reasons': s.get('reasons', [])[:4],
            'date': datetime.now().strftime('%Y-%m-%d'),
        })

    if not top5:
        print("  ⚠️ 无符合最低分门槛的信号，今日不推荐")

    # ── 7. 写入推荐记录 ────────────────────────────────────
    if RECOMMENDATIONS:
        db = get_db()
        db['stock_recommendations'].update_one(
            {'_id': datetime.now().strftime('%Y-%m-%d')},
            {'$set': {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'recommendations': RECOMMENDATIONS,
                'has_news_event': has_news,
                'min_score_threshold': min_score,
                'generated_at': datetime.now().isoformat(),
            }},
            upsert=True
        )
        print(f"\n✅ 推荐已写入 stock_recommendations/{datetime.now().strftime('%Y-%m-%d')}")

    print(f"{'='*58}")

    # ── 8. 胜率预估 ─────────────────────────────────────────
    if stats and stats['days'] >= 5:
        print(f"\n🎯 目标：每批≥3只上涨（60%胜率）")
        print(f"   当前历史胜率: {stats['win_rate']}%")
        if stats['win_rate'] >= 60:
            print(f"   ✅ 已达标！")
        else:
            print(f"   📈 还差{60 - stats['win_rate']:.1f}%，继续优化中...")

    return RECOMMENDATIONS

if __name__ == '__main__':
    recommend()
