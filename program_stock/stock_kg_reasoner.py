#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_kg_reasoner.py - 股票KG推理引擎 V2
结合：概念热度 + 周线选股池 + 日线择时持仓 + 财务数据 → 买入/卖出信号
"""

import sys, os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import pymongo
from pymongo import UpdateOne

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

# ─── 数据加载 ─────────────────────────────────────────────

def load_scorecard():
    """加载当前自适应评分卡（若无则用默认）"""
    db = get_db()
    card = db['stock_scorecard'].find_one({'_id': 'current'})
    if not card:
        return None
    return {
        'concept_quality': card.get('concept_quality', {}),
        'score_accuracy': card.get('score_accuracy', {}),
        'coverage_penalty': card.get('coverage_penalty', {}),
    }

# ─── 泛概念列表（市场覆盖率>1000只，全量过滤）───
BROAD_CONCEPTS = {
    '融资融券', '深股通', '沪股通', '央国企改革', '创业板综',
    '富时罗素', '专精特新', '机构重仓', '标准普尔', 'MSCI中国',
    '沪股通', '深股通', '标普道琼斯', '富时罗素', '中证500',
    '中证1000', '沪深300', '上证180', '上证380',
}

def get_concept_weight(conc, scorecard):
    """计算某概念的动态权重（含泛概念过滤）"""
    if conc in BROAD_CONCEPTS:
        return 0.0  # 彻底过滤
    if not scorecard:
        return 1.0
    cq = scorecard.get('concept_quality', {})
    cp = scorecard.get('coverage_penalty', {})
    w = cq.get(conc, {}).get('weight', 1.0)
    if conc in cp:
        w *= 0.5
    return w


def load_basics():
    """一次性加载所有基础数据"""
    db = get_db()
    scorecard = load_scorecard()

    # 1. 概念热度（优先NEWS_EVENT；无则用HAS_CONCEPT市场覆盖率）
    cutoff = (datetime.now() - timedelta(days=3)).isoformat()
    heat_map = {}
    for r in db['stock_kg'].aggregate([
        {'$match': {
            'relation': {'$in': ['NEWS_EVENT', 'CONCEPT_HOT']},
            'updated_at': {'$gte': cutoff},
        }},
        {'$group': {'_id': '$object', 'count': {'$sum': 1}}},
        {'$sort': {'count': -1}}
    ]):
        heat_map[r['_id']] = r['count']

    # 如果没有新闻热度，用HAS_CONCEPT的股票覆盖率作为热度代理
    if not heat_map:
        print("  (今日无NEWS_EVENT，用HAS_CONCEPT市场覆盖率替代概念热度)")
        for r in db['stock_concepts'].find().sort('stock_count', -1).limit(30):
            heat_map[r['name']] = r.get('stock_count', 0) or 0

    # 2. 周线选股池
    wp = db['weekly_pool'].find_one(sort=[('_id', -1)])
    weekly_pool = {s['code'] for s in wp.get('stocks', [])} if wp else set()

    # 3. 日线择时持仓
    dt = db['daily_timing'].find_one(sort=[('_id', -1)])
    timing = dt.get('signals', []) if dt else []
    timing_map = {s['code']: s for s in timing}

    # 4. 所有股票的HAS_CONCEPT（一次性查，按concept聚合）
    code_concepts = {}  # code -> [concept_list]
    concept_stocks = {}  # concept -> {code: name}
    for r in db['stock_kg'].aggregate([
        {'$match': {'relation': 'HAS_CONCEPT'}},
        {'$lookup': {
            'from': 'code', 'localField': 'subject',
            'foreignField': 'code', 'as': 'info'
        }},
        {'$unwind': {'path': '$info', 'preserveNullAndEmptyArrays': True}},
        {'$project': {
            'code': '$subject',
            'concept': '$object',
            'name': {'$ifNull': ['$info.name', '$subject_name']},
        }}
    ]):
        c = r['code']
        conc = r['concept']
        if c not in code_concepts:
            code_concepts[c] = []
        code_concepts[c].append(conc)
        if conc not in concept_stocks:
            concept_stocks[conc] = {}
        concept_stocks[conc][c] = r.get('name', '')

    # 5. PE/PB（一次性查）
    pe_pb = {}
    for r in db['code'].find({}, {'code': 1, 'name': 1, 'PE': 1, 'PB': 1, 'industry': 1}):
        pe_pb[r['code']] = {
            'name': r.get('name', ''),
            'industry': r.get('industry', ''),
            'pe': r.get('PE'),
            'pb': r.get('PB'),
        }

    # 6. 近90天机构评级
    rating_cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    ratings = {}
    for r in db['stock_kg'].aggregate([
        {'$match': {
            'relation': 'RATED_BY',
            'updated_at': {'$gte': rating_cutoff},
        }},
        {'$sort': {'updated_at': -1}},
        {'$group': {
            '_id': '$subject',
            'rating': {'$first': '$object'},
            'buy': {'$first': '$rating_buy'},
            'add': {'$first': '$rating_add'},
        }}
    ]):
        ratings[r['_id']] = r

    return {
        'heat_map': heat_map,
        'weekly_pool': weekly_pool,
        'timing_map': timing_map,
        'code_concepts': code_concepts,
        'concept_stocks': concept_stocks,
        'pe_pb': pe_pb,
        'ratings': ratings,
    }

# ─── 核心推理 ─────────────────────────────────────────────

def compute_score(code, d, scorecard=None):
    """对单只股票评分"""
    score = 0
    reasons = []

    concepts = d['code_concepts'].get(code, [])
    heat_map = d['heat_map']

    # A. 概念热度得分（Top15递减）× 动态权重 × 泛概念过滤
    ranked = sorted(d['heat_map'].items(), key=lambda x: -x[1])
    conc_score_map = {}
    for i, (conc, cnt) in enumerate(ranked):
        if conc in BROAD_CONCEPTS:
            continue
        base = max(30 - i * 2, 2) if i < 15 else 0
        dyn_weight = get_concept_weight(conc, scorecard)
        final = round(base * dyn_weight)
        conc_score_map[conc] = final
        if final > 0 and conc in concepts:
            reasons.append(f'「{conc}」{base}×{dyn_weight:.1f}={final}分')

    # B. 周线选股池（权重增强）
    if code in d['weekly_pool']:
        score += 25
        reasons.append('周线选股池+25')

    # C. 日线择时已有信号（持仓信号最强）
    if code in d['timing_map']:
        sig = d['timing_map'][code]
        action = sig.get('action', '')
        if '买' in action:
            score += 30
            reasons.append(f'择时持仓[✅买]+30')
        elif '等' in action or '确认' in action:
            score += 20
            reasons.append(f'择时等待[⏳]+20')
        else:
            score += 15
            reasons.append(f'择时信号[{action}]+15')

    # D. 机构评级
    r = d['ratings'].get(code)
    if r:
        if r.get('buy', 0) > 5:
            score += 18
            reasons.append(f'机构强推({r["buy"]}家)+18')
        elif r.get('buy', 0) > 3:
            score += 15
            reasons.append(f'机构推荐({r["buy"]}家)+15')
        elif r.get('buy', 0) > 0:
            score += 8
            reasons.append(f'机构推荐({r["buy"]}家)+8')

    # E. 财务健康
    fin = d['pe_pb'].get(code, {})
    pe = fin.get('pe')
    pb = fin.get('pb')
    if pe and 0 < pe < 50:
        score += 10
        reasons.append(f'PE={pe:.1f}<50 +10')
    elif pe and 0 < pe < 30:
        score += 5
    if pb and 0 < pb < 5:
        score += 5
        reasons.append(f'PB={pb:.1f}<5 +5')

    return score, reasons


def reason():
    print(f"\n{'='*58}")
    print(f"🧠 股票KG推理引擎  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*58}")

    d = load_basics()

    print(f"\n📊 基础数据:")
    print(f"  热门概念: {len(d['heat_map'])} 个")
    print(f"  周线选股池: {len(d['weekly_pool'])} 只")
    print(f"  日线择时持仓: {len(d['timing_map'])} 只")
    print(f"  有概念股票: {len(d['code_concepts'])} 只")

    # 打印TOP10热门概念
    ranked = sorted(d['heat_map'].items(), key=lambda x: -x[1])[:10]
    print(f"\n🔥 热门概念 TOP10:")
    for conc, cnt in ranked:
        print(f"  {conc}: {cnt}条")

    # ── 买入推理 ────────────────────────────────────────
    print(f"\n{'='*58}")
    print(f"📈 买入信号推理（Top15，≥50分）")
    print(f"{'='*58}")

    candidates = set()
    # 缩小候选范围：只考虑有热门概念的股票
    for conc in list(d['heat_map'].keys())[:30]:
        candidates.update(d['concept_stocks'].get(conc, {}).keys())

    print(f"  候选范围: {len(candidates)} 只（有热门概念的股票）")

    scored = []
    scorecard = load_scorecard()
    if scorecard:
        print(f"  (使用自适应评分卡，动态权重生效)")
    for code in candidates:
        score, reasons = compute_score(code, d, scorecard)
        if score >= 50:
            fin = d['pe_pb'].get(code, {})
            name = fin.get('name', '')
            industry = fin.get('industry', '')
            scored.append({
                'code': code,
                'name': name,
                'industry': industry,
                'score': score,
                'reasons': reasons,
                'pe': fin.get('pe'),
                'pb': fin.get('pb'),
                'is_timing': code in d['timing_map'],
                'is_weekly': code in d['weekly_pool'],
            })

    scored.sort(key=lambda x: -x['score'])

    BUY_SIGNALS = []
    print(f"  {'代码':<8} {'名称':<8} {'分':<4} {'PE':<7} {'池':<3} {'择时':<4} TOP3理由")
    print(f"  {'-'*58}")
    for s in scored[:15]:
        tag_pool = '✅' if s['is_weekly'] else ''
        tag_timing = '📌' if s['is_timing'] else ''
        pe_str = f'{s["pe"]:.1f}' if s['pe'] else 'N/A'
        reasons_str = s['reasons'][0] if s['reasons'] else ''
        print(f"  {s['code']:<8} {s['name']:<8} {s['score']:<4} {pe_str:<7} {tag_pool:<3} {tag_timing:<4} {reasons_str}")
        for r in s['reasons'][1:]:
            print(f"  {'':>22} └ {r}")
        BUY_SIGNALS.append(s)

    # ── 卖出推理 ────────────────────────────────────────
    print(f"\n{'='*58}")
    print(f"📉 卖出/止损预警")
    print(f"{'='*58}")

    SELL_SIGNALS = []
    # 持仓股票的概念被打压
    regulated_concepts = [
        '房地产税', '互联网金融监管', '医药集采', '教育培训监管',
        '平台经济监管', '数据安全监管', '互联网反垄断',
        '游戏监管', '电子烟监管', '增高针监管'
    ]

    for code, sig in d['timing_map'].items():
        concepts = d['code_concepts'].get(code, [])
        hit = [c for c in concepts if c in regulated_concepts]

        fin = d['pe_pb'].get(code, {})
        pe = fin.get('pe')
        entry = sig.get('entry_price', 0)
        pnl = sig.get('current_pnl', 0)

        if hit:
            SELL_SIGNALS.append({
                'code': code, 'name': sig.get('name', ''),
                'type': '🔴卖出预警', 'reason': f'「{hit[0]}」受监管',
                'entry': entry, 'pnl': pnl
            })
        elif pe and pe > 100:
            SELL_SIGNALS.append({
                'code': code, 'name': sig.get('name', ''),
                'type': '🔴止损', 'reason': f'PE={pe:.1f}>100',
                'entry': entry, 'pnl': pnl
            })

    if SELL_SIGNALS:
        for s in SELL_SIGNALS:
            print(f"  {s['code']} {s['name']}: {s['type']} - {s['reason']} | 浮亏{pnl:.1f}%" if s.get('pnl') else f"  {s['code']} {s['name']}: {s['type']} - {s['reason']}")
    else:
        print("  无卖出/止损信号 ✓")

    # ── 写入 MongoDB ───────────────────────────────────
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')

    db['stock_signals'].update_one(
        {'_id': today},
        {'$set': {
            'date': today,
            'buy_signals': BUY_SIGNALS[:15],
            'sell_signals': SELL_SIGNALS,
            'hot_concepts': [{'concept': c, 'count': n} for c, n in ranked[:20]],
            'generated_at': datetime.now().isoformat(),
        }},
        upsert=True
    )

    print(f"\n✅ 结果已写入 stock_signals/{today}")
    print(f"{'='*58}")

    return BUY_SIGNALS, SELL_SIGNALS


if __name__ == '__main__':
    reason()
