"""
止损止盈（简单2条）：
  1. 固定止损：亏损 -5%
  2. 离场：3日最高点回撤 -7%
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from datetime import datetime, timedelta
from typing import List, Dict, Optional
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

# ─────────────────────────────────────────────────────────────
# 参数
# ─────────────────────────────────────────────────────────────
MA5_PERIOD  = 5
MA10_PERIOD = 10
MA20_PERIOD = 20

VOL_MA       = 20          # 成交量均线周期
VOL_SHRINK   = 0.70        # 缩量阈值（< 30% of 20日均量）
PULLBACK_TOL = 0.02         # 回踩容忍度（偏离MA < 2%）
LOOKBACK_DAYS = 60          # 日线回溯天数

# ─────────────────────────────────────────────────────────────
# 止损 / 止盈 参数
# ─────────────────────────────────────────────────────────────
STOP_LOSS_PCT    = -0.05      # 止损：入场价 × 0.95
TAKE_PROFIT_PCT = 0.15      # 离场：3日最高点 × 0.93

# ─────────────────────────────────────────────────────────────
# 数据获取：一次查询全量
# ─────────────────────────────────────────────────────────────
def load_all_daily_adjusted(week_codes: List[str], lookback_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """
    一次查询所有候选股的日线数据（前复权）
    返回: DataFrame [code, date, raw_close, hfq_factor, close_adj, open, high, low, vol]
    """
    db = get_db()
    end_dt   = datetime.now().strftime("%Y-%m-%d")
    start_dt = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    print(f"  📡 查询日线数据 ({start_dt} ~ {end_dt})，共 {len(week_codes)} 只候选股...")
    raw_cur = list(
        db["k_raw_v3"].find(
            {"code": {"$in": week_codes}, "date": {"$gte": start_dt, "$lte": end_dt}},
            {"_id": 0, "code": 1, "date": 1, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}
        )
    )
    fac_cur = list(
        db["k_factors_v3"].find(
            {"code": {"$in": week_codes}, "date": {"$gte": start_dt, "$lte": end_dt}},
            {"_id": 0, "code": 1, "date": 1, "hfq_factor": 1}
        )
    )

    raw_df = pd.DataFrame(raw_cur).rename(columns={"close": "raw_close", "vol": "raw_vol"})
    fac_df = pd.DataFrame(fac_cur)

    if raw_df.empty:
        return pd.DataFrame()

    # 每只股票用自己的最新因子
    latest_fac = fac_df.groupby("code").last().reset_index()
    hfq_latest_by_code = latest_fac.set_index("code")["hfq_factor"].to_dict()
    hfq_global = fac_df["hfq_factor"].iloc[-1]

    raw_df = raw_df.sort_values(["code", "date"]).reset_index(drop=True)
    fac_df = fac_df.sort_values(["code", "date"]).reset_index(drop=True)

    m = raw_df.merge(fac_df, on=["code", "date"], how="left")
    m["hfq_factor"] = m["hfq_factor"].fillna(m["code"].map(hfq_latest_by_code).fillna(hfq_global))

    # 前复权
    hfq_latest_s = m["code"].map(hfq_latest_by_code).fillna(hfq_global)
    m["close_adj"] = m["raw_close"] * hfq_latest_s / m["hfq_factor"]
    m["open_adj"]   = m["open"]       * hfq_latest_s / m["hfq_factor"]
    m["high_adj"]   = m["high"]       * hfq_latest_s / m["hfq_factor"]
    m["low_adj"]    = m["low"]        * hfq_latest_s / m["hfq_factor"]
    m["date"] = pd.to_datetime(m["date"])
    m = m.sort_values(["code", "date"]).reset_index(drop=True)

    print(f"  ✅ 日线加载完成: {len(m):,} 行, {m['code'].nunique()} 只")
    return m


# ─────────────────────────────────────────────────────────────
# 核心：计算日线技术指标 + 择时信号
# ─────────────────────────────────────────────────────────────
def calc_daily_timing(daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    对全量日线数据计算 MA + 成交量 + 择时信号
    返回满足"缩量回踩"条件的股票
    """
    print("  📊 计算日线 MA + 成交量均线...")
    df = daily_df.copy()

    # 计算 MA (5, 10, 20)
    for p in (MA5_PERIOD, MA10_PERIOD, MA20_PERIOD):
        df[f"ma{p}"] = df.groupby("code")["close_adj"].transform(
            lambda x: x.rolling(window=p, min_periods=p).mean()
        )

    # 计算成交量均线
    df["vol_ma20"] = df.groupby("code")["raw_vol"].transform(
        lambda x: x.rolling(window=VOL_MA, min_periods=VOL_MA).mean()
    )

    # 成交量比率
    df["vol_ratio"] = df["raw_vol"] / df["vol_ma20"]

    # 次日涨跌（用于确认反弹）
    df["next_close"]  = df.groupby("code")["close_adj"].shift(-1)
    df["next_open"]   = df.groupby("code")["open_adj"].shift(-1)
    df["next_is_up"]  = df["next_close"] > df["close_adj"]  # 次日收涨

    # 阳线（收盘 > 开盘）
    df["is_bullish"] = df["close_adj"] > df["open_adj"]

    print(f"  🔍 开始扫描择时信号...")
    return df


def find_timing_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    扫描所有股票，找"缩量回踩 + 反弹确认"的买点
    信号条件：
      1. 当日收盘价回踩 MA5 或 MA10（偏离 < PULLBACK_TOL）
      2. 当日成交量 < VOL_MA * VOL_SHRINK（缩量）
      3. 次日阳线反弹确认（收盘 > MA5）
      4. 当前收盘在 MA5 之上（不是跌破）
    """
    # 条件1: 回踩 MA5 或 MA10（收盘在均线 ±2% 以内）
    pullback_ma5 = (
        (df["close_adj"] <= df["ma5"] * (1 + PULLBACK_TOL)) &
        (df["close_adj"] >= df["ma5"] * (1 - PULLBACK_TOL)) &
        df["ma5"].notna()
    )
    pullback_ma10 = (
        (df["close_adj"] <= df["ma10"] * (1 + PULLBACK_TOL)) &
        (df["close_adj"] >= df["ma10"] * (1 - PULLBACK_TOL)) &
        df["ma10"].notna()
    )
    df["pullback"] = pullback_ma5 | pullback_ma10

    # 条件2: 缩量（< 30% of 20日均量）
    df["shrink"] = df["vol_ratio"] < VOL_SHRINK

    # 条件3: 次日阳线反弹 + 收盘站稳 MA5
    df["rebound_confirm"] = (
        df["next_is_up"].fillna(False) &
        (df["next_close"] > df["ma5"].shift(-1)) &
        df["ma5"].shift(-1).notna()
    )

    # 综合择时信号（当日收盘满足回踩+缩量）
    df["timing_signal"] = df["pullback"] & df["shrink"]

    # 提取有信号的股票
    signals = df[df["timing_signal"]].copy()

    # 对每只股票，只保留最近一次信号
    signals = signals.sort_values("date", ascending=False).groupby("code").first().reset_index()

    print(f"  ✅ 找到 {len(signals)} 只股票满足择时条件")
    return signals


def get_stock_name(code: str) -> str:
    doc = get_db()["code"].find_one({"code": code}, {"_id": 0, "name": 1})
    return doc.get("name", "") if doc else ""


def build_timing_result(signals: pd.DataFrame, daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    构建最终择时结果
    """
    results = []
    for _, row in signals.iterrows():
        code = row["code"]

        # 次日反弹确认
        next_day = daily_df[
            (daily_df["code"] == code) & (daily_df["date"] > row["date"])
        ].nsmallest(1, "date")

        rebound_ok = False
        next_close_val = None
        if not next_day.empty:
            nd = next_day.iloc[0]
            signal_ma5 = row["ma5"]
            rebound_ok = bool(
                pd.notna(nd.get("close_adj")) and
                pd.notna(signal_ma5) and nd["close_adj"] > signal_ma5
            )
            next_close_val = nd["close_adj"]

        # 基本字段
        ma5_val  = row.get("ma5")
        ma10_val = row.get("ma10")
        ma20_val = row.get("ma20")
        close    = row["close_adj"]
        pullback_which = "MA5" if (ma5_val and abs(close - ma5_val) / ma5_val < PULLBACK_TOL) else "MA10"
        dev_pct  = round((close - ma5_val) / ma5_val * 100, 2) if ma5_val else 0

        # 评分
        score = 0.0
        if rebound_ok:
            vol_score = max(0, 1 - row["vol_ratio"] / VOL_SHRINK)
            dev_score = max(0, -dev_pct / 5.0) if dev_pct < 0 else 0
            score = round(vol_score * 0.6 + dev_score * 0.4, 3)

        # 止损止盈
        entry_price = close
        stop_loss   = round(entry_price * 0.95, 2)

        # 3日高点
        three = daily_df[
            (daily_df["code"] == code) &
            (daily_df["date"] >= row["date"]) &
            (daily_df["date"] <= row["date"] + pd.Timedelta(days=3))
        ]
        high_3d     = three["high_adj"].max() if not three.empty else entry_price
        exit_price  = round(high_3d * 0.93, 2)
        profit_space = round((exit_price - entry_price) / entry_price * 100, 1)

        # 最新价 & 当前盈亏
        latest_row   = daily_df[daily_df["code"] == code].nlargest(1, "date").iloc[0]
        latest_price = round(latest_row["close_adj"], 2)
        current_pnl  = round((latest_price - entry_price) / entry_price * 100, 1)

        results.append({
            "code":           code,
            "name":           get_stock_name(code),
            "date":           row["date"].strftime("%Y-%m-%d"),
            "entry_price":    round(entry_price, 2),
            "ma5":            round(ma5_val, 2) if pd.notna(ma5_val) else None,
            "ma20":           round(ma20_val, 2) if pd.notna(ma20_val) else None,
            "pullback_which": pullback_which,
            "vol_ratio":      round(row["vol_ratio"], 3),
            "next_close":     round(next_close_val, 2) if next_close_val else None,
            "rebound_ok":     rebound_ok,
            "stop_loss":      stop_loss,
            "stop_loss_pct":  5,
            "high_3d":        round(high_3d, 2),
            "exit_price":     exit_price,
            "profit_space":    profit_space,
            "latest_price":   latest_price,
            "current_pnl":    current_pnl,
            "score":          score,
            "action":         "✅买入" if rebound_ok else "⏳等待确认",
        })

    if not results:
        return pd.DataFrame()

    result_df = pd.DataFrame(results)
    # 先只保留 rebound_ok=True 的（已确认信号）
    confirmed = result_df[result_df["rebound_ok"] == True].copy()
    # 未确认的单独展示
    unconfirmed = result_df[result_df["rebound_ok"] == False].copy()

    confirmed   = confirmed.sort_values("score", ascending=False).reset_index(drop=True)
    unconfirmed = unconfirmed.sort_values("score", ascending=False).reset_index(drop=True)
    return confirmed, unconfirmed


# ─────────────────────────────────────────────────────────────
# 保存到 MongoDB
# ─────────────────────────────────────────────────────────────
def save_daily_timing(result_df: pd.DataFrame) -> str:
    if result_df.empty:
        print("  ⚠️ 无择时信号，跳过保存")
        return ""

    db = get_db()
    today_str = datetime.now().strftime("%Y-%m-%d")
    week_id   = datetime.now().strftime("%Y-%m-%d")

    doc = {
        "_id":        week_id,
        "date":       today_str,
        "strategy":   "缩量回踩均线v1（日线择时）",
        "total_signals": len(result_df),
        "signals":    result_df.to_dict("records"),
        "created_at": datetime.now(),
    }
    db["daily_timing"].update_one({"_id": week_id}, {"$set": doc}, upsert=True)
    print(f"  💾 已保存到 daily_timing，ID={week_id}")
    return week_id


# ─────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 70)
    print("日线择时 — 缩量回踩均线策略 v1")
    print("=" * 70)

    import time
    t0 = time.time()

    # Step 1: 读取周线候选股
    db = get_db()
    pool_doc = db["weekly_pool"].find_one(sort=[("_id", -1)])
    if not pool_doc:
        print("❌ 未找到 weekly_pool，请先运行周线选股")
        sys.exit(1)

    week_codes = [s["code"] for s in pool_doc["stocks"]]
    print(f"\n📋 周线候选股: {len(week_codes)} 只 (from {pool_doc['_id']})")

    # Step 2: 一次查询所有候选股的日线数据
    daily_df = load_all_daily_adjusted(week_codes, LOOKBACK_DAYS)
    t1 = time.time()
    print(f"  ⏱️ 数据加载: {t1-t0:.1f}秒")

    if daily_df.empty:
        print("❌ 无日线数据")
        sys.exit(1)

    # Step 3: 计算指标 + 找信号
    df = calc_daily_timing(daily_df)
    signals = find_timing_signals(df)
    t2 = time.time()
    print(f"  ⏱️ 信号扫描: {t2-t1:.1f}秒")

    if not signals.empty:
        confirmed, unconfirmed = build_timing_result(signals, daily_df)

        # 保存全部信号
        all_signals = pd.concat([confirmed, unconfirmed], ignore_index=True)
        save_daily_timing(all_signals)
        t3 = time.time()
        print(f"\n⏱️ 总耗时: {t3-t0:.1f}秒")

        print(f"\n{'='*70}")
        print(f"✅ 买入确认 ({len(confirmed)} 只)：")
        cols = ["code","name","date","entry_price","stop_loss","high_3d","exit_price","profit_space","latest_price","current_pnl","score"]
        if not confirmed.empty:
            print(confirmed[cols].to_string(index=False))

        if not unconfirmed.empty:
            print(f"\n⏳ 等待确认 ({len(unconfirmed)} 只，次一跳空高开才能买入）：")
            print(unconfirmed[cols].to_string(index=False))
    else:
        print("\n⚠️ 本周无满足条件的日线择时信号")
