#!/usr/bin/env python3
"""
量化交易系统 - 主入口
功能：数据拉取、回测、实盘（待开通）
"""

import sys
import os
import argparse
from pathlib import Path

# 添加项目路径
sys.path.insert(0, "/program/stock")

from data.fetcher.binance_fetcher import (
    fetch_and_cache_klines,
    load_cached_klines,
    get_symbol_ticker,
    get_account_info,
)
from data.fetcher.astock_fetcher import (
    get_klines as load_astock_klines,
    get_account,
    to_backtrader_format,
)


def cmd_binance_ticker():
    """显示币安实时价格"""
    tickers = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    for sym in tickers:
        try:
            t = get_symbol_ticker(sym)
            print(f"{sym}: ${float(t['last_price']):,.2f} "
                  f"(24h vol: {float(t['quote_volume']):,.0f} USDT)")
        except Exception as e:
            print(f"{sym}: ERROR - {e}")


def cmd_binance_fetch(symbol="BTCUSDT", interval="1h", days=30):
    """拉取币安K线数据"""
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    print(f"Fetching {symbol} {interval} from {start_date} to {end_date}...")
    df = fetch_and_cache_klines(symbol, interval, start_date, end_date)
    if not df.empty:
        print(f"  Got {len(df)} klines, from {df.iloc[0]['date']} to {df.iloc[-1]['date']}")
        print(f"  Latest close: ${df.iloc[-1]['close']}")
    else:
        print("  No data fetched")


def cmd_astock_klines(code="000001", days=30):
    """显示A股K线信息"""
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    df = load_astock_klines(code=code, start_date=start_date, end_date=end_date,
                            collection="k_raw_v3", limit=100)
    if not df.empty:
        print(f"Stock {code}: {len(df)} rows, from {df.iloc[0]['date']} to {df.iloc[-1]['date']}")
        print(df.tail(5)[["date", "open", "high", "low", "close", "vol"]].to_string())
    else:
        print(f"No data for {code}")


def cmd_astock_account():
    """显示A股账户信息"""
    accounts = get_account()
    for a in accounts:
        print(f"\nAccount: {a.get('name')} ({a.get('type')})")
        print(f"  Cash: ${a.get('cash', 0):,.2f}")
        print(f"  Initial: ${a.get('initial_capital', 0):,.2f}")
        stocks = a.get('stocks', [])
        print(f"  Holdings: {len(stocks)}")
        for s in stocks[:5]:
            print(f"    {s.get('code')} - {s.get('name')}: {s.get('quantity')} shares @ ${s.get('cost'):.2f}")


def cmd_backtest(symbol="BTCUSDT", strategy="grid"):
    """运行回测"""
    from backtest.backtest_engine import (
        run_backtest, print_backtest_result,
        GridStrategy, TestStrategy
    )

    print(f"\n=== Backtest: {strategy} on {symbol} ===")

    # 加载数据
    df = load_cached_klines(symbol, "1h")
    if df.empty:
        print("No cached data, fetching...")
        df = fetch_and_cache_klines(symbol, "1h", days=60)

    if df.empty:
        print("Failed to get data")
        return

    df = df.rename(columns={"date": "datetime"})
    df = df.tail(500)

    if strategy == "grid":
        from backtest.backtest_engine import GridStrategy as strat
        params = {"grid_count": 5, "grid_range_pct": 0.05}
    else:
        from backtest.backtest_engine import TestStrategy as strat
        params = {"macd_fast": 12, "macd_slow": 26, "macd_signal": 9}

    cerebro, result = run_backtest(
        df, strat,
        strategy_params=params,
        cash=10000.0,
        commission=0.001,
        printlog=False,
    )
    print_backtest_result(cerebro, result)


def main():
    parser = argparse.ArgumentParser(description="量化交易系统")
    subparsers = parser.add_subparsers(dest="cmd", help="子命令")

    # binance ticker
    p = subparsers.add_parser("ticker", help="显示币安实时价格")

    # binance fetch
    p = subparsers.add_parser("fetch", help="拉取币安K线数据")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--days", type=int, default=30)

    # astock klines
    p = subparsers.add_parser("astock", help="显示A股K线")
    p.add_argument("--code", default="000001")
    p.add_argument("--days", type=int, default=30)

    # account
    p = subparsers.add_parser("account", help="显示账户信息")

    # backtest
    p = subparsers.add_parser("backtest", help="运行回测")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--strategy", default="grid",
                    choices=["grid", "macd"])

    args = parser.parse_args()

    if args.cmd == "ticker":
        cmd_binance_ticker()
    elif args.cmd == "fetch":
        cmd_binance_fetch(args.symbol, args.interval, args.days)
    elif args.cmd == "astock":
        cmd_astock_klines(args.code, args.days)
    elif args.cmd == "account":
        cmd_astock_account()
    elif args.cmd == "backtest":
        cmd_backtest(args.symbol, args.strategy)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
