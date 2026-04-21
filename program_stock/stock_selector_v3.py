#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股精准选股系统 V3 — 实战版
截止: 2026-03-27 | 目标: <=5只
策略: 动量+资金+趋势位置 为核心，财务辅助验证
"""

from pymongo import MongoClient
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

CUTOFF_DATE = "2026-03-27"
DB_URI = 'mongodb://stock:681123@192.168.1.2:27017/admin'

client = MongoClient(DB_URI)
db = client['stock']

print(f"🔍 截点: {CUTOFF_DATE}")
print("="*60)

# ==================== Step 1: K线数据 ====================
klines = {doc['code']: doc for doc in db['k_raw_v3'].find({'date': CUTOFF_DATE})}
code_map = {c['code']: c.get('name', c['code']) for c in db['code'].find({}, {'code': 1, 'name': 1})}

all_dates = sorted(db['k_raw_v3'].distinct('date'))
ci = all_dates.index(CUTOFF_DATE)

# 获取历史数据
hist = {}
for offset in [3, 5, 10, 20, 60]:
    d = all_dates[ci - offset] if ci >= offset else None
    if d:
        for doc in db['k_raw_v3'].find({'date': d}, {'code': 1, 'close': 1, 'high': 1, 'low': 1, 'vol': 1}):
            if doc['code'] not in hist:
                hist[doc['code']] = {}
            hist[doc['code']][d] = doc

rows = []
for code, doc in klines.items():
    if not doc.get('close') or doc['close'] <= 0 or doc.get('open', 0) <= 0:
        continue
    hp = hist.get(code, {})
    d3 = all_dates[ci - 3] if ci >= 3 else None
    d5 = all_dates[ci - 5] if ci >= 5 else None
    d10 = all_dates[ci - 10] if ci >= 10 else None
    d20 = all_dates[ci - 20] if ci >= 20 else None
    d60 = all_dates[ci - 60] if ci >= 60 else None

    p3 = hp.get(d3, {}).get('close') if d3 else None
    p5 = hp.get(d5, {}).get('close') if d5 else None
    p10 = hp.get(d10, {}).get('close') if d10 else None
    p20 = hp.get(d20, {}).get('close') if d20 else None
    p60 = hp.get(d60, {}).get('close') if d60 else None
    v5 = hp.get(d5, {}).get('vol') if d5 else None

    # 高低点 (20日)
    highs = [doc.get('high', 0) for d in [d3, d5, d10, d20] if d and d in hp]
    lows = [doc.get('low', 0) for d in [d3, d5, d10, d20] if d and d in hp]
    highs.append(doc.get('high', 0))
    lows.append(doc.get('low', 0))

    pct = (doc['close'] - doc['open']) / doc['open'] * 100
    r3 = (doc['close'] / p3 - 1) * 100 if p3 else None
    r5 = (doc['close'] / p5 - 1) * 100 if p5 else None
    r10 = (doc['close'] / p10 - 1) * 100 if p10 else None
    r20 = (doc['close'] / p20 - 1) * 100 if p20 else None
    r60 = (doc['close'] / p60 - 1) * 100 if p60 else None
    vr = doc.get('vol', 0) / v5 if v5 and v5 > 0 else None

    rows.append({
        'code': code, 'name': code_map.get(code, code),
        'close': doc['close'], 'open': doc['open'],
        'high': doc.get('high', 0), 'low': doc.get('low', 0),
        'vol': doc.get('vol', 0), 'amount': doc.get('amount', 0),
        'turnover': doc.get('turnover', 0),
        'pct': pct,
        'r3': r3, 'r5': r5, 'r10': r10, 'r20': r20, 'r60': r60,
        'vr': vr, 'v5': v5,
        'highs': highs, 'lows': lows
    })

df = pd.DataFrame(rows)
# 过滤ST/涨跌停
df['name_str'] = df['name'].astype(str)
df = df[~df['name_str'].str.contains('ST|退市')]
df = df[(df['pct'] < 9.5) & (df['pct'] > -9.5)]
print(f"\n📥 有效股票: {len(df)}只")

# ==================== Step 2: 财务数据 (取最新可用) ====================
# 业绩快报/年报
yjbb = list(db['fin_yjbb'].aggregate([
    {'$sort': {'最新公告日期': -1}},
    {'$group': {'_id': '$股票代码', 'doc': {'$first': '$$ROOT'}}},
    {'$replaceRoot': {'newRoot': '$doc'}}
]))
df_yjbb = pd.DataFrame(yjbb)
if not df_yjbb.empty and '股票代码' in df_yjbb.columns:
    df_yjbb = df_yjbb.rename(columns={'股票代码': 'code'})
    for col in ['净资产收益率', '净利润-同比增长', '营业总收入-同比增长', '每股经营现金流量']:
        if col in df_yjbb.columns:
            df_yjbb[col] = pd.to_numeric(df_yjbb[col], errors='coerce')
    df_yjbb = df_yjbb[['code', '净资产收益率', '净利润-同比增长', '营业总收入-同比增长', '每股经营现金流量']]

# 资产负债
zcfz = list(db['fin_zcfz'].aggregate([
    {'$sort': {'公告日期': -1}},
    {'$group': {'_id': '$股票代码', 'doc': {'$first': '$$ROOT'}}},
    {'$replaceRoot': {'newRoot': '$doc'}}
]))
df_zcfz = pd.DataFrame(zcfz)
if not df_zcfz.empty and '股票代码' in df_zcfz.columns:
    df_zcfz = df_zcfz.rename(columns={'股票代码': 'code'})
    if '资产负债率' in df_zcfz.columns:
        df_zcfz['资产负债率'] = pd.to_numeric(df_zcfz['资产负债率'], errors='coerce')
    df_zcfz = df_zcfz[['code', '资产负债率']]

# 分析师
df_forecast = pd.DataFrame(list(db['fin_forecast'].find({}, {'_id': 0, '股票代码': 1, '机构投资评级(近六个月)-买入': 1}))).rename(columns={'股票代码': 'code'})
df_forecast['机构投资评级(近六个月)-买入'] = pd.to_numeric(df_forecast['机构投资评级(近六个月)-买入'], errors='coerce')

# 合并
df = df.merge(df_yjbb, on='code', how='left')
df = df.merge(df_zcfz, on='code', how='left')
df = df.merge(df_forecast, on='code', how='left')

# ==================== Step 3: 精准筛选 ====================
print("\n🎯 开始精准筛选...")
print("-" * 60)

candidates = []

for _, row in df.iterrows():
    code = row['code']
    name = row['name']
    close = row['close']
    name_str = row['name_str']

    # ===== A. 动量核 心要求 =====
    # 5日 > 0 AND 20日 > 0 (趋势向上)
    r5 = row['r5']
    r10 = row['r10']
    r20 = row['r20']
    r60 = row['r60']
    if r5 is None or r20 is None:
        continue
    if not (r5 > 0 and r20 > 0):
        continue

    # ===== B. 资金放大 (要求5日前有一定流动性，排除北交所/极低流动性假象) =====
    v5 = row['v5']
    if not v5 or v5 < 5000:  # 5日前成交量<5000手，排除流动性极差的股票
        continue
    vr = row['vr']
    if not vr or vr < 1.5:
        continue

    # ===== C. 价格位置 35%-80% =====
    highs = row['highs']
    lows = row['lows']
    high_20d = max(highs) if highs else close
    low_20d = min(lows) if lows else close
    if high_20d <= low_20d or close <= 0:
        continue
    price_pos = (close - low_20d) / (high_20d - low_20d) * 100
    if price_pos < 35 or price_pos > 80:
        continue

    # ===== D. 换手率健康 =====
    turnover = row['turnover']
    if not turnover or turnover < 1.0 or turnover > 12:
        continue

    # ===== E. 今日涨幅合理 =====
    pct = row['pct']
    if pct < -5 or pct > 8:  # 不追涨停，也不大跌
        continue

    # ===== F. 财务辅助验证 (有则验证，无则跳过) =====
    roe = row.get('净资产收益率')
    ni_growth = row.get('净利润-同比增长')
    debt = row.get('资产负债率')
    ocf = row.get('每股经营现金流量')
    buy_rating = row.get('机构投资评级(近六个月)-买入')

    fin_score = 0
    fin_detail = []

    if pd.notna(buy_rating) and buy_rating >= 5:
        fin_score += 1
        fin_detail.append(f"买入评级{int(buy_rating)}")
    if pd.notna(roe) and roe > 10:
        fin_score += 1
        fin_detail.append(f"ROE{roe:.1f}%")
    if pd.notna(ni_growth) and ni_growth > 15:
        fin_score += 1
        fin_detail.append(f"净利增{ni_growth:.0f}%")
    if pd.notna(debt) and debt < 65:
        fin_score += 1
        fin_detail.append(f"负债率{debt:.0f}%")

    # ===== G. 买入时机信号 =====
    # 连续放量上涨: 5日正 AND (量比>1.5 OR 换手率>3%)
    buy_signal = "✅强烈买入" if (r5 > 3 and vr > 2.0) else "✅买入"
    if r5 < 0 or vr < 1.3:
        buy_signal = "⚠️观望"

    candidates.append({
        'code': code, 'name': name, 'close': close,
        'pct': round(pct, 2), 'turnover': turnover, 'vr': round(vr, 2) if vr else None,
        'r5': round(r5, 2), 'r10': round(r10, 2) if r10 else None,
        'r20': round(r20, 2), 'r60': round(r60, 2) if r60 else None,
        'price_pos': round(price_pos, 1),
        'roe': round(roe, 2) if pd.notna(roe) else None,
        'ni_growth': round(ni_growth, 2) if pd.notna(ni_growth) else None,
        'buy_rating': int(buy_rating) if pd.notna(buy_rating) else None,
        'fin_score': fin_score,
        'fin_detail': ', '.join(fin_detail) if fin_detail else '财务数据不足',
        'buy_signal': buy_signal,
    })

print(f"   动量+资金+位置初筛: {len(candidates)}只")

# 按: 动量质量(50%) + 资金参与(30%) + 财务辅助(20%) 排序
df_c = pd.DataFrame(candidates)
if df_c.empty:
    print("\n❌ 无满足条件的股票!")
    print("\n💡 当前市场: 2026-03-27")
    print("   可能原因: 当时市场整体偏弱，强势股较少")
    exit()

# 综合评分
df_c['momentum'] = df_c['r5'] * 0.4 + df_c['r20'] * 0.3 + (df_c['r10'].fillna(0)) * 0.3
df_c['volume_score'] = df_c['vr'].fillna(0) * 20
df_c['fin_weight'] = df_c['fin_score'] / 4  # 最多4分
df_c['综合分'] = df_c['momentum'] * 0.5 + df_c['volume_score'] * 0.3 + df_c['fin_weight'] * 20 * 0.2

df_c = df_c.sort_values('综合分', ascending=False)
top5 = df_c.head(5)

# ==================== Step 4: 输出结果 ====================
print("\n" + "="*70)
print("🏆 A股精准选股 — Top 5 (2026-03-27)")
print("="*70)

for i, (_, row) in enumerate(top5.iterrows(), 1):
    print(f"\n{'='*70}")
    print(f"#{i}  {row['code']} {row['name']}")
    print(f"{'='*70}")
    print(f"  📍 收盘: {row['close']:.2f} | 今日涨跌: {row['pct']}% | 换手率: {row['turnover']:.2f}% | 量比: {row['vr']}x")
    print(f"  📈 动量: 5日 +{row['r5']}% | 10日 +{row['r10']}% | 20日 +{row['r20']}% | 60日 +{row['r60']}%")
    print(f"  📊 价格位置: {row['price_pos']}% (20日区间)")
    print(f"  🏅 财务: {row['fin_detail']}")
    print(f"  ⭐ 买入信号: {row['buy_signal']}")
    print(f"  🎯 综合评分: {row['综合分']:.1f}")

# 买入理由
print("\n" + "="*70)
print("📋 买入理由总结")
print("="*70)
for i, (_, row) in enumerate(top5.iterrows(), 1):
    reasons = []
    if row['r5'] > 5: reasons.append(f"✅ 5日涨{row['r5']}%，短线动能强劲")
    elif row['r5'] > 0: reasons.append(f"✅ 5日正动量，趋势向上")
    if row['r20'] > 10: reasons.append(f"✅ 20日涨{row['r20']}%，中期趋势确认")
    if row['vr'] and row['vr'] > 2: reasons.append(f"✅ 量比{row['vr']}x，放量明显")
    if row['vr'] and row['vr'] > 1.5: reasons.append(f"✅ 资金持续介入")
    if row['price_pos'] and 40 < row['price_pos'] < 70: reasons.append(f"✅ 价格在中部，上涨空间充足")
    if row['buy_rating'] and row['buy_rating'] >= 5: reasons.append(f"✅ {row['buy_rating']}家机构买入评级")
    if row['roe'] and row['roe'] > 10: reasons.append(f"✅ ROE {row['roe']}%，盈利能力强")
    if row['ni_growth'] and row['ni_growth'] > 30: reasons.append(f"✅ 净利润增长{row['ni_growth']}%")
    if row['fin_score'] >= 3: reasons.append(f"✅ 财务质量好({row['fin_score']}/4)")

    print(f"\n#{i} {row['name']} ({row['code']}):")
    for r in reasons:
        print(f"   {r}")

print("\n" + "="*70)
print("⚠️ 风险提示:")
print("  - 选股基于2026-03-27静态数据，后续需动态跟踪")
print("  - 建议配合大盘环境、板块轮动综合判断")
print("  - 严格止损，建议单票仓位<=20%")
print("="*70)

# 保存
output = f"/home/node/.openclaw/workspace/workareas/quant/精准选股V3_{CUTOFF_DATE}.csv"
top5.to_csv(output, index=False, encoding='utf-8-sig')
print(f"\n💾 已保存: {output}")
