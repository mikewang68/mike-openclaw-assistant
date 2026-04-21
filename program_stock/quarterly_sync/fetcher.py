#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetcher.py - akshare 数据获取封装 v2
支持：财务报表（英文字段）、财务指标、公司信息、行业分类
"""

import time
import random
from typing import Optional

import akshare as ak
import pandas as pd

# ─── 速率限制 ──────────────────────────────────────────────

class RateLimiter:
    def __init__(self, calls_per_sec: float = 5):
        self.interval = 1.0 / calls_per_sec
        self.last_call = 0.0

    def wait(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed + random.uniform(0, 0.03))
        self.last_call = time.time()

limiter = RateLimiter(calls_per_sec=5)

def _call(fn, *args, **kwargs):
    """带速率限制的API调用"""
    limiter.wait()
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        print(f"  ⚠️ API失败 {fn.__name__}: {e}")
        return None

def _to_str(v):
    if pd.isna(v):
        return ''
    if hasattr(v, 'item'):
        try:
            v = v.item()
        except:
            pass
    if isinstance(v, float):
        if abs(v) > 1e10:
            return str(int(v))
        return f"{v:.4g}"
    return str(v)

def clean_df(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """DataFrame 清洗：统一日期列、去除空行"""
    if df is None or df.empty:
        return None
    # 统一报告日期列名
    date_col = None
    for col in ['REPORT_DATE', '报告日期', '日期']:
        if col in df.columns:
            date_col = col
            break
    if date_col and date_col != 'REPORT_DATE':
        df = df.rename(columns={date_col: 'REPORT_DATE'})
    # 过滤无日期行
    df = df[df['REPORT_DATE'].notna()].copy()
    if df.empty:
        return None
    # 统一股票代码列
    for col in ['SECURITY_CODE', '股票代码', '代码']:
        if col in df.columns:
            if col != 'SECURITY_CODE':
                df = df.rename(columns={col: 'SECURITY_CODE'})
            break
    return df


# ─── 工具函数 ─────────────────────────────────────────────

def symbol_em(code: str) -> str:
    """000001 -> 000001.SZ / 600000 -> 600000.SH"""
    if code.startswith(('0', '1', '3')):
        return f"{code}.SZ"
    return f"{code}.SH"


# ─── 财务报表 ─────────────────────────────────────────────

def fetch_balance_sheet(code: str) -> Optional[pd.DataFrame]:
    """资产负债表（英文字段，全量报告期）"""
    df = _call(ak.stock_balance_sheet_by_report_em, symbol_em(code))
    df = clean_df(df)
    if df is None:
        return None
    # 关键列（太多了，只保留最重要的 + 所有原始列）
    # 直接返回全量列，由调用方筛选
    df['_code'] = code
    return df

def fetch_profit_sheet(code: str) -> Optional[pd.DataFrame]:
    """利润表（英文字段，全量报告期）"""
    df = _call(ak.stock_profit_sheet_by_report_em, symbol_em(code))
    df = clean_df(df)
    if df is None:
        return None
    df['_code'] = code
    return df

def fetch_cash_flow_sheet(code: str) -> Optional[pd.DataFrame]:
    """现金流量表（英文字段，全量报告期）"""
    df = _call(ak.stock_cash_flow_sheet_by_report_em, symbol_em(code))
    df = clean_df(df)
    if df is None:
        return None
    df['_code'] = code
    return df


# ─── 财务分析指标 ──────────────────────────────────────────

def fetch_financial_indicator(code: str) -> Optional[pd.DataFrame]:
    """财务分析指标（ROE/ROA/毛利率/净利率等）"""
    df = _call(ak.stock_financial_analysis_indicator_em, symbol_em(code))
    df = clean_df(df)
    if df is None:
        return None
    # 关键指标列（只保留有意义的子集，避免列爆炸）
    key_cols = [
        'REPORT_DATE', 'SECURITY_CODE', 'SECURITY_NAME_ABBR',
        'ROE_AVG',           # 净资产收益率（平均）
        'ROA',               # 总资产净利润率
        'GROSS_PROFIT_MARGIN',  # 销售毛利率
        'NET_PROFIT_MARGIN',    # 销售净利率
        'DEBT_ASSET_RATIO',  # 资产负债率
        'CURRENT_RATIO',      # 流动比率
        'QUICK_RATIO',       # 速动比率
        'ARTURNOVER',        # 应收账款周转率
        'INVENTORY_TURN',    # 存货周转率
        'FATURN',            # 固定资产周转率
        'TATURN',            # 总资产周转率
        'NETPROFIT_YOY',     # 净利润同比
        'OPERATE_INCOME_YOY', # 营业收入同比
        'BASIC_EPS',         # 基本每股收益
        'DILUTED_EPS',       # 稀释每股收益
        'OPERATE_CASHFLOW_PS',# 每股经营现金流
        'GROSS_PROFIT_MARGIN_YOY',  # 毛利率同比变化
        'NETPROFIT_RATIO_QOQ', # 净利率环比
    ]
    existing = [c for c in key_cols if c in df.columns]
    df = df[existing].copy()
    df['_code'] = code
    return df


# ─── 公司基本信息 ─────────────────────────────────────────

def fetch_company_profile(code: str) -> Optional[dict]:
    """公司基本信息"""
    try:
        df = _call(ak.stock_profile_cninfo, code)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            '_id': code,
            '股票代码': code,
            '公司名称':   str(row.get('公司名称', '')),
            '英文名称':   str(row.get('英文名称', '')),
            '所属市场':   str(row.get('所属市场', '')),
            '所属行业':   str(row.get('所属行业', '')),
            '法人代表':   str(row.get('法人代表', '')),
            '注册资金':   str(row.get('注册资金', '')),
            '成立日期':   str(row.get('成立日期', '')),
            '上市日期':   str(row.get('上市日期', '')),
            '主营业务':   str(row.get('主营业务', ''))[:1000],
            '经营范围':   str(row.get('经营范围', ''))[:1000],
            '注册地址':   str(row.get('注册地址', '')),
            '办公地址':   str(row.get('办公地址', '')),
        }
    except Exception as e:
        return None


# ─── 行业分类 ──────────────────────────────────────────────

def fetch_industry_class() -> Optional[pd.DataFrame]:
    """证监会行业分类（全量）"""
    try:
        df = _call(ak.stock_industry_category_cninfo, symbol='证监会行业分类标准')
        return df
    except Exception:
        return None
