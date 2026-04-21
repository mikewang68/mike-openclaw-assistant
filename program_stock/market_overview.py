#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_overview.py - 盘面概览脚本
盘中实时：指数 + 涨停股 + 热门新闻
用法：python3 market_overview.py
"""

import requests
import akshare as ak
import pandas as pd
from datetime import datetime

# ── 1. 指数实时行情 ───────────────────────────────

def get_index():
    """腾讯实时指数"""
    url = 'https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000688,sz399005,sh000016'
    r = requests.get(url, timeout=5)
    indices = {}
    for line in r.text.strip().split(';'):
        if '=' not in line:
            continue
        p = line.split('"')[1].split('~')
        if len(p) < 35:
            continue
        code = p[2]
        name = p[1]
        price = float(p[3])
        prev = float(p[4])
        high = float(p[33])
        low = float(p[34])
        pct = (price - prev) / prev * 100
        indices[code] = {'name': name, 'price': price, 'prev': prev,
                         'pct': pct, 'high': high, 'low': low}
    return indices

# ── 2. 涨停股池 ──────────────────────────────────

def get_zt_pool():
    """今日涨停股"""
    date_str = datetime.now().strftime('%Y%m%d')
    try:
        df = ak.stock_zt_pool_em(date=date_str)
        if df is None or df.empty:
            date_str = (datetime.now().replace(day=1) if datetime.now().month == 1
                        else datetime.now().replace(day=datetime.now().day - 1)).strftime('%Y%m%d')
            df = ak.stock_zt_pool_em(date=date_str)
        return df
    except Exception:
        return None

# ── 3. 最新财经新闻 ─────────────────────────────

def get_news(limit=8):
    """最新财经新闻"""
    try:
        df = ak.stock_news_em()
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.head(limit).iterrows():
            title = str(row.get('新闻标题', ''))
            time = str(row.get('发布时间', ''))
            src = str(row.get('文章来源', ''))
            url = str(row.get('新闻链接', ''))
            result.append({'title': title, 'time': time, 'source': src, 'url': url})
        return result
    except Exception:
        return []

# ── 4. 输出格式化 ────────────────────────────────

def print_overview():
    print(f"\n{'='*58}")
    print(f"📊 盘面概览  {datetime.now().strftime('%Y-%m-%d %H:%M')} (盘中)")
    print(f"{'='*58}")

    # 指数
    print("\n【主要指数】")
    print(f"  {'名称':<10} {'最新价':>10} {'涨跌幅':>10} {'高':>10} {'低':>10}")
    print(f"  {'-'*50}")
    indices = get_index()
    for code, info in indices.items():
        pct = info['pct']
        arrow = '▲' if pct >= 0 else '▼'
        sign = '+' if pct >= 0 else ''
        print(f"  {info['name']:<10} {info['price']:>10.2f} {sign}{pct:>8.2f}%{arrow}  高:{info['high']:>8.2f}  低:{info['low']:>8.2f}")

    # 涨停股
    print("\n【今日涨停】")
    zt_df = get_zt_pool()
    if zt_df is not None and not zt_df.empty:
        lb_col = '连板数' if '连板数' in zt_df.columns else '连板'
        zt_col = '涨停统计' if '涨停统计' in zt_df.columns else '涨停'
        mkt_col = '流通市值' if '流通市值' in zt_df.columns else '市值'

        # 连板股
        lb = zt_df[zt_df[lb_col] > 1] if lb_col in zt_df.columns else pd.DataFrame()
        print(f"  涨停总数: {len(zt_df)}  |  连板股: {len(lb)}")

        if not lb.empty:
            print(f"\n  {'代码':<8} {'名称':<8} {'连板':>4} {'流通市值':>12} {'涨停统计'}")
            print(f"  {'-'*50}")
            for _, row in lb.head(10).iterrows():
                code = str(row.get('代码', ''))
                name = str(row.get('名称', ''))
                lb_cnt = row.get(lb_col, 1)
                mkt = row.get(mkt_col, 0)
                zt_stat = str(row.get(zt_col, ''))
                if mkt > 1e10:
                    mkt_str = f'{mkt/1e10:.1f}万亿'
                elif mkt > 1e8:
                    mkt_str = f'{mkt/1e8:.0f}亿'
                else:
                    mkt_str = f'{mkt/1e6:.0f}万'
                print(f"  {code:<8} {name:<8} {lb_cnt:>4}x  {mkt_str:>10}  {zt_stat}")

        # 今日首板（前10）
        yb = zt_df[zt_df[lb_col] == 1] if lb_col in zt_df.columns else zt_df
        print(f"\n  【首板股 TOP10】（按市值）")
        if mkt_col in yb.columns:
            yb = yb.sort_values(mkt_col, ascending=False)
        print(f"  {'代码':<8} {'名称':<8} {'流通市值':>12}")
        print(f"  {'-'*36}")
        for _, row in yb.head(10).iterrows():
            code = str(row.get('代码', ''))
            name = str(row.get('名称', ''))
            mkt = row.get(mkt_col, 0)
            if mkt > 1e10:
                mkt_str = f'{mkt/1e10:.1f}万亿'
            elif mkt > 1e8:
                mkt_str = f'{mkt/1e8:.0f}亿'
            else:
                mkt_str = f'{mkt/1e6:.0f}万'
            print(f"  {code:<8} {name:<8} {mkt_str:>12}")
    else:
        print("  暂无法获取涨停数据")

    # 新闻
    print("\n【最新财经新闻】")
    news = get_news(8)
    if news:
        for i, n in enumerate(news, 1):
            print(f"  {i}. {n['title'][:40]}")
            print(f"     {n['time']} | {n['source']}")
    else:
        print("  暂无法获取新闻")

    print(f"\n{'='*58}")


if __name__ == '__main__':
    print_overview()
