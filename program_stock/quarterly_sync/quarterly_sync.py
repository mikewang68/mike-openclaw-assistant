#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quarterly_sync.py - 季度财务数据同步主程序 v2

功能：
1. 从 akshare 获取全量财务报表（资产负债表、利润表、现金流量表）
2. 获取财务分析指标（ROE/ROA/毛利率等）
3. 获取公司基本信息（法人/注册地/主营业务）
4. 获取行业分类
5. Upsert 入 MongoDB stock 数据库

使用方式：
  python3 quarterly_sync.py --type all        # 全量同步
  python3 quarterly_sync.py --type fin_stmt   # 仅财务报表
  python3 quarterly_sync.py --type indicator  # 仅财务指标
  python3 quarterly_sync.py --type profile    # 仅公司信息
  python3 quarterly_sync.py --type industry   # 仅行业分类（一次性）
  python3 quarterly_sync.py --resume          # 从断点继续

Cron 时间点（Asia/Shanghai）：
  年报+一季报：5月10日 10:00
  半年报：9月10日 10:00
  三季报：10月31日 10:00
  年报预告：2月15日 10:00
"""

import os
import sys
import argparse
import json
import time
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd
import pymongo
from pymongo import UpdateOne

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from fetcher import (
    symbol_em,
    fetch_balance_sheet, fetch_profit_sheet, fetch_cash_flow_sheet,
    fetch_financial_indicator, fetch_company_profile, fetch_industry_class
)

DB_URI = os.environ.get(
    'MONGO_URI',
    'mongodb://stock:681123@192.168.1.2:27017/admin'
)
STATE_FILE = os.path.join(BASE_DIR, '.sync_state.json')

counter_lock = Lock()
stats = {'ok': 0, 'fail': 0, 'skip': 0}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

def get_all_codes() -> list:
    client = pymongo.MongoClient(DB_URI, serverSelectionTimeoutMS=10000)
    db = client['stock']
    codes = [r['code'] for r in db['code'].find({}, {'code': 1})]
    client.close()
    return sorted(codes)

def _colls():
    client = pymongo.MongoClient(DB_URI, serverSelectionTimeoutMS=10000)
    db = client['stock']
    return {
        'fin_zcfz': db['fin_zcfz'],
        'fin_lrb': db['fin_lrb'],
        'fin_xjll': db['fin_xjll'],
        'fin_indicator': db['fin_indicator'],
        'company_profile': db['company_profile'],
        'industry_class': db['industry_class'],
    }

# ─── 同步函数 ──────────────────────────────────────────────

def sync_financial_stmt(code: str, colls: dict) -> str:
    """同步单只股票的三张财务报表"""
    global stats
    try:
        bdf = fetch_balance_sheet(code)
        ldf = fetch_profit_sheet(code)
        cdf = fetch_cash_flow_sheet(code)

        def make_ops(df, suffix):
            if df is None or df.empty:
                return []
            ops = []
            for _, row in df.iterrows():
                rdate = str(row.get('REPORT_DATE', ''))[:10]
                if not rdate:
                    continue
                doc = row.to_dict()
                doc['股票代码'] = code
                doc['报告日期'] = rdate
                doc['_id'] = f"{code}_{rdate}_{suffix}"
                ops.append(UpdateOne({'_id': doc['_id']}, {'$set': doc}, upsert=True))
            return ops

        bops = make_ops(bdf, 'zcfz')
        lops = make_ops(ldf, 'lrb')
        cops = make_ops(cdf, 'xjll')

        if bops: colls['fin_zcfz'].bulk_write(bops, ordered=False)
        if lops: colls['fin_lrb'].bulk_write(lops, ordered=False)
        if cops: colls['fin_xjll'].bulk_write(cops, ordered=False)

        with counter_lock:
            stats['ok'] += 1
        return 'ok'
    except Exception as e:
        with counter_lock:
            stats['fail'] += 1
        return f'fail:{e}'


def sync_indicator(code: str, colls: dict) -> str:
    """同步单只股票的财务分析指标"""
    global stats
    try:
        df = fetch_financial_indicator(code)
        if df is None or df.empty:
            with counter_lock:
                stats['skip'] += 1
            return 'skip'

        ops = []
        for _, row in df.iterrows():
            rdate = str(row.get('REPORT_DATE', ''))[:10]
            if not rdate:
                continue
            doc = row.to_dict()
            doc['股票代码'] = code
            doc['报告日期'] = rdate
            doc['_id'] = f"{code}_{rdate}"
            ops.append(UpdateOne({'_id': doc['_id']}, {'$set': doc}, upsert=True))

        if ops:
            colls['fin_indicator'].bulk_write(ops, ordered=False)

        with counter_lock:
            stats['ok'] += 1
        return 'ok'
    except Exception as e:
        with counter_lock:
            stats['fail'] += 1
        return f'fail:{e}'


def sync_profile(code: str, colls: dict) -> str:
    """同步单只股票的公司基本信息"""
    global stats
    try:
        profile = fetch_company_profile(code)
        if profile is None:
            with counter_lock:
                stats['skip'] += 1
            return 'skip'

        colls['company_profile'].update_one(
            {'_id': code},
            {'$set': profile},
            upsert=True
        )
        with counter_lock:
            stats['ok'] += 1
        return 'ok'
    except Exception as e:
        with counter_lock:
            stats['fail'] += 1
        return f'fail:{e}'


def sync_industry(colls: dict) -> str:
    """同步证监会行业分类（一次性）"""
    global stats
    try:
        df = fetch_industry_class()
        if df is None or df.empty:
            return 'skip'

        ops = []
        for _, row in df.iterrows():
            doc = row.to_dict()
            doc['_id'] = str(doc.get('类目编码', ''))
            ops.append(UpdateOne({'_id': doc['_id']}, {'$set': doc}, upsert=True))

        if ops:
            colls['industry_class'].bulk_write(ops, ordered=False)

        with counter_lock:
            stats['ok'] += 1
        return 'ok'
    except Exception as e:
        with counter_lock:
            stats['fail'] += 1
        return f'fail:{e}'


# ─── 主流程 ─────────────────────────────────────────────────

def run_sync(sync_type: str, batch_size: int = 50, max_workers: int = 10, resume: bool = False):
    print(f"\n{'='*60}")
    print(f"📊 季度财务同步  type={sync_type}  workers={max_workers}")
    print(f"{'='*60}")
    print(f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    state = load_state() if resume else {}
    colls = _colls()
    start_time = time.time()

    if sync_type == 'industry':
        print("\n🔄 同步行业分类...")
        sync_industry(colls)
        print(f"✅ 完成 ({time.time()-start_time:.1f}s)")
        return

    codes = get_all_codes()
    total = len(codes)
    print(f"股票总数: {total}")

    done_key = f'done_{sync_type}'
    done_set_key = f'done_set_{sync_type}'

    if resume and done_set_key in state:
        done_set = set(state[done_set_key])
        codes = [c for c in codes if c not in done_set]
        print(f"📍 断点续传：跳过 {len(done_set)} 只，剩余 {len(codes)} 只")

    SYNC_FN = {
        'fin_stmt': sync_financial_stmt,
        'indicator': sync_indicator,
        'profile': sync_profile,
    }.get(sync_type, sync_financial_stmt)

    done_set = set(state.get(done_set_key, [])) if resume else set()
    done_list = state.get(done_key, []) if resume else []
    batch_done = []

    def progress(i, status):
        if i % 50 == 0 or status.startswith('fail'):
            elapsed = time.time() - start_time
            ok = stats['ok']
            fail = stats['fail']
            skip = stats['skip']
            rate = ok / elapsed if elapsed > 0 else 1
            eta = (total - i) / rate / 60 if rate > 0 else 0
            print(f"  [{i}/{total}] ok={ok} fail={fail} skip={skip}  ETA={eta:.1f}min  last={status}")

    print(f"\n🚀 开始同步 ({sync_type})...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for i, code in enumerate(codes):
            future = pool.submit(SYNC_FN, code, colls)
            futures[future] = (i, code)
            # 控制提交节奏
            if i > 0 and i % (max_workers * 3) == 0:
                time.sleep(0.2)

        for future in as_completed(futures):
            i, code = futures[future]
            status = future.result()
            progress(i, status)
            batch_done.append(code)
            done_set.add(code)

            # 每200只保存一次断点
            if len(done_set) % 200 == 0:
                state[done_key] = list(done_set)
                state[done_set_key] = list(done_set)
                save_state(state)

    elapsed = time.time() - start_time
    print(f"\n✅ 完成！总耗时: {elapsed/60:.1f}min")
    print(f"   成功: {stats['ok']}  失败: {stats['fail']}  跳过: {stats['skip']}")

    state[done_key] = list(done_set)
    state[done_set_key] = list(done_set)
    state[f'stats_{sync_type}'] = dict(stats)
    save_state(state)
    print(f"   断点已保存")


# ─── CLI ──────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--type', default='all',
                        choices=['all', 'fin_stmt', 'indicator', 'profile', 'industry'])
    parser.add_argument('--batch', type=int, default=50)
    parser.add_argument('--workers', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()

    if args.type == 'all':
        for t in ['industry', 'fin_stmt', 'indicator', 'profile']:
            stats['ok'] = stats['fail'] = stats['skip'] = 0
            run_sync(t, args.batch, args.workers, args.resume)
            time.sleep(3)
    else:
        run_sync(args.type, args.batch, args.workers, args.resume)
