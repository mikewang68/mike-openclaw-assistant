#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_outcome_tracker.py - 每日盈亏追踪 V3
新增功能：
  - 追踪盈亏后，将验证结果回写到 paper KG（inferred_triples.validated）
  - 信号涨 → 对应概念三元组 validated=true
  - 信号跌 → validated=false 或降低 confidence

反馈闭环：
  stock_signals → outcome → validated update → KG演化
"""

import sys, os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import pymongo

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://stock:681123@192.168.1.2:27017/admin')

def get_stock_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['stock']

def get_kg_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)['knowledge_graph']


def get_next_open_price(code, signal_date):
    """获取 signal_date 之后第一个交易日的开盘价（作为入场成本）"""
    db = get_stock_db()
    r = db['k_raw_v3'].find_one(
        {'code': code, 'date': {'$gt': signal_date}},
        sort=[('date', 1)],
        projection={'date': 1, 'open': 1}
    )
    if r:
        return r.get('open', 0) or 0, r['date']
    return 0, ''


def get_nth_close_price(code, signal_date, n):
    """获取信号日后的第n个交易日收盘价"""
    db = get_stock_db()
    pipeline = [
        {'$match': {'code': code, 'date': {'$gt': signal_date}}},
        {'$sort': {'date': 1}},
        {'$limit': n},
        {'$group': {'_id': None, 'date': {'$last': '$date'}, 'close': {'$last': '$close'}}},
    ]
    r = list(db['k_raw_v3'].aggregate(pipeline))
    if r:
        return r[0].get('close', 0) or 0, r[0]['date']
    return 0, ''


def compute_returns(signals, signal_date):
    """计算 signals 在 signal_date 生成后的实际收益"""
    if not signals:
        return []

    updated = []
    for sig in signals:
        code = sig['code']
        name = sig.get('name', '')

        open_price, entry_date = get_next_open_price(code, signal_date)
        if not open_price:
            print(f"  ⚠️  {code} {name}: 无法获取T+1开盘价，跳过")
            continue

        close_1d, date_1d = get_nth_close_price(code, signal_date, 1)
        close_3d, date_3d = get_nth_close_price(code, signal_date, 3)
        close_5d, date_5d = get_nth_close_price(code, signal_date, 5)

        pnl_1d = (close_1d - open_price) / open_price * 100 if open_price > 0 and close_1d > 0 else 0
        pnl_3d = (close_3d - open_price) / open_price * 100 if open_price > 0 and close_3d > 0 else 0
        pnl_5d = (close_5d - open_price) / open_price * 100 if open_price > 0 and close_5d > 0 else 0

        sig['entry_price'] = round(open_price, 2)
        sig['entry_date'] = entry_date
        sig['pnl_1d'] = round(pnl_1d, 2)
        sig['pnl_3d'] = round(pnl_3d, 2)
        sig['pnl_5d'] = round(pnl_5d, 2)
        sig['close_1d'] = round(close_1d, 2)
        sig['close_1d_date'] = date_1d
        sig['close_3d'] = round(close_3d, 2)
        sig['close_3d_date'] = date_3d
        sig['close_5d'] = round(close_5d, 2)
        sig['close_5d_date'] = date_5d
        sig['outcome_computed'] = True
        updated.append(sig)

    return updated


def validate_kg_triples(updated_signals, signal_date):
    """
    将盈亏验证结果回写到 paper KG（knowledge_graph.inferred_triples）
    
    逻辑：
    - 如果股票涨了（pnl_1d > 0）→ 该股票关联的概念三元组 validated=True
    - 如果股票跌了（pnl_1d < -2%）→ validated=False，降低 confidence
    - 记录 validation_count（验证次数）和 last_validated_at
    """
    kg_db = get_kg_db()
    
    validated_count = 0
    invalidated_count = 0
    
    for sig in updated_signals:
        code = sig['code']
        name = sig['name']
        pnl_1d = sig.get('pnl_1d', 0)
        pnl_3d = sig.get('pnl_3d', 0)
        
        # 判断验证结果
        if pnl_1d > 0:
            outcome = 'validated'
        elif pnl_1d < -2 or pnl_3d < -4:
            outcome = 'invalidated'
        else:
            continue  # 模糊结果不更新
        
        # 找到该股票在 stock_kg 中的概念三元组（subject = code）
        # 这些概念就是该股票被选入TOP5的原因
        stock_triples = list(kg_db.stock_kg.find(
            {'subject': code},
            {'_id': 0, 'object': 1, 'relation': 1}
        ))
        
        if not stock_triples:
            continue
        
        concepts = list(set(t['object'] for t in stock_triples))
        
        # 对每个概念，找到 paper KG 中的 inferred triples（涉及该概念）
        # 即：inferred triples 中 subject 或 object 是这些概念
        for concept in concepts:
            # 找到涉及该概念的 inferred triples
            query = {
                '$or': [
                    {'subject': {'$regex': concept}},
                    {'object': {'$regex': concept}},
                ]
            }
            
            # 获取当前 inferred triple 状态
            for t in kg_db.inferred_triples.find(query):
                t_id = t['_id']
                old_validated = t.get('validated', 'pending')
                old_confidence = t.get('confidence', 0.5)
                
                # 如果之前已经验证过（validated/invalidated），保持结果不覆盖
                if old_validated in ['validated', 'invalidated']:
                    continue
                
                if outcome == 'validated':
                    new_validated = 'validated'
                    new_confidence = min(old_confidence + 0.05, 1.0)  # 置信度+5%，上限1.0
                    validation_count = t.get('validation_count', 0) + 1
                    
                    kg_db.inferred_triples.update_one(
                        {'_id': t_id},
                        {'$set': {
                            'validated': new_validated,
                            'confidence': round(new_confidence, 4),
                            'last_validated_at': datetime.now().isoformat(),
                            'validation_count': validation_count,
                            'validation_source': f'stock_signal_{signal_date}',
                        }}
                    )
                    validated_count += 1
                    
                elif outcome == 'invalidated':
                    new_validated = 'invalidated'
                    new_confidence = max(old_confidence - 0.08, 0.1)  # 置信度-8%，下限0.1
                    validation_count = t.get('validation_count', 0) + 1
                    
                    kg_db.inferred_triples.update_one(
                        {'_id': t_id},
                        {'$set': {
                            'validated': new_validated,
                            'confidence': round(new_confidence, 4),
                            'last_validated_at': datetime.now().isoformat(),
                            'validation_count': validation_count,
                            'validation_source': f'stock_signal_{signal_date}',
                        }}
                    )
                    invalidated_count += 1
    
    print(f"\n  🔄 KG反馈写入：validated +{validated_count}，invalidated +{invalidated_count}")
    return validated_count, invalidated_count


def update_yesterday_signals(date=None):
    """追踪指定日期信号的盈亏，并回写到KG"""
    db = get_stock_db()

    if date is None:
        today = datetime.now().strftime('%Y-%m-%d')
        doc = db['stock_signals'].find_one(sort=[('_id', -1)])
        if not doc:
            print("  无历史信号，跳过")
            return []
        date = doc['_id']
        if date == today:
            print(f"  今日信号（{date}），跳过（等待明日追踪）")
            return []

    doc = db['stock_signals'].find_one({'_id': date})
    if not doc:
        print(f"  无 {date} 信号，跳过")
        return []

    buy_signals = doc.get('buy_signals', [])
    if not buy_signals:
        print(f"  {date} 无买入信号，跳过")
        return []

    print(f"\n{'='*55}")
    print(f"📊 盈亏追踪  信号日={date}")
    print(f"{'='*55}")
    print(f"  筛选日期: {date}  →  入场: T+1开  →  追踪: T+1/T+3/T+5收")

    updated = compute_returns(buy_signals, date)
    print(f"  有效信号: {len(updated)}/{len(buy_signals)} 只")

    if not updated:
        print("  无法计算盈亏（可能T+1数据未入库）")
        return []

    # 统计
    winners = [s for s in updated if s.get('pnl_1d', 0) > 0]
    losers = [s for s in updated if s.get('pnl_1d', 0) <= 0]
    avg_1d = sum(s.get('pnl_1d', 0) for s in updated) / len(updated)
    avg_3d = sum(s.get('pnl_3d', 0) for s in updated) / len(updated)
    avg_5d = sum(s.get('pnl_5d', 0) for s in updated) / len(updated)

    print(f"\n  胜率: {len(winners)}/{len(updated)} ({len(winners)/len(updated)*100:.0f}%)")
    print(f"  平均1日收益: {avg_1d:+.2f}%")
    print(f"  平均3日收益: {avg_3d:+.2f}%")
    print(f"  平均5日收益: {avg_5d:+.2f}%")
    print(f"\n  {'代码':<8} {'名称':<8} {'入场价':<8} {'1日':<8} {'3日':<8} {'5日':<8}")
    print(f"  {'-'*55}")
    for s in sorted(updated, key=lambda x: x.get('pnl_1d', 0), reverse=True):
        print(f"  {s['code']:<8} {s['name']:<8} {s['entry_price']:<8.2f} {s['pnl_1d']:>+7.2f}% {s['pnl_3d']:>+7.2f}% {s['pnl_5d']:>+7.2f}%")

    # 写回 stock_signals
    db['stock_signals'].update_one(
        {'_id': date},
        {'$set': {
            'buy_signals': updated,
            'outcome_computed_at': datetime.now().isoformat(),
        }}
    )

    # 写入 outcome_history
    outcome = {
        '_id': date,
        'date': date,
        'total': len(updated),
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': round(len(winners)/len(updated)*100, 1),
        'avg_pnl_1d': round(avg_1d, 2),
        'avg_pnl_3d': round(avg_3d, 2),
        'avg_pnl_5d': round(avg_5d, 2),
        'signals': updated,
        'generated_at': datetime.now().isoformat(),
    }
    db['stock_outcome_history'].update_one(
        {'_id': date},
        {'$set': outcome},
        upsert=True
    )

    print(f"\n✅ 盈亏已写入 stock_outcome_history/{date}")

    # 反馈写入 KG（自我演化闭环）
    print(f"\n🔄 开始KG反馈写入...")
    v, inv = validate_kg_triples(updated, date)
    print(f"  KG演化完成：validated +{v}，invalidated +{inv}")

    return updated


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=None, help='信号日期 YYYY-MM-DD（默认追踪最近一个有信号的日期）')
    args = parser.parse_args()
    update_yesterday_signals(date=args.date)