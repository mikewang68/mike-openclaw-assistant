"""
回测：日线择时策略（含止损止盈规则）
止损：-5% 离场
离场：5日最高点回撤 -10%
信号：回踩MA5/MA10 + 缩量 + 次日反弹确认 + 3日脱离成本区
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import pandas as pd
import numpy as np
from pymongo import MongoClient

MONGO_URI = "mongodb://stock:681123@192.168.1.2:27017/admin"
MONGO_DB  = "stock"

# ─────────────────────────────────────────────────────────────
# 参数
# ─────────────────────────────────────────────────────────────
MA5_PERIOD    = 5
MA10_PERIOD   = 10
VOL_MA        = 20
VOL_SHRINK    = 0.70
PULLBACK_TOL  = 0.02
STOP_LOSS_PCT = 0.05    # 止损 5%
TRAIL_PCT     = 0.10    # 跟踪止盈：从5日高点回撤10%
MAX_HOLD_DAYS = 15      # 最大持仓天数
NO_GAIN_EXIT  = 0.05    # 3天无+5%涨幅则离场
LOOKBACK_DAYS = 250      # 回测历史天数
VOL_SURGE    = 2.0     # 5日均量需≥2倍20日均量（放量确认）

# ─────────────────────────────────────────────────────────────
# 数据加载
# ─────────────────────────────────────────────────────────────
def get_db():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)[MONGO_DB]

def load_daily_data(codes: List[str], lookback_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    db = get_db()
    end_dt   = datetime.now().strftime("%Y-%m-%d")
    start_dt = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    raw_cur = list(db["k_raw_v3"].find(
        {"code": {"$in": codes}, "date": {"$gte": start_dt, "$lte": end_dt}},
        {"_id": 0, "code": 1, "date": 1, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}
    ))
    fac_cur = list(db["k_factors_v3"].find(
        {"code": {"$in": codes}, "date": {"$gte": start_dt, "$lte": end_dt}},
        {"_id": 0, "code": 1, "date": 1, "hfq_factor": 1}
    ))

    raw_df = pd.DataFrame(raw_cur).rename(columns={"close": "raw_close", "vol": "raw_vol"})
    fac_df = pd.DataFrame(fac_cur)

    latest_fac = fac_df.groupby("code").last().reset_index()
    hfq_map = latest_fac.set_index("code")["hfq_factor"].to_dict()
    hfq_global = fac_df["hfq_factor"].iloc[-1]

    m = raw_df.merge(fac_df, on=["code", "date"], how="left")
    m["hfq_factor"] = m["hfq_factor"].fillna(m["code"].map(hfq_map).fillna(hfq_global))
    hfq_latest_s = m["code"].map(hfq_map).fillna(hfq_global)
    m["close_adj"] = m["raw_close"] * hfq_latest_s / m["hfq_factor"]
    m["open_adj"]  = m["open"]  * hfq_latest_s / m["hfq_factor"]
    m["high_adj"]  = m["high"]  * hfq_latest_s / m["hfq_factor"]
    m["low_adj"]   = m["low"]   * hfq_latest_s / m["hfq_factor"]
    m["date"] = pd.to_datetime(m["date"])
    m = m.sort_values(["code", "date"]).reset_index(drop=True)
    return m


# ─────────────────────────────────────────────────────────────
# 计算指标
# ─────────────────────────────────────────────────────────────
def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for p in (MA5_PERIOD, MA10_PERIOD):
        df[f"ma{p}"] = df.groupby("code")["close_adj"].transform(
            lambda x: x.rolling(window=p, min_periods=p).mean()
        )
    df["vol_ma20"] = df.groupby("code")["raw_vol"].transform(
        lambda x: x.rolling(window=VOL_MA, min_periods=VOL_MA).mean()
    )
    df["vol_ratio"] = df["raw_vol"] / df["vol_ma20"]
    df["vol_ma5"]  = df.groupby("code")["raw_vol"].transform(
        lambda x: x.rolling(window=5, min_periods=5).mean()
    )

    # 次日数据（shift(-1) = 次日）
    df["next_close"] = df.groupby("code")["close_adj"].shift(-1)
    df["next_open"]  = df.groupby("code")["open_adj"].shift(-1)
    return df


# ─────────────────────────────────────────────────────────────
# 核心回测逻辑
# 对每只股票，从第21天开始扫描信号
# 入场：信号日次日以开盘价买入
# 止损：持仓亏损 -5%
# 离场：3日最高点回撤 -7%（每天检查）
# ─────────────────────────────────────────────────────────────
def backtest_stock(code: str, df: pd.DataFrame) -> List[Dict]:
    """
    返回每笔交易的结果：{entry_date, entry_price, exit_date, exit_price, pnl_pct, exit_reason}
    """
    s = df[df["code"] == code].copy()
    s = s.sort_values("date").reset_index(drop=True)
    if len(s) < 25:
        return []

    trades = []
    in_position = False
    entry_price = 0.0
    entry_date  = None
    peak_price  = 0.0   # 持仓期最高价
    stop_loss   = 0.0   # 止损价 = entry × 0.95

    for i in range(20, len(s) - 1):  # 留1天给次日
        row  = s.iloc[i]
        today_close = row["close_adj"]
        today_ma5   = row.get(f"ma{MA5_PERIOD}")
        today_ma10  = row.get(f"ma{MA10_PERIOD}")
        vol_ratio   = row["vol_ratio"]

        if not in_position:
            # ── 信号判断 ──
            # 1. 回踩 MA5 或 MA10
            pullback = False
            if today_ma5 and abs(today_close - today_ma5) / today_ma5 < PULLBACK_TOL:
                pullback = True
                pullback_which = "MA5"
            elif today_ma10 and abs(today_close - today_ma10) / today_ma10 < PULLBACK_TOL:
                pullback = True
                pullback_which = "MA10"

            # 2. 缩量
            shrink = vol_ratio < VOL_SHRINK

            # 3. 次日反弹确认（入场条件）
            next_row = s.iloc[i + 1]
            next_close = next_row["close_adj"]
            rebound_ok = (today_ma5 and next_close > today_ma5)

            if pullback and shrink and rebound_ok:
                # 以次日开盘价买入
                entry_price = next_row["open_adj"]
                if entry_price <= 0:
                    continue
                entry_date  = next_row["date"]
                peak_price  = entry_price
                stop_loss   = entry_price * (1 - STOP_LOSS_PCT)
                in_position = True
                hold_count  = 0

        else:
            # ── 持仓期 ──
            hold_count += 1
            current_close = today_close
            peak_price   = max(peak_price, current_close)

            # 日内止损（收盘跌破止损价）
            if current_close < stop_loss:
                trades.append({
                    "code":        code,
                    "entry_date":  entry_date.strftime("%Y-%m-%d"),
                    "entry_price": round(entry_price, 4),
                    "exit_date":   row["date"].strftime("%Y-%m-%d"),
                    "exit_price":  round(current_close, 4),
                    "hold_days":   hold_count,
                    "pnl_pct":     round((current_close - entry_price) / entry_price * 100, 2),
                    "exit_reason": "止损",
                })
                in_position = False
                continue

            # 跟踪止盈：5日最高点回撤 -10%
            if hold_count >= 5:
                # 5日窗口的最高价
                win_start = i - hold_count + 1
                win_rows   = s.iloc[win_start:i + 1]
                high_5d    = win_rows["high_adj"].max()
                exit_trigger = high_5d * (1 - TRAIL_PCT)

                if current_close < exit_trigger:
                    trades.append({
                        "code":        code,
                        "entry_date":  entry_date.strftime("%Y-%m-%d"),
                        "entry_price": round(entry_price, 4),
                        "exit_date":   row["date"].strftime("%Y-%m-%d"),
                        "exit_price":  round(current_close, 4),
                        "hold_days":   hold_count,
                        "pnl_pct":     round((current_close - entry_price) / entry_price * 100, 2),
                        "exit_reason": "回撤离场",
                    })
                    in_position = False
                    continue

            # ── 3日脱离成本区检查 ──
            # Day 3 收盘价需 >= entry × 1.05，未达到则收盘价离场
            if hold_count == 3 and entry_price > 0:
                if current_close < entry_price * 1.05:
                    trades.append({
                        "code":        code,
                        "entry_date":  entry_date.strftime("%Y-%m-%d"),
                        "entry_price": round(entry_price, 4),
                        "exit_date":   row["date"].strftime("%Y-%m-%d"),
                        "exit_price":  round(current_close, 4),
                        "hold_days":   hold_count,
                        "pnl_pct":     round((current_close - entry_price) / entry_price * 100, 2),
                        "exit_reason": "3日未脱离成本区",
                    })
                    in_position = False
                    continue

            # 持满MAX_HOLD_DAYS强制离场
            if hold_count >= MAX_HOLD_DAYS:
                trades.append({
                    "code":        code,
                    "entry_date":  entry_date.strftime("%Y-%m-%d"),
                    "entry_price": round(entry_price, 4),
                    "exit_date":   row["date"].strftime("%Y-%m-%d"),
                    "exit_price":  round(current_close, 4),
                    "hold_days":   hold_count,
                    "pnl_pct":     round((current_close - entry_price) / entry_price * 100, 2),
                    "exit_reason": "到期强制离场",
                })
                in_position = False

    return trades


# ─────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 70)
    print("回测：日线择时策略（含止损止盈）")
    print(f"止损: -{STOP_LOSS_PCT*100:.0f}%  |  离场: 5日高点×{1-TRAIL_PCT:.2f}  |  缩量<{VOL_SHRINK*100:.0f}%  |  最大持仓{MAX_HOLD_DAYS}天")
    print("=" * 70)

    import time
    t0 = time.time()

    # 读取周线候选股
    db = get_db()
    pool = db["weekly_pool"].find_one(sort=[("_id", -1)])
    if not pool:
        print("❌ 未找到 weekly_pool")
        sys.exit(1)

    week_codes = [s["code"] for s in pool["stocks"]]
    print(f"\n📋 周线候选股: {len(week_codes)} 只")

    # 加载数据
    df = load_daily_data(week_codes, LOOKBACK_DAYS)
    t1 = time.time()
    print(f"  数据加载: {t1-t0:.1f}秒, {len(df):,} 行")

    # 计算指标
    df = calc_indicators(df)
    t2 = time.time()
    print(f"  指标计算: {t2-t1:.1f}秒")

    # 回测每只股票
    print("  正在回测...")
    all_trades = []
    for code in week_codes:
        trades = backtest_stock(code, df)
        all_trades.extend(trades)

    t3 = time.time()
    print(f"  回测完成: {t3-t2:.1f}秒, 共 {len(all_trades)} 笔交易")

    if not all_trades:
        print("❌ 无交易记录")
        sys.exit(1)

    trades_df = pd.DataFrame(all_trades)

    # ── 统计 ──
    total     = len(trades_df)
    wins      = len(trades_df[trades_df["pnl_pct"] > 0])
    losses    = len(trades_df[trades_df["pnl_pct"] <= 0])
    win_rate  = wins / total * 100 if total > 0 else 0
    avg_pnl   = trades_df["pnl_pct"].mean()
    avg_hold  = trades_df["hold_days"].mean()
    avg_win   = trades_df[trades_df["pnl_pct"] > 0]["pnl_pct"].mean()
    avg_loss  = trades_df[trades_df["pnl_pct"] <= 0]["pnl_pct"].mean()

    # 止损 vs 离场统计
    stop_count    = len(trades_df[trades_df["exit_reason"] == "止损"])
    trail_count   = len(trades_df[trades_df["exit_reason"] == "回撤离场"])
    expire_count  = len(trades_df[trades_df["exit_reason"] == "到期强制离场"])

    print(f"\n{'='*60}")
    print(f"【回测结果】约 {LOOKBACK_DAYS} 天历史，{len(week_codes)} 只候选股")
    print(f"{'='*60}")
    print(f"  总交易次数:   {total} 笔")
    print(f"  盈利次数:     {wins} 笔 ({win_rate:.1f}%)")
    print(f"  亏损次数:     {losses} 笔")
    print(f"  平均盈亏:     {avg_pnl:+.2f}%")
    print(f"  平均持仓:     {avg_hold:.1f} 天")
    print(f"  平均盈利:     {avg_win:+.2f}%")
    print(f"  平均亏损:     {avg_loss:+.2f}%")
    print()
    print(f"  止损触发:     {stop_count} 次 ({stop_count/total*100:.1f}%)")
    print(f"  回撤离场:     {trail_count} 次 ({trail_count/total*100:.1f}%)")
    print(f"  到期强制离场: {expire_count} 次 ({expire_count/total*100:.1f}%)")
    print()

    # Top10 最大盈利
    print(f"  Top10 最大盈利:")
    top10 = trades_df.nlargest(10, "pnl_pct")
    for _, t in top10.iterrows():
        print(f"    {t['code']} {t['entry_date']} → {t['exit_date']} {t['pnl_pct']:+.1f}% ({t['exit_reason']})")

    # Top10 最大亏损
    print(f"\n  Top10 最大亏损:")
    bot10 = trades_df.nsmallest(10, "pnl_pct")
    for _, t in bot10.iterrows():
        print(f"    {t['code']} {t['entry_date']} → {t['exit_date']} {t['pnl_pct']:+.1f}% ({t['exit_reason']})")
