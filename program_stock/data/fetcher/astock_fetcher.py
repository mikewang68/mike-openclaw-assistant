"""
A股 MongoDB 数据拉取模块
功能：从本地MongoDB读取K线、财务数据、账户信息
"""

import sys
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path

import pandas as pd
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

# MongoDB 配置（与 /program/knowledge/config.py 一致）
MONGO_URI = "mongodb://stock:681123@192.168.1.2:27017/admin"
MONGO_DB = "stock"


def get_client() -> MongoClient:
    """获取MongoDB连接"""
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)


def get_klines(code: str = None, start_date: str = None,
               end_date: str = None, collection: str = "k_raw_v3",
               limit: int = None) -> pd.DataFrame:
    """
    读取K线数据
    code: 股票代码，如 "000001"（不带交易所后缀）
          None表示读取所有股票
    start_date/end_date: "2024-01-01" 格式
    collection: k_raw_v3 (新) 或 k_data (旧)
    """
    client = get_client()
    db = client[MONGO_DB]
    coll = db[collection]

    query = {}
    if code:
        query["code"] = code
    if start_date or end_date:
        date_query = {}
        if start_date:
            date_query["$gte"] = start_date
        if end_date:
            date_query["$lte"] = end_date
        query["date"] = date_query

    cursor = coll.find(query, {"_id": 0}).sort("date", 1)
    if limit:
        cursor = cursor.limit(limit)

    df = pd.DataFrame(list(cursor))

    if df.empty:
        return df

    # 转换日期
    df["date"] = pd.to_datetime(df["date"])

    # 统一列名
    if "open" in df.columns and "开盘" not in df.columns:
        # k_raw_v3 格式已经是英文
        pass
    elif "开盘" in df.columns:
        df = df.rename(columns={
            "代码": "code", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "换手率": "turnover", "日期": "date"
        })

    client.close()
    return df


def get_code_list() -> pd.DataFrame:
    """获取所有股票代码列表"""
    client = get_client()
    db = client[MONGO_DB]
    coll = db["code"]

    cursor = coll.find({}, {"_id": 0, "code": 1, "name": 1, "industry": 1,
                            "PE": 1, "PB": 1, "conception": 1})
    df = pd.DataFrame(list(cursor))
    client.close()
    return df


def get_account() -> dict:
    """获取当前账户信息"""
    client = get_client()
    db = client[MONGO_DB]
    coll = db["accounts"]

    accounts = list(coll.find())
    client.close()
    return accounts


def get_orders(limit: int = 100) -> pd.DataFrame:
    """获取最近成交订单"""
    client = get_client()
    db = client[MONGO_DB]
    coll = db["orders"]

    cursor = coll.find().sort("created_at", -1).limit(limit)
    df = pd.DataFrame(list(cursor))

    if not df.empty:
        df = df.drop(columns=["_id"], errors="ignore")
        if "created_at" in df.columns:
            df["created_at"] = pd.to_datetime(df["created_at"])

    client.close()
    return df


def get_financial_data(code: str = None,
                       table: str = "fin_lrb") -> pd.DataFrame:
    """获取财务报表数据（利润表/资产负债表/现金流量表）"""
    client = get_client()
    db = client[MONGO_DB]
    coll = db[table]

    query = {}
    if code:
        query["_id"] = code

    cursor = coll.find(query)
    df = pd.DataFrame(list(cursor))

    if not df.empty:
        df = df.rename(columns={"_id": "code"})
        df = df.drop(columns=["_id"], errors="ignore")

    client.close()
    return df


def get_stock_pool(strategy_name: str = None) -> pd.DataFrame:
    """获取股票池"""
    client = get_client()
    db = client[MONGO_DB]
    coll = db["pool"]

    query = {}
    if strategy_name:
        query["strategy_name"] = strategy_name

    cursor = coll.find(query)
    df = pd.DataFrame(list(cursor))

    if not df.empty:
        df = df.drop(columns=["_id"], errors="ignore")

    client.close()
    return df


def to_backtrader_format(df: pd.DataFrame) -> pd.DataFrame:
    """
    将A股数据转换为 Backtrader 格式
    Backtrader 需要: datetime, open, high, low, close, volume
    """
    bt_df = pd.DataFrame()
    bt_df["datetime"] = df["date"]
    bt_df["open"] = df["open"].astype(float)
    bt_df["high"] = df["high"].astype(float)
    bt_df["low"] = df["low"].astype(float)
    bt_df["close"] = df["close"].astype(float)
    vol_col = "vol" if "vol" in df.columns else "volume"
    bt_df["volume"] = df[vol_col].astype(float)
    bt_df = bt_df.set_index("datetime")
    return bt_df


if __name__ == "__main__":
    print("=== A股 MongoDB Fetcher Test ===")

    # 测试1: 获取股票列表
    print("\n[1] Stock code list:")
    codes = get_code_list()
    print(f"  Total: {len(codes)} stocks")
    print(codes.head(3).to_string())

    # 测试2: 获取单只股票K线
    print("\n[2] K-line for 000001 (last 5):")
    df = get_klines(code="000001", collection="k_raw_v3", limit=5)
    if not df.empty:
        print(df[["date", "code", "open", "high", "low", "close", "volume"]].to_string())
    else:
        print("  No data")

    # 测试3: 获取账户信息
    print("\n[3] Account info:")
    accounts = get_account()
    for a in accounts:
        print(f"  {a.get('name')} - cash: {a.get('cash')}, stocks: {len(a.get('stocks', []))}")

    # 测试4: 获取最近订单
    print("\n[4] Recent orders:")
    orders = get_orders(limit=3)
    if not orders.empty:
        print(orders[["date", "code", "name", "action", "price", "quantity"]].to_string())

    # 测试5: 获取利润表
    print("\n[5] Financial data (fin_lrb) for 000001:")
    fin = get_financial_data(code="000001", table="fin_lrb")
    if not fin.empty:
        print(f"  Fields: {list(fin.columns)[:8]}")
        print(fin.head(1).to_string())

    print("\n✅ A股 fetcher test complete")
