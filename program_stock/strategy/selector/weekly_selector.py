"""
周线选股层 — 均线多头排列策略 v4
优化：一次查询 k_raw_v3 + k_factors_v3（全量），pandas 内存计算
性能目标：< 60秒完成 5516 只股票
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from datetime import datetime, timedelta
from typing import List, Dict
import pandas as pd
import numpy as np
from pymongo import MongoClient

MONGO_URI = "mongodb://stock:681123@192.168.1.2:27017/admin"
MONGO_DB  = "stock"

# ─────────────────────────────────────────────────────────────
# MongoDB
# ─────────────────────────────────────────────────────────────
def get_db():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)[MONGO_DB]

def get_all_codes() -> List[str]:
    return [d["code"] for d in get_db()["code"].find({}, {"_id": 0, "code": 1})]

def get_stock_info(code: str) -> Dict:
    return get_db()["code"].find_one(
        {"code": code},
        {"_id": 0, "code": 1, "name": 1, "industry": 1, "PE": 1, "PB": 1, "conception": 1}
    ) or {}

# ─────────────────────────────────────────────────────────────
# 参数
# ─────────────────────────────────────────────────────────────
MA_PERIODS      = (5, 10, 20)
MAX_PE          = 80
MAX_PB          = 10
MAX_RALLY_PCT   = 80   # 从52周低点涨超此值则过滤
RALLY_PENALTY   = 50   # 从低点涨超此值则评分打7折
LOOKBACK_DAYS   = 365

# ─────────────────────────────────────────────────────────────
# 核心：一次查询 + pandas 全量计算
# ─────────────────────────────────────────────────────────────
def load_all_adjusted_prices(lookback_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """
    一次查询 k_raw_v3 + k_factors_v3，全量载入内存
    返回合并后的 DataFrame：[code, date, raw_close, hfq_factor, close_adj]
    """
    db = get_db()
    end_dt   = datetime.now().strftime("%Y-%m-%d")
    start_dt = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    print(f"  📡 正在查询 k_raw_v3 ({start_dt} ~ {end_dt})...")
    raw_cur = list(
        db["k_raw_v3"].find(
            {"date": {"$gte": start_dt, "$lte": end_dt}},
            {"_id": 0, "code": 1, "date": 1, "close": 1, "vol": 1}
        )
    )
    print(f"  📡 正在查询 k_factors_v3 ({start_dt} ~ {end_dt})...")
    fac_cur = list(
        db["k_factors_v3"].find(
            {"date": {"$gte": start_dt, "$lte": end_dt}},
            {"_id": 0, "code": 1, "date": 1, "hfq_factor": 1}
        )
    )

    raw_df = pd.DataFrame(raw_cur).rename(columns={"close": "raw_close", "vol": "raw_vol"})
    fac_df = pd.DataFrame(fac_cur)

    # 取最新的 hfq 作为 hfq_latest
    hfq_latest = fac_df["hfq_factor"].iloc[-1]

    # ── 关键修复：分别按 code+date 排序后再 merge ──
    # 确保 raw 和 fac 的最后一条都是同一股票的同一日期
    raw_df = raw_df.sort_values(["code", "date"]).reset_index(drop=True)
    fac_df = fac_df.sort_values(["code", "date"]).reset_index(drop=True)

    # 用每只股票最新一天的因子作为 hfq_latest（而不是全局最后一条）
    latest_fac = fac_df.groupby("code").last().reset_index()
    hfq_latest_by_code = latest_fac.set_index("code")["hfq_factor"].to_dict()
    hfq_global = fac_df["hfq_factor"].iloc[-1]  # 兜底

    # Merge
    m = raw_df.merge(fac_df, on=["code", "date"], how="left")
    m["hfq_factor"] = m["hfq_factor"].fillna(m["code"].map(hfq_latest_by_code).fillna(hfq_global))

    # 前复权价 = raw × hfq_latest_per_code / hfq_factor_at_date
    hfq_latest_series = m["code"].map(hfq_latest_by_code).fillna(hfq_global)
    m["close_adj"] = m["raw_close"] * hfq_latest_series / m["hfq_factor"]
    m["date"] = pd.to_datetime(m["date"])
    m = m.sort_values(["code", "date"]).reset_index(drop=True)

    # 验证
    chk = m[m["code"]=="000001"]
    hfq_000001 = hfq_latest_by_code.get("000001", hfq_global)
    print(f"  ✅ 加载完成: {len(m):,} 行, {m['code'].nunique()} 只股票")
    print(f"     hfq_000001={hfq_000001:.4f} (与 db 一致: {abs(hfq_000001-161.1857)<0.01})")
    return m


def compute_weekly_ma_bull(df: pd.DataFrame) -> pd.DataFrame:
    """
    对全量 DataFrame 计算周线 MA 多头排列
    返回满足条件的股票列表
    """
    print("  📊 正在转换为周线并计算均线...")

    # 转为周线（按 code + year_week 分组）
    df = df.copy()
    df["week"]      = df["date"].dt.isocalendar().week
    df["year"]      = df["date"].dt.isocalendar().year
    df["year_week"] = df["year"].astype(str) + "-" + df["week"].astype(str).str.zfill(2)

    weekly = df.groupby(["code", "year_week"]).agg(
        week_start=("date", "min"),
        open  =("raw_close", "first"),
        high  =("raw_close", "max"),
        low   =("raw_close", "min"),
        close =("close_adj", "last"),
        volume=("raw_vol",   "sum"),
    ).reset_index()

    print(f"  📈 周线数据: {len(weekly):,} 行 (code×week)")

    # 计算 MA (5, 10, 20)
    print("  🔢 正在计算 MA5/MA10/MA20...")
    for p in MA_PERIODS:
        weekly[f"ma{p}"] = weekly.groupby("code")["close"].transform(
            lambda x: x.rolling(window=p, min_periods=p).mean()
        )

    # ── 新增：放量过滤（5日均量 ≥ 2× 20日均量） ──
    print("  📊 正在计算放量过滤器...")
    df_sorted = df.sort_values(["code", "date"])
    df_sorted["vol_ma5"]  = df_sorted.groupby("code")["raw_vol"].transform(
        lambda x: x.rolling(5, min_periods=5).mean()
    )
    df_sorted["vol_ma20"] = df_sorted.groupby("code")["raw_vol"].transform(
        lambda x: x.rolling(20, min_periods=20).mean()
    )
    df_sorted["vol_ratio"] = df_sorted["vol_ma5"] / df_sorted["vol_ma20"]
    # 取每只股票最新一天的数据
    daily_latest = df_sorted.groupby("code").last().reset_index()
    vol_filter = daily_latest[daily_latest["vol_ratio"] >= 2.0][["code", "vol_ma5", "vol_ma20", "vol_ratio"]]
    print(f"     放量（5日均量≥2×20日均量）股票: {len(vol_filter)} 只")

    # 取每只股票最新一周的数据
    latest = weekly.groupby("code").last().reset_index()

    # MA 多头排列筛选
    print("  🔍 正在筛选 MA 多头排列...")
    cond = (
        (latest["ma5"].notna()) &
        (latest["ma10"].notna()) &
        (latest["ma20"].notna()) &
        (latest["ma5"] > latest["ma10"]) &
        (latest["ma10"] > latest["ma20"])
    )
    bull_df = latest[cond].copy()

    # 计算从52周低点的涨幅
    print("  📏 正在计算追高过滤...")
    low_52w = weekly.groupby("code")["close"].transform(
        lambda x: x.rolling(window=52, min_periods=1).min()
    )
    weekly["low_52w"] = low_52w
    latest_with_low = weekly.groupby("code").last().reset_index()
    bull_df = bull_df.merge(
        latest_with_low[["code", "low_52w", "close"]],
        on="code", suffixes=("", "_latest")
    )
    bull_df["rally_from_low"] = (bull_df["close"] - bull_df["low_52w"]) / bull_df["low_52w"] * 100

    # 追高过滤
    bull_df = bull_df[bull_df["rally_from_low"] <= MAX_RALLY_PCT].copy()
    print(f"  ✅ 追高过滤后剩余: {len(bull_df)} 只")



    return bull_df


def fundamental_filter(codes: List[str]) -> pd.DataFrame:
    """基本面过滤（PE/PB）"""
    db = get_db()
    print("  💰 正在基本面过滤...")
    cursor = db["code"].find(
        {"code": {"$in": codes}},
        {"_id": 0, "code": 1, "name": 1, "industry": 1, "PE": 1, "PB": 1, "conception": 1}
    )
    df = pd.DataFrame(list(cursor))
    df = df[
        (df["PE"].notna()) & (df["PE"] > 0) & (df["PE"] < MAX_PE) &
        (df["PB"].notna()) & (df["PB"] > 0) & (df["PB"] < MAX_PB)
    ]
    print(f"  ✅ 基本面通过: {len(df)} 只")
    return df


def score_and_rank(bull_df: pd.DataFrame, fund_df: pd.DataFrame) -> pd.DataFrame:
    """综合评分与排名"""
    # PE/PB/fundamental 字段来自 fund_df
    merged = bull_df.merge(
        fund_df[["code", "name", "industry", "PE", "PB", "conception"]],
        on="code", how="inner"
    )

    # 追高评分调整
    merged["rally_flag"] = np.where(
        merged["rally_from_low"] > MAX_RALLY_PCT, "⚠️追高",
        np.where(merged["rally_from_low"] < 30, "✅适中", "🔸偏高")
    )
    merged["rally_penalty"] = np.where(merged["rally_from_low"] > RALLY_PENALTY, 0.7, 1.0)

    # 综合评分
    merged["vol_score"]   = 0.3  # 简化
    merged["trend_score"] = 0.3
    merged["fund_score"]  = 1.0 - merged["PE"] / MAX_PE
    merged["composite_score"] = (
        merged["vol_score"] * 0.3 +
        merged["trend_score"] * 0.3 +
        merged["fund_score"] * 0.4
    ) * merged["rally_penalty"]

    merged = merged.sort_values("composite_score", ascending=False).reset_index(drop=True)
    return merged


def save_weekly_pool(df: pd.DataFrame, week_id: str = None) -> str:
    if week_id is None:
        today  = datetime.now()
        week   = today.isocalendar()[1]
        week_id = f"{today.year}-{week:02d}"

    db = get_db()
    monday = today_dt = datetime.now()
    monday = today_dt - timedelta(days=today_dt.weekday())
    sunday = monday + timedelta(days=6)

    doc = {
        "_id": week_id,
        "week_start": monday.strftime("%Y-%m-%d"),
        "week_end":   sunday.strftime("%Y-%m-%d"),
        "strategy":   "均线多头排列v4（一次查询+pandas全量计算）",
        "total_stocks_analyzed": len(get_all_codes()),
        "stocks_selected": len(df),
        "stocks": df.to_dict("records"),
        "created_at": datetime.now(),
    }
    db["weekly_pool"].update_one({"_id": week_id}, {"$set": doc}, upsert=True)
    return week_id


# ─────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 70)
    print("周线选股 v4 — 均线多头排列（一次查询 + pandas 全量）")
    print("=" * 70)

    import time
    t0 = time.time()

    # Step 1: 一次查询全量数据
    all_df = load_all_adjusted_prices(LOOKBACK_DAYS)
    t1 = time.time()
    print(f"  ⏱️ 数据加载: {t1-t0:.1f}秒")

    # Step 2: 计算周线 MA
    bull_df = compute_weekly_ma_bull(all_df)
    t2 = time.time()
    print(f"  ⏱️ MA 计算: {t2-t1:.1f}秒")

    if not bull_df.empty:
        # Step 3: 基本面过滤
        fund_df = fundamental_filter(bull_df["code"].tolist())
        t3 = time.time()
        print(f"  ⏱️ 基本面过滤: {t3-t2:.1f}秒")

        # Step 4: 评分排名
        result_df = score_and_rank(bull_df, fund_df)
        t4 = time.time()
        print(f"  ⏱️ 评分排名: {t4-t3:.1f}秒")

        print(f"\n✅ 共选出 {len(result_df)} 只（总耗时 {t4-t0:.1f}秒）:")
        cols = ["code","name","industry","PE","close","ma5","ma10","ma20",
                "rally_from_low","rally_flag","composite_score"]
        print(result_df[cols].head(20).to_string(index=False))

        week_id = save_weekly_pool(result_df)
        print(f"\n已保存到 weekly_pool，ID={week_id}")
    else:
        print("无符合条件的股票")
