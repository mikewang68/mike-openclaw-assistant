#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_adaptive_scorecard.py - 自适应评分卡（核心自我学习）
根据历史信号的盈亏，自动调整各 feature 的权重
逻辑：EMA跟踪各概念的预测准确率，动态加权
"""

import sys, os, json
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import pymongo
from pymongo import UpdateOne

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

# ─── 默认评分规则 ────────────────────────────────────────

DEFAULT_WEIGHTS = {
    # 概念热度（NEWS_EVENT当日）
    'concept_news':  {
        'base': 15,
        'decay': 2,       # 每下降1名减2分
        'max': 30,
    },
    # 周线选股池
    'weekly_pool': {
        'bonus': 20,
    },
    # 日线择时已有信号
    'timing_signal': {
        'bonus': 15,
    },
    # 机构强烈推荐(buy>3)
    'rating_strong': {
        'bonus': 15,
    },
    # 机构推荐(buy>0)
    'rating_normal': {
        'bonus': 8,
    },
    # PE健康
    'pe_healthy': {
        'bonus': 10,
        'threshold': 50,
    },
    'pe_very_cheap': {
        'bonus': 5,
        'threshold': 30,
    },
    # PB低估
    'pb_low': {
        'bonus': 5,
        'threshold': 5,
    },
    # 市场覆盖率惩罚（大概念稀释）
    'concept_coverage_penalty': {
        'threshold': 1000,  # >1000只股票的概念降权
        'penalty': 0.5,    # 乘以0.5
    },
}

DEFAULT_CONCEPT_WEIGHT = 1.0  # 每个概念基础权重

# ─── 概念质量统计 ─────────────────────────────────────────

def compute_concept_quality(outcome_docs, min_samples=3):
    """
    从 outcome_history 计算每个概念的平均收益率
    返回: {concept: {'avg_pnl': float, 'count': int, 'win_rate': float, 'weight': float}}
    """
    concept_stats = defaultdict(lambda: {'pnl_sum': 0, 'count': 0, 'wins': 0})

    for doc in outcome_docs:
        signals = doc.get('signals', [])
        for sig in signals:
            pnl = sig.get('pnl_1d', 0)
            concepts = []
            # 从reasons里解析concept
            for r in sig.get('reasons', []):
                # 格式: 「概念名」分
                if '「' in r and '」' in r:
                    start = r.index('「') + 1
                    end = r.index('」')
                    concepts.append(r[start:end])

            for conc in concepts:
                concept_stats[conc]['pnl_sum'] += pnl
                concept_stats[conc]['count'] += 1
                if pnl > 0:
                    concept_stats[conc]['wins'] += 1

    # 计算质量
    quality = {}
    for conc, s in concept_stats.items():
        if s['count'] >= min_samples:
            avg = s['pnl_sum'] / s['count']
            wr = s['wins'] / s['count']
            # 动态权重：胜率高且平均收益正 → 权重>1
            # 胜率<40% → 降权
            if wr >= 0.6 and avg > 1:
                weight = 1.5
            elif wr >= 0.5 and avg > 0:
                weight = 1.2
            elif wr < 0.4 and avg < -1:
                weight = 0.5
            elif wr < 0.3:
                weight = 0.3
            else:
                weight = 1.0
            quality[conc] = {
                'avg_pnl': round(avg, 2),
                'count': s['count'],
                'win_rate': round(wr * 100, 1),
                'weight': weight,
            }

    return quality


# ─── 分数段准确率统计 ─────────────────────────────────────

def compute_score_accuracy(outcome_docs, min_samples=2):
    """
    计算每个分数段的胜率
    返回: {score_bucket: {'wins': int, 'total': int, 'avg_pnl': float}}
    """
    buckets = defaultdict(lambda: {'pnl_sum': 0, 'count': 0, 'wins': 0})

    for doc in outcome_docs:
        for sig in doc.get('signals', []):
            score = sig.get('score', 0)
            pnl = sig.get('pnl_1d', 0)
            # 分桶：<60, 60-80, 80-100, 100-120, 120+
            if score < 60:
                bucket = '<60'
            elif score < 80:
                bucket = '60-80'
            elif score < 100:
                bucket = '80-100'
            elif score < 120:
                bucket = '100-120'
            else:
                bucket = '120+'

            buckets[bucket]['pnl_sum'] += pnl
            buckets[bucket]['count'] += 1
            if pnl > 0:
                buckets[bucket]['wins'] += 1

    result = {}
    for b, s in buckets.items():
        result[b] = {
            'wins': s['wins'],
            'total': s['count'],
            'avg_pnl': round(s['pnl_sum'] / s['count'], 2) if s['count'] > 0 else 0,
            'win_rate': round(s['wins'] / s['count'] * 100, 1) if s['count'] > 0 else 0,
        }
    return result


# ─── 概念覆盖率惩罚 ─────────────────────────────────────

def get_coverage_penalty():
    """
    返回需要降权的泛概念（覆盖率>阈值）
    """
    db = get_db()
    penalties = {}
    for r in db['stock_concepts'].find({'stock_count': {'$gt': 1000}}):
        penalties[r['name']] = 0.5  # 降权50%
    return penalties


# ─── 主流程：每周日自动调整权重 ───────────────────────────

def adapt_weights():
    print(f"\n{'='*55}")
    print(f"🧬 自适应评分卡  {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'='*55}")

    # 取近30天的 outcome_history
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    db = get_db()

    outcomes = list(db['stock_outcome_history'].find(
        {'generated_at': {'$gte': cutoff}}
    ).sort('date', 1))

    print(f"  分析近 {len(outcomes)} 个交易日")
    if len(outcomes) < 5:
        print("  数据不足（<5天），跳过权重调整")
        return

    # 1. 概念质量分析
    concept_quality = compute_concept_quality(outcomes, min_samples=3)
    print(f"\n📊 概念质量（样本≥3）:")
    winners = sorted(concept_quality.items(), key=lambda x: x[1]['weight'], reverse=True)[:10]
    losers = sorted(concept_quality.items(), key=lambda x: x[1]['weight'])[:5]

    print("  🏆 强概念 TOP5:")
    for conc, q in winners[:5]:
        print(f"    {conc}: 胜率{q['win_rate']}% 平均{q['avg_pnl']:+.2f}% 权重×{q['weight']}")

    print("  ⚠️ 弱概念 TOP5:")
    for conc, q in losers[:5]:
        print(f"    {conc}: 胜率{q['win_rate']}% 平均{q['avg_pnl']:+.2f}% 权重×{q['weight']}")

    # 2. 分数段准确率
    score_acc = compute_score_accuracy(outcomes, min_samples=2)
    print(f"\n📈 分数段胜率:")
    for bucket in ['<60', '60-80', '80-100', '100-120', '120+']:
        if bucket in score_acc:
            a = score_acc[bucket]
            print(f"  {bucket:>8}: {a['win_rate']:>5}%  ({a['wins']}/{a['total']}) 平均{a['avg_pnl']:+.2f}%")

    # 3. 生成新权重
    coverage_penalty = get_coverage_penalty()

    new_card = {
        '_id': datetime.now().strftime('%Y-%m-%d'),
        'version': datetime.now().strftime('%Y%m%d%H%M'),
        'date': datetime.now().strftime('%Y-%m-%d'),
        'analysis_window_days': 30,
        'outcomes_count': len(outcomes),
        'concept_quality': concept_quality,
        'score_accuracy': score_acc,
        'coverage_penalty': coverage_penalty,
        'adapted_at': datetime.now().isoformat(),
    }

    # 4. 写入MongoDB
    db['stock_scorecard'].update_one(
        {'_id': 'current'},
        {'$set': new_card},
        upsert=True
    )

    # 保存历史版本
    db['stock_scorecard_history'].update_one(
        {'_id': new_card['version']},
        {'$set': new_card},
        upsert=True
    )

    # 5. 结论建议
    high_wr_concepts = [c for c, q in concept_quality.items() if q['weight'] >= 1.5]
    low_wr_concepts = [c for c, q in concept_quality.items() if q['weight'] <= 0.5]

    print(f"\n🧬 权重调整建议:")
    if high_wr_concepts:
        print(f"  ⬆️ 加权概念: {', '.join(high_wr_concepts[:5])}")
    if low_wr_concepts:
        print(f"  ⬇️ 降权概念: {', '.join(low_wr_concepts[:5])}")
    if coverage_penalty:
        print(f"  🚫 泛概念降权: {', '.join(list(coverage_penalty.keys())[:5])}")

    # 6. 推送信号阈值建议
    useful_buckets = {b: a for b, a in score_acc.items() if a['total'] >= 3}
    if useful_buckets:
        best_bucket = max(useful_buckets.items(), key=lambda x: x[1]['win_rate'])
        min_bucket = min(useful_buckets.items(), key=lambda x: x[1]['win_rate'])
        print(f"\n  📊 最佳分数段: {best_bucket[0]} (胜率{best_bucket[1]['win_rate']}%)")
        print(f"  📊 建议最低买入分: {min_bucket[0]} (胜率{min_bucket[1]['win_rate']}%)")

    print(f"\n✅ 评分卡已更新: stock_scorecard/current")
    print(f"{'='*55}")

    return new_card


if __name__ == '__main__':
    adapt_weights()
