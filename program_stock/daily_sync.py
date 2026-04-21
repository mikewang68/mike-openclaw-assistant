#!/usr/bin/env python
# coding=utf-8
import akshare as ak
import requests
import pandas as pd
import numpy as np
from pymongo import MongoClient, UpdateOne
import concurrent.futures
import datetime
import os
import sys

# 强制清空代理
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

DB_URI = 'mongodb://stock:681123@192.168.1.2:27017/admin'

def fetch_tencent_qt(codes):
    """首选源：腾讯 QT (Batch)"""
    def get_symbol(c):
        # 0, 1, 3 -> sz | 8, 4, 9 -> bj | others -> sh
        if c.startswith(('0', '1', '3')): return f"sz{c}"
        if c.startswith(('8', '4', '9')): return f"bj{c}"
        return f"sh{c}"
    
    symbols = [get_symbol(c) for c in codes]
    url = f"http://qt.gtimg.cn/q={','.join(symbols)}"
    try:
        r = requests.get(url, timeout=10)
        lines = r.text.split(';')
        data = []
        for line in lines:
            if '=' not in line: continue
            p = line.split('"')[1].split('~')
            if len(p) < 40: continue
            
            # 腾讯源：成交量(手), 成交额(万元), 换手率(%)
            data.append({
                'code': p[2], 
                'date': f"{p[30][:4]}-{p[30][4:6]}-{p[30][6:8]}",
                'open': float(p[5]), 'high': float(p[33]), 'low': float(p[34]), 'close': float(p[3]),
                'vol': float(p[36]), 
                'amount': float(p[37]) * 10000, # 万元 -> 元
                'turnover': float(p[38]),
                'source': 'Tencent'
            })
        return data
    except: return []

def sync_assets_and_data():
    client = MongoClient(DB_URI)
    db = client['stock']
    
    # --- [A] 资产库自更新 (Auto Discovery) ---
    print("📡 正在同步全量资产名单 (Discovery from Sina/EM)...")
    try:
        # 1. 使用轻量级的新浪接口获取代码名录 (最稳健)
        code_df = ak.stock_info_a_code_name()
        if not code_df.empty:
            online_codes = set(code_df['code'].tolist())
            local_codes = set(db['code'].distinct('code'))
            new_codes = online_codes - local_codes
            
            if new_codes:
                print(f"🎉 发现 {len(new_codes)} 只新上市股票！正在录入...")
                new_ops = []
                for _, row in code_df.iterrows():
                    if row['code'] in new_codes:
                        new_ops.append(UpdateOne({'_id': row['code']}, {'$set': {
                            'code': row['code'], 'name': row['name'], 
                            'last_updated': datetime.datetime.now().strftime("%Y%m%d"),
                            'industry': '待分类'
                        }}, upsert=True))
                if new_ops: db['code'].bulk_write(new_ops)
        
        # 2. 尝试从东财补充 PE/PB 字段 (可选，若限频则跳过)
        try:
            spot_df = ak.stock_zh_a_spot_em()
            if not spot_df.empty:
                spot_df = spot_df.rename(columns={'代码':'code', '市盈率-动态':'PE', '市净率':'PB'})
                update_ops = []
                for _, row in spot_df.iterrows():
                    update_ops.append(UpdateOne({'_id': row['code']}, {'$set': {
                        'PE': float(row.get('PE', 0)) if row.get('PE') != '-' else 0,
                        'PB': float(row.get('PB', 0)) if row.get('PB') != '-' else 0
                    }}))
                if update_ops: db['code'].bulk_write(update_ops[:1000]) # 示例：仅更新部分或全部
        except: print("⚠️ 提示：东财 PE/PB 补充接口受限，跳过。")
            
    except Exception as e:
        print(f"⚠️ 资产自更新模块警告: {e}")

    # --- [B] 指针校验 ---
    last_sync = db['update_date'].find_one({'lastest': {'$exists': True}})
    last_val = last_sync['lastest'] if last_sync else "19000101"

    # --- [C] 多源抓取 ---
    all_codes = sorted(db['code'].distinct('code'))
    print(f"🚀 开始全量同步 (目标: {len(all_codes)} 只)...")
    
    # 1. 腾讯源 (主推)
    batches = [all_codes[i:i+100] for i in range(0, len(all_codes), 100)]
    t_data = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(fetch_tencent_qt, b) for b in batches]
        for f in concurrent.futures.as_completed(futures): t_data.extend(f.result())
    
    df_t = pd.DataFrame(t_data)
    
    # 锚定同步日期 (以腾讯源最新日期为准)
    if not df_t.empty:
        sync_date = df_t['date'].iloc[0]
    else:
        sync_date = datetime.datetime.now().strftime("%Y-%m-%d")

    # 2. 补齐逻辑 (若缺失则启动东财)
    success_codes = set(df_t['code']) if not df_t.empty else set()
    missing = list(set(all_codes) - success_codes)
    
    if missing:
        print(f"🚨 补课启动 (缺失 {len(missing)} 只)，调用东财源...")
        try:
            em_df = ak.stock_zh_a_spot_em()
            em_df['代码'] = em_df['代码'].astype(str).str.zfill(6)
            
            # 东财源：成交量(股), 成交额(元), 换手率(%)
            em_df = em_df.rename(columns={
                '代码': 'code', '今开': 'open', '最高': 'high', '最低': 'low', 
                '最新价': 'close', '成交量': 'vol', '成交额': 'amount', '换手率': 'turnover'
            })
            em_df['date'] = sync_date
            em_df['source'] = 'EM'
            
            # 过滤并保留缺失的部分
            em_missing = em_df[em_df['code'].isin(missing)].copy()
            
            # 合并
            df_final = pd.concat([df_t, em_missing]) if not df_t.empty else em_missing
        except Exception as e:
            print(f"⚠️ 东财源获取失败: {e}")
            df_final = df_t
    else:
        df_final = df_t

    if df_final.empty: return print("❌ 抓取失败")

    # --- [C] 智能量纲对齐 (Smart Unit Alignment) ---
    print(f"📏 正在执行跨源量纲对齐...")
    def align_volume(row):
        # 审计标准期望 vol 为“手”
        # 逻辑：如果 vol / (amount/close) 接近 1.0，说明是“股”，需转为“手”
        if row['amount'] > 0 and row['close'] > 0:
            est_shares = row['amount'] / row['close']
            ratio = row['vol'] / est_shares
            if 0.7 < ratio < 1.3: 
                return row['vol'] / 100.0
        return row['vol']
    
    df_final['vol'] = df_final.apply(align_volume, axis=1)

    # --- [D] 指针与日期校验 ---
    if sync_date.replace('-', '') <= last_val:
        return print(f"⏩ 跳过：{sync_date} 已同步。")

    # --- [E] 批量因子注入与入库 ---
    # 预加载因子 Map (提高效率)
    last_f_record = db['k_factors_v3'].find_one(sort=[('date', -1)])
    last_date = last_f_record['date'] if last_f_record else None
    
    if last_date:
        print(f"📅 使用 {last_date} 的因子作为基准...")
        f_map = {f['code']: f['hfq_factor'] for f in db['k_factors_v3'].find({'date': last_date})}
    else:
        print("⚠️ 未发现历史因子数据，将初始化为 1.0")
        f_map = {}
    
    raw_ops, fac_ops = [], []
    total = len(df_final)
    print(f"📦 正在准备入库数据 (共 {total} 条)...")
    
    for i, (_, row) in enumerate(df_final.iterrows()):
        c, dt = row['code'], row['date']
        did = f"{dt}:{c}"
        
        if (i + 1) % 500 == 0:
            print(f"⏳ 已处理 {i + 1}/{total} ({((i + 1)/total*100):.1f}%)")
        
        # 因子逻辑：从 Map 获取，缺失则从数据库查一次并存入 map
        hfq = f_map.get(c)
        if hfq is None:
            last_f = db['k_factors_v3'].find_one({'code': c}, sort=[('date', -1)])
            hfq = last_f['hfq_factor'] if last_f else 1.0
            f_map[c] = hfq
            
        item = row.to_dict()
        raw_ops.append(UpdateOne({'_id': did}, {'$set': item}, upsert=True))
        fac_ops.append(UpdateOne({'_id': did}, {'$set': {'code': c, 'date': dt, 'hfq_factor': float(hfq)}}, upsert=True))

    if raw_ops:
        print(f"💾 正在写入数据库 (Bulk Write {len(raw_ops)} ops)...")
        db['k_raw_v3'].bulk_write(raw_ops, ordered=False)
        db['k_factors_v3'].bulk_write(fac_ops, ordered=False)
        print(f"🏁 同步达成！日期: {sync_date} | 覆盖: {len(df_final)} 只")

def check_time_lock():
    now = datetime.datetime.now().strftime("%H:%M")
    if "08:30" <= now <= "16:00":
        print(f"❌ [安全锁定] {now} 禁止运行。"); sys.exit(0)

if __name__ == '__main__':
    check_time_lock(); sync_assets_and_data()
