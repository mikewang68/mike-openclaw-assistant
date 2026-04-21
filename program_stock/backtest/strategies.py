"""
4大策略回测 — 修复版
"""

import sys, os
sys.path.insert(0, "/program/stock")
os.environ['BACKTRADER_NO_RANK'] = '1'

import backtrader as bt
import pandas as pd
import numpy as np
from data.fetcher.astock_fetcher import get_klines, get_code_list, to_backtrader_format


# ─────────────────────────────────────────────────────────────────
# 策略1: 趋势动量型
# 信号: 价格站上20日均线 + RSI(10)>50 + 成交量萎缩 → 买入
# 退出: 止损5% 或 RSI>75
# ─────────────────────────────────────────────────────────────────
class TrendMomentumStrategy(bt.Strategy):
    params = (
        ("ma_period", 20),
        ("rsi_period", 10),
        ("rsi_threshold", 50),
        ("vol_shrink", 0.5),
        ("stop_loss", 0.05),
    )

    def __init__(self):
        self.order = None
        self.dataclose = self.data0.close
        self.datavol = self.data0.volume
        self.sma20 = bt.indicators.SMA(self.data0.close, period=self.params.ma_period)
        self.rsi = bt.indicators.RSI(self.data0.close, period=self.params.rsi_period)
        self.last_buy = None

    def next(self):
        if self.order:
            return

        price = self.dataclose[0]
        vol = self.datavol[0]

        # 成交量均线
        vol_ma = np.mean([self.datavol[-i] for i in range(1, min(21, len(self)))])
        vol_shrink = vol < vol_ma * self.params.vol_shrink

        # 趋势信号: 价格在均线上方 + RSI健康
        above_ma = price > self.sma20[0]
        rsi_ok = self.rsi[0] > self.params.rsi_threshold

        # 买入条件: 回调到均线附近 + 缩量 + 趋势确认
        prev_price = self.dataclose[-1] if len(self) > 1 else price
        pullback = prev_price < self.sma20[-1] and price >= self.sma20[0] * 0.99

        if not self.position:
            if pullback and above_ma and rsi_ok and vol_shrink:
                self.order = self.buy()
                self.last_buy = price
        else:
            stop = self.last_buy * (1 - self.params.stop_loss) if self.last_buy else 0
            if stop and price < stop:
                self.order = self.sell()
            elif self.rsi[0] > 75:
                self.order = self.sell()


# ─────────────────────────────────────────────────────────────────
# 策略2: 均线多头排列
# 信号: MA5 > MA10 > MA20 (日线) → 强势股
# 退出: MA5 < MA10 或 止损5%
# ─────────────────────────────────────────────────────────────────
class MABullishStrategy(bt.Strategy):
    params = (
        ("ma_s", 5),
        ("ma_m", 10),
        ("ma_l", 20),
        ("stop_loss", 0.05),
    )

    def __init__(self):
        self.order = None
        self.dataclose = self.data0.close
        self.ma5 = bt.indicators.SMA(self.data0.close, period=self.params.ma_s)
        self.ma10 = bt.indicators.SMA(self.data0.close, period=self.params.ma_m)
        self.ma20 = bt.indicators.SMA(self.data0.close, period=self.params.ma_l)
        self.last_buy = None

    def next(self):
        if self.order:
            return

        price = self.dataclose[0]
        if len(self) < self.params.ma_l:
            return

        # 多头排列
        is_bullish = self.ma5[0] > self.ma10[0] > self.ma20[0]
        # 回踩10日线
        near_ma10 = abs(price - self.ma10[0]) / self.ma10[0] < 0.02

        if not self.position:
            if is_bullish and near_ma10:
                self.order = self.buy()
                self.last_buy = price
        else:
            still_bullish = self.ma5[0] > self.ma10[0]
            stop = self.last_buy * (1 - self.params.stop_loss) if self.last_buy else 0
            if (stop and price < stop) or not still_bullish:
                self.order = self.sell()


# ─────────────────────────────────────────────────────────────────
# 策略3: 突破型
# 信号: 日线突破60日最高价 + 成交量放大
# 退出: 止损5% 或 跌破突破位5%
# ─────────────────────────────────────────────────────────────────
class BreakoutStrategy(bt.Strategy):
    params = (
        ("lookback", 60),
        ("breakout_pct", 0.05),
        ("vol_ratio", 1.5),
        ("stop_loss", 0.05),
    )

    def __init__(self):
        self.order = None
        self.dataclose = self.data0.close
        self.datavol = self.data0.volume
        self.last_buy = None
        self.breakout_price = None

    def next(self):
        if self.order:
            return

        price = self.dataclose[0]
        vol = self.datavol[0]

        if len(self) < self.params.lookback + 2:
            return

        # 60日最高价
        prices_60d = [self.dataclose[-i] for i in range(1, min(self.params.lookback + 1, len(self) + 1))]
        max_60d = max(prices_60d)

        vol_ma = np.mean([self.datavol[-i] for i in range(1, min(21, len(self) + 1))])

        is_breakout = price > max_60d * (1 + self.params.breakout_pct)
        vol_confirm = vol > vol_ma * self.params.vol_ratio

        if not self.position:
            if is_breakout and vol_confirm:
                self.order = self.buy()
                self.last_buy = price
                self.breakout_price = price
        else:
            stop = self.last_buy * (1 - self.params.stop_loss) if self.last_buy else 0
            pullback = self.breakout_price and price < self.breakout_price * 0.95
            if (stop and price < stop) or pullback:
                self.order = self.sell()


# ─────────────────────────────────────────────────────────────────
# 策略4: 多因子综合评分
# 因子: 趋势因子 + 动量因子 + RSI健康度 + 成交量确认
# ─────────────────────────────────────────────────────────────────
class MultiFactorStrategy(bt.Strategy):
    params = (
        ("ma_period", 20),
        ("roc_period", 20),
        ("rsi_period", 14),
        ("score_threshold", 0.65),
        ("stop_loss", 0.05),
    )

    def __init__(self):
        self.order = None
        self.dataclose = self.data0.close
        self.datavol = self.data0.volume
        self.sma = bt.indicators.SMA(self.data0.close, period=self.params.ma_period)
        self.rsi = bt.indicators.RSI(self.data0.close, period=self.params.rsi_period)
        self.last_buy = None
        self.price_history = []

    def next(self):
        if self.order:
            return

        price = self.dataclose[0]
        vol = self.datavol[0]

        self.price_history.append(price)
        if len(self.price_history) > self.params.roc_period + 2:
            self.price_history.pop(0)

        if len(self.price_history) < max(self.params.ma_period, self.params.roc_period) + 2:
            return

        # 因子1: 趋势 (0-1)
        trend = min(1.0, max(0.0, (price / self.sma[0] - 0.9) * 10))

        # 因子2: 动量 ROC (0-1)
        roc = (price - self.price_history[-self.params.roc_period-1]) / self.price_history[-self.params.roc_period-1]
        momentum = min(1.0, max(0.0, roc * 5 + 0.5))

        # 因子3: RSI 健康度
        rsi_val = self.rsi[0]
        rsi_health = 1.0 if 40 < rsi_val < 60 else 0.5 if 30 < rsi_val < 70 else 0.0

        # 综合评分
        score = trend * 0.4 + momentum * 0.3 + rsi_health * 0.3

        # 成交量确认
        vol_ma = np.mean([self.datavol[-i] for i in range(1, min(21, len(self) + 1))])
        vol_ok = vol > vol_ma * 0.7

        if not self.position:
            if score > self.params.score_threshold and vol_ok:
                self.order = self.buy()
                self.last_buy = price
        else:
            stop = self.last_buy * (1 - self.params.stop_loss) if self.last_buy else 0
            if (stop and price < stop) or score < 0.3:
                self.order = self.sell()


# ─────────────────────────────────────────────────────────────────
# 单只股票回测
# ─────────────────────────────────────────────────────────────────
STRATEGIES = {
    "趋势动量型": TrendMomentumStrategy,
    "均线多头排列": MABullishStrategy,
    "突破型": BreakoutStrategy,
    "多因子综合": MultiFactorStrategy,
}


def run_single(code, strat_class, strat_name, start="2024-01-01", end="2025-12-31",
               cash=100000.0, commission=0.0005):
    df = get_klines(code=code, start_date=start, end_date=end, collection="k_raw_v3")
    if df.empty or len(df) < 60:
        return None

    df_bt = to_backtrader_format(df)
    if df_bt.empty:
        return None

    try:
        cerebro = bt.Cerebro()
        cerebro.addstrategy(strat_class)
        cerebro.adddata(bt.feeds.PandasData(dataname=df_bt))
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=commission)

        results = cerebro.run()
        final = cerebro.broker.getvalue()
        ret = (final - cash) / cash * 100

        # 交易次数
        strat = results[0]
        trade_count = 0
        if hasattr(strat, 'order') and strat.order is not None:
            trade_count = 1

        return {
            "strategy": strat_name,
            "code": code,
            "return_pct": round(ret, 2),
            "final_value": round(final, 2),
            "init_cash": cash,
        }
    except Exception as e:
        return {"strategy": strat_name, "code": code, "return_pct": None, "error": str(e)[:60]}


def run_all_on_stock(code, start="2024-01-01", end="2025-12-31"):
    results = []
    for name, strat in STRATEGIES.items():
        r = run_single(code, strat, name, start, end)
        if r:
            results.append(r)
    return results


def run_universe(codes, start="2024-01-01", end="2025-12-31"):
    rows = []
    total = len(codes)
    for i, code in enumerate(codes):
        for name, strat in STRATEGIES.items():
            r = run_single(code, strat, name, start, end)
            if r:
                rows.append(r)
        if (i + 1) % 20 == 0:
            print(f"  ... {i+1}/{total} stocks done")
    return pd.DataFrame(rows)


if __name__ == "__main__":
    print("=" * 70)
    print("4大策略A股回测 (2024-01-01 ~ 2025-12-31)")
    print("=" * 70)

    # 获取股票列表
    codes_df = get_code_list()
    all_codes = codes_df["code"].tolist()
    print(f"Total stocks: {len(all_codes)}")

    # 先看几只代表性股票
    samples = ["000001", "000002", "600519", "600036", "000858",
                "300750", "601318", "000333", "002475", "300015"]

    print("\n[SAMPLE] 10只代表性股票:")
    print("-" * 70)
    all_results = []
    for code in samples:
        if code not in all_codes:
            continue
        results = run_all_on_stock(code)
        all_results.extend(results)
        rets = {r["strategy"]: r["return_pct"] for r in results}
        best = max(rets.values()) if rets else 0
        print(f"  {code}: 趋势={rets.get('趋势动量型')}, 多头={rets.get('均线多头排列')}, "
              f"突破={rets.get('突破型')}, 多因子={rets.get('多因子综合')} | Best: {best:+.1f}%")

    # 全市场扫描
    print(f"\n[UNIVERSE] 全市场扫描 ({len(all_codes)} 只股票)...")
    universe_results = run_universe(all_codes)

    print("\n[SUMMARY BY STRATEGY]")
    print("-" * 70)
    for strat in STRATEGIES.keys():
        subset = universe_results[universe_results["strategy"] == strat]
        if subset.empty:
            continue
        rets = subset["return_pct"].dropna()
        pos = (rets > 0).sum()
        neg = (rets < 0).sum()
        avg = rets.mean()
        win_rate = pos / len(rets) * 100 if len(rets) > 0 else 0
        print(f"  {strat}:")
        print(f"    Stocks tested: {len(rets)}")
        print(f"    Avg return:    {avg:+.1f}%")
        print(f"    Win rate:      {win_rate:.0f}% ({pos} wins / {neg} losses)")
        print(f"    Best:          {rets.max():+.1f}% | Worst: {rets.min():+.1f}%")
