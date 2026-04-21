"""
前复权价格获取 — pandas 高效实现
数据源: k_raw_v3 (实时) + k_factors_v3 (后复权因子)
公式: 前复权价 = raw × hfq_latest / hfq_date
注意: hfq_factor 是后复权因子，前复权公式有一定误差（历史越早误差越大）
      对均线多头排列判断影响有限（相对关系）
"""

import sys
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np
import pymongo
from pymongo import MongoClient

MONGO_URI = "mongodb://stock:681123@192.168.1.2:27017/admin"
MONGO_DB = "stock"

# ─────────────────────────────────────────────────────────────
# MongoDB 连接
# ─────────────────────────────────────────────────────────────
_client = None

def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    return _client

def get_db():
    return get_client()[MONGO_DB]


# ─────────────────────────────────────────────────────────────
# 批量获取单只股票前复权数据（pandas merge 方式）
# 返回: pd.DataFrame with columns [date, open, high, low, close_adj, volume]
# ─────────────────────────────────────────────────────────────
def get_adjusted_price_batch(code: str, lookback_days: int = 365) -> pd.DataFrame:
    """
    高效获取前复权日线数据
    用 pandas merge k_raw_v3 + k_factors_v3，按公式计算前复权价
    """
    db = get_db()
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    # 并行读两个集合
    raw_cur = list(
        db["k_raw_v3"].find(
            {"code": code, "date": {"$gte": start_date, "$lte": end_date}},
            {"_id": 0, "date": 1, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}
        ).sort("date", 1)
    )
    fac_cur = list(
        db["k_factors_v3"].find(
            {"code": code, "date": {"$gte": start_date, "$lte": end_date}},
            {"_id": 0, "date": 1, "hfq_factor": 1}
        ).sort("date", 1)
    )

    if not raw_cur:
        return pd.DataFrame()

    raw_df = pd.DataFrame(raw_cur).rename(columns={"close": "raw_close", "vol": "raw_vol"})
    fac_df = pd.DataFrame(fac_cur)

    # 取最新的 hfq 作为 hfq_latest
    hfq_latest = fac_df["hfq_factor"].iloc[-1] if not fac_df.empty else 1.0

    # Merge
    merged = raw_df.merge(fac_df, on="date", how="left")
    merged["hfq_factor"] = merged["hfq_factor"].fillna(hfq_latest)

    # 前复权价 = raw × hfq_latest / hfq_date
    merged["close_adj"] = merged["raw_close"] * hfq_latest / merged["hfq_factor"]

    # 后复权价 = raw × hfq_factor（用于参考）
    merged["close_hfq"] = merged["raw_close"] * merged["hfq_factor"]

    # 开高低也做复权（用同一因子）
    merged["open_adj"]   = merged["open"]  * hfq_latest / merged["hfq_factor"]
    merged["high_adj"]   = merged["high"]  * hfq_latest / merged["hfq_factor"]
    merged["low_adj"]    = merged["low"]   * hfq_latest / merged["hfq_factor"]

    merged["date"] = pd.to_datetime(merged["date"])
    merged = merged.sort_values("date").reset_index(drop=True)
    return merged


# ─────────────────────────────────────────────────────────────
# 批量获取多只股票前复权数据（减少数据库往返）
# ─────────────────────────────────────────────────────────────
def get_adjusted_prices_multi(codes: List[str], lookback_days: int = 365) -> dict:
    """
    批量获取多只股票前复权数据
    返回: {code: pd.DataFrame}
    """
    db = get_db()
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    # 一次性读 raw
    raw_cur = list(
        db["k_raw_v3"].find(
            {"code": {"$in": codes}, "date": {"$gte": start_date, "$lte": end_date}},
            {"_id": 0, "code": 1, "date": 1, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}
        )
    )
    # 一次性读 factors
    fac_cur = list(
        db["k_factors_v3"].find(
            {"code": {"$in": codes}, "date": {"$gte": start_date, "$lte": end_date}},
            {"_id": 0, "code": 1, "date": 1, "hfq_factor": 1}
        )
    )

    raw_df = pd.DataFrame(raw_cur).rename(columns={"close": "raw_close", "vol": "raw_vol"})
    fac_df = pd.DataFrame(fac_cur)

    if raw_df.empty:
        return {c: pd.DataFrame() for c in codes}

    # 按股票分别处理
    result = {}
    for code in codes:
        r = raw_df[raw_df["code"] == code].copy()
        f = fac_df[fac_df["code"] == code].copy()
        if r.empty:
            result[code] = pd.DataFrame()
            continue

        hfq_latest = f["hfq_factor"].iloc[-1] if not f.empty else 1.0

        m = r.merge(f[["date", "hfq_factor"]], on="date", how="left")
        m["hfq_factor"] = m["hfq_factor"].fillna(hfq_latest)
        m["close_adj"]  = m["raw_close"] * hfq_latest / m["hfq_factor"]
        m["open_adj"]    = m["open"]      * hfq_latest / m["hfq_factor"]
        m["high_adj"]    = m["high"]      * hfq_latest / m["hfq_factor"]
        m["low_adj"]     = m["low"]       * hfq_latest / m["hfq_factor"]
        m["date"]        = pd.to_datetime(m["date"])
        m                = m.sort_values("date").reset_index(drop=True)
        result[code] = m

    return result


# ─────────────────────────────────────────────────────────────
# 周线转换
# ─────────────────────────────────────────────────────────────
def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """日线 DataFrame → 周线 DataFrame（前复权价）"""
    if df.empty:
        return pd.DataFrame()

    d = df.copy()
    d["week"]     = d["date"].dt.isocalendar().week
    d["year"]     = d["date"].dt.isocalendar().year
    d["year_week"] = d["year"].astype(str) + "-" + d["week"].astype(str).str.zfill(2)

    weekly = d.groupby("year_week").agg(
        week_start=("date", "min"),
        open   =("open_adj",   "first"),
        high   =("high_adj",   "max"),
        low    =("low_adj",    "min"),
        close  =("close_adj",  "last"),
        volume =("raw_vol",    "sum"),
    ).reset_index()
    weekly["code"] = df["code"].iloc[0] if "code" in df.columns else ""
    return weekly


# ─────────────────────────────────────────────────────────────
# 测试
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("测试 pandas merge 前复权计算...")
    df = get_adjusted_price_batch("000001", lookback_days=30)
    if not df.empty:
        print(f"\n000001 近30天前复权数据 ({len(df)} 条):")
        print(df[["date", "raw_close", "hfq_factor", "close_adj", "close_hfq"]].tail(10).to_string(index=False))

        # 验证: 最新一天 adj = raw（因为 hfq_latest/hfq_latest = 1）
        latest = df.iloc[-1]
        print(f"\n最新: {latest['date'].date()}, raw={latest['raw_close']}, adj={latest['close_adj']:.2f}")
        print(f"后复权验证: raw × hfq = {latest['raw_close']} × {latest['hfq_factor']:.4f} = {latest['raw_close']*latest['hfq_factor']:.2f}")
    else:
        print("无数据")
