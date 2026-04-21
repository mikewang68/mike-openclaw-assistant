"""
Backtrader 回测引擎
支持：币安数据 + A股MongoDB数据
"""

import os
import sys
import backtrader as bt
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# 添加data fetcher路径
sys.path.insert(0, "/program/stock/data/fetcher")
from binance_fetcher import load_cached_klines as load_binance_klines, fetch_and_cache_klines
from astock_fetcher import get_klines as load_astock_klines, to_backtrader_format


class TestStrategy(bt.Strategy):
    """简单测试策略：MACD金叉买入，死叉卖出"""
    params = (
        ("macd_fast", 12),
        ("macd_slow", 26),
        ("macd_signal", 9),
        ("rsi_period", 14),
        ("rsi_oversold", 30),
        ("rsi_overbought", 70),
        ("printlog", False),
    )

    def __init__(self):
        self.dataclose = self.datas[0].close
        self.order = None
        self.buyprice = None
        self.buycomm = None

        # MACD
        self.macd = bt.indicators.MACD(
            self.datas[0],
            period_me1=self.params.macd_fast,
            period_me2=self.params.macd_slow,
            period_signal=self.params.macd_signal
        )

        # RSI
        self.rsi = bt.indicators.RSI(
            self.datas[0].close,
            period=self.params.rsi_period
        )

        # 追踪最近一次MACD金叉/死叉
        self.macd_cross = bt.indicators.CrossOver(self.macd.macd, self.macd.signal)

    def log(self, txt, dt=None):
        if self.params.printlog:
            dt = dt or self.datas[0].datetime.date(0)
            print(f"[{dt.isoformat()}] {txt}")

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return

        if order.status in [order.Completed]:
            if order.isbuy():
                self.buyprice = order.executed.price
                self.buycomm = order.executed.comm
                self.log(f"BUY @ {order.executed.price:.4f}, comm={order.executed.comm:.4f}")
            else:
                self.log(f"SELL @ {order.executed.price:.4f}, comm={order.executed.comm:.4f}")

        self.order = None

    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        self.log(f"TRADE PROFIT: gross={trade.pnl:.4f}, net={trade.pnlcomm:.4f}")

    def next(self):
        if self.order:
            return

        # 简单策略：MACD金叉 + RSI超卖 -> 买入
        #           MACD死叉 -> 卖出
        if not self.position:
            if self.macd_cross > 0 and self.rsi < self.params.rsi_oversold:
                self.log(f"BUY SIGNAL, MACD_cross={self.macd_cross[0]:.4f}, RSI={self.rsi[0]:.2f}")
                self.order = self.buy()
        else:
            if self.macd_cross < 0:
                self.log(f"SELL SIGNAL, MACD_cross={self.macd_cross[0]:.4f}")
                self.order = self.sell()


class GridStrategy(bt.Strategy):
    """网格交易策略（适合加密货币震荡行情）"""
    params = (
        ("grid_count", 5),
        ("grid_range_pct", 0.10),  # ±10%
        ("order_pct", 0.10),       # 每格下单金额比例
        ("printlog", False),
    )

    def __init__(self):
        self.dataclose = self.datas[0].close
        self.order = None
        self.base_price = None
        self.grids = []
        self.position_opened = {}

    def log(self, txt, dt=None):
        if self.params.printlog:
            dt = dt or self.datas[0].datetime.date(0)
            print(f"[{dt.isoformat()}] {txt}")

    def next(self):
        if self.order:
            return

        current_price = self.dataclose[0]

        # 初始化网格（以第一根K线价格为基准）
        if self.base_price is None:
            self.base_price = current_price
            grid_step = current_price * self.params.grid_range_pct / self.params.grid_count
            for i in range(-self.params.grid_count, self.params.grid_count + 1):
                self.grids.append(self.base_price + i * grid_step)
            self.log(f"Grid initialized: base={current_price:.4f}, grids={[f'{g:.4f}' for g in self.grids]}")
            return

        # 网格交易逻辑
        for i, grid_price in enumerate(self.grids):
            if i in self.position_opened:
                continue

            # 价格跌破网格 -> 买入
            if current_price <= grid_price:
                size = (self.broker.getvalue() * self.params.order_pct) / current_price
                self.log(f"GRID BUY at {grid_price:.4f}, price={current_price:.4f}, size={size:.4f}")
                self.order = self.buy()
                self.position_opened[i] = True
                break

            # 价格涨破网格 -> 卖出
            elif current_price >= grid_price:
                if self.position:
                    self.log(f"GRID SELL at {grid_price:.4f}, price={current_price:.4f}")
                    self.order = self.sell()
                    self.position_opened.pop(i, None)
                    break


def run_backtest(
    data_feed,
    strategy_class,
    strategy_params: dict = None,
    cash: float = 100000.0,
    commission: float = 0.001,
    start_date: str = None,
    end_date: str = None,
    printlog: bool = False,
) -> bt.Cerebro:
    """
    运行回测

    data_feed: pd.DataFrame with columns [datetime, open, high, low, close, volume]
               or a bt.feed.DataFeeder
    strategy_class: bt.Strategy subclass
    strategy_params: dict of strategy parameters
    """
    cerebro = bt.Cerebro()

    # 添加数据
    if isinstance(data_feed, pd.DataFrame):
        data_feed["datetime"] = pd.to_datetime(data_feed["datetime"])
        data_feed = data_feed.set_index("datetime")
        data_feed["openinterest"] = 0

        bt_data = bt.feeds.PandasData(dataname=data_feed)
        cerebro.adddata(bt_data)
    else:
        cerebro.adddata(data_feed)

    # 添加策略
    params = strategy_params or {}
    cerebro.addstrategy(strategy_class, printlog=printlog, **params)

    # 设置初始资金
    cerebro.broker.setcash(cash)

    # 手续费
    cerebro.broker.setcommission(commission=commission)

    # 添加分析器
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    # 执行
    results = cerebro.run()

    return cerebro, results[0]


def print_backtest_result(cerebro, strategist):
    """打印回测结果"""
    print("\n" + "=" * 60)
    print("BACKTEST RESULT")
    print("=" * 60)

    final_value = cerebro.broker.getvalue()
    initial_cash = cerebro.broker.startingcash
    total_return = (final_value - initial_cash) / initial_cash * 100

    print(f"Initial Cash:  ${initial_cash:,.2f}")
    print(f"Final Value:   ${final_value:,.2f}")
    print(f"Total Return:  {total_return:.2f}%")

    # 获取分析器结果
    try:
        sharpe = strategist.analyzers.sharpe.get_analysis()
        print(f"Sharpe Ratio:  {sharpe.get('sharperatio', 'N/A')}")
    except:
        pass

    try:
        dd = strategist.analyzers.drawdown.get_analysis()
        print(f"Max Drawdown:  {dd.get('max', {}).get('drawdown', 0):.2f}%")
    except:
        pass

    try:
        trades = strategist.analyzers.trades.get_analysis()
        total = trades.get('total', {})
        won = trades.get('won', {})
        lost = trades.get('lost', {})
        print(f"Total Trades:  {total.get('total', 0)}")
        print(f"  Won: {won.get('total', 0)}, Lost: {lost.get('total', 0)}")
        if won.get('total', 0) + lost.get('total', 0) > 0:
            win_rate = won.get('total', 0) / (won.get('total', 0) + lost.get('total', 0)) * 100
            print(f"  Win Rate: {win_rate:.1f}%")
    except:
        pass

    print("=" * 60)


if __name__ == "__main__":
    print("=== Backtrader Backtest Engine Test ===")

    # 测试1: 币安BTC数据回测
    print("\n[1] Backtest Grid Strategy on BTCUSDT...")
    df = load_binance_klines("BTCUSDT", "1h")
    if df.empty:
        print("  No cached data, fetching from Binance...")
        df = fetch_and_cache_klines("BTCUSDT", "1h", start_date="2025-01-01")

    if not df.empty:
        df = df.rename(columns={"date": "datetime"})
        cerebro, strat = run_backtest(
            df.tail(500),  # 最近500根1h K线
            GridStrategy,
            strategy_params={"grid_count": 5, "grid_range_pct": 0.05},
            cash=10000.0,
            commission=0.001,
            printlog=False,
        )
        print_backtest_result(cerebro, strat)
    else:
        print("  Failed to get data")

    # 测试2: A股回测
    print("\n[2] Backtest MACD Strategy on A-stock 000001...")
    df_astock = load_astock_klines("000001", start_date="2024-01-01", collection="k_raw_v3")
    if not df_astock.empty:
        df_bt = to_backtrader_format(df_astock.tail(500))
        cerebro2, strat2 = run_backtest(
            df_bt,
            TestStrategy,
            strategy_params={"macd_fast": 12, "macd_slow": 26, "macd_signal": 9},
            cash=100000.0,
            commission=0.0005,
            printlog=False,
        )
        print_backtest_result(cerebro2, strat2)
    else:
        print("  No A-stock data available")

    print("\n✅ Backtest engine test complete")
