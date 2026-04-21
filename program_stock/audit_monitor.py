#!/usr/bin/env python
# coding=utf-8
import pandas as pd
import numpy as np
from pymongo import MongoClient
import datetime
import sys

DB_URI = 'mongodb://stock:681123@192.168.1.2:27017/admin'

def run_audit():
    client = MongoClient(DB_URI)
    db = client['stock']
    
    # 1. 批量加载今日行情与因子
    latest_doc = db['k_raw_v3'].find_one(sort=[('date', -1)])
    if not latest_doc: return print("❌ 库中无数据")
    today_dt = latest_doc['date']
    print(f"\n🕵️ [Pandas 加速审计] 锁定日期: {today_dt}")

    # 一次性读取今日所有数据
    df_raw = pd.DataFrame(list(db['k_raw_v3'].find({'date': today_dt})))
    df_fac = pd.DataFrame(list(db['k_factors_v3'].find({'date': today_dt})))

    if df_raw.empty: return print("❌ 今日行情数据缺失")
    
    audit_passed = True

    # --- [1] 对齐审计 (Symmetry) ---
    print(f"   [1/4] 主从对齐校验... ", end="", flush=True)
    if len(df_raw) == len(df_fac):
        print(f"✅ 完美对称 ({len(df_raw)})")
    else:
        print(f"❌ 数量不一致 ({len(df_raw)} vs {len(df_fac)})")
        audit_passed = False

    # --- [2] 物理量纲校验 (Vectorized) ---
    print(f"   [2/4] 物理量纲校验 (Price/Amount vs Vol)... ", end="", flush=True)
    # 选取有效交易数据进行校验
    valid = df_raw[df_raw['amount'] > 1000].copy()
    if not valid.empty:
        valid['ratio'] = valid['vol'] / (valid['amount'] / valid['close'])
        # 计算比例接近 1.0 (股) 的记录占比
        unit_errors = len(valid[(valid['ratio'] > 0.7) & (valid['ratio'] < 1.3)])
        if unit_errors == 0:
            print(f"✅ 量纲正确 (手)")
        else:
            print(f"❌ 发现 {unit_errors} 条疑似股/手量纲混淆记录！")
            audit_passed = False

    # --- [3] 脏数据校验 (Vectorized) ---
    print("   [3/4] 字段脏数据检查... ", end="", flush=True)
    dirty = df_raw[(df_raw['vol'] > 0) & ((df_raw['amount'] == 0) | (df_raw['turnover'] == 0))]
    if len(dirty) == 0:
        print("✅ 0 脏数据")
    else:
        print(f"❌ 发现 {len(dirty)} 条脏记录！")
        audit_passed = False

    # --- [4] 覆盖率校验 ---
    print("   [4/4] 活跃资产覆盖率... ", end="", flush=True)
    active_codes = set(db['code'].distinct('code'))
    missing_codes = active_codes - set(df_raw['code'])
    coverage = (len(active_codes) - len(missing_codes)) / len(active_codes) * 100
    if coverage > 90:
        print(f"✅ {coverage:.2f}%")
    else:
        print(f"❌ 覆盖率过低 ({coverage:.2f}%)")
        audit_passed = False

    print("\n" + "="*50)
    if audit_passed:
        print("🥇 审计结论：[Green Light]")
        sync_val = today_dt.replace('-', '')
        db['update_date'].update_one({'lastest': {'$exists': True}}, {'$set': {'lastest': sync_val, 'updated_at': datetime.datetime.now()}}, upsert=True)
        print(f"✅ 指针已更新至: {sync_val}")
    else:
        print("🛑 审计结论：数据链路存在缺陷！ [Red Light]")
    print("="*50 + "\n")

def check_time_lock():
    now = datetime.datetime.now().strftime("%H:%M")
    if "08:30" <= now <= "16:00":
        print(f"❌ [安全锁定] {now} 禁止运行。"); sys.exit(0)

if __name__ == '__main__':
    check_time_lock(); run_audit()
