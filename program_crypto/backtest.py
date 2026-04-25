"""
回测脚本
python3 backtest.py --symbol BTCUSDT --days 90
"""
import os, sys, argparse, pandas as pd, numpy as np, yaml
from datetime import datetime, timedelta
from binance.client import Client

# 统一依赖检查
sys.path.insert(0, "/home/node/.openclaw/workspace")
import python_deps
python_deps.ensure()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategies.bbrsi_macd import BBRSI_MACDStrategy


def load_config():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return yaml.safe_load(open(os.path.join(base_dir, "configs", "config.yaml")))


def fetch_klines(client, symbol: str, interval: str, limit: int = 1000):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    return df


def backtest(df: pd.DataFrame, strategy, initial_balance=10000, position_ratio=0.20):
    df = strategy.add_indicators(df)
    balance = initial_balance
    position = None
    trades = []

    for i in range(30, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]

        if position is None:
            buy_sig, reason = strategy.check_buy(row, prev_row)
            if buy_sig:
                position_size = balance * position_ratio
                quantity = position_size / row["close"]
                position = {"entry_price": row["close"], "quantity": quantity,
                             "highest": row["close"], "entry_idx": i}
                trades.append({"type": "BUY", "idx": i, "price": row["close"],
                               "quantity": quantity, "reason": reason})
        else:
            position["highest"] = max(position["highest"], row["close"])
            sell_sig, reason, stype = strategy.check_sell(
                row, position["entry_price"], position["highest"]
            )
            if sell_sig:
                pnl_pct = (row["close"] - position["entry_price"]) / position["entry_price"]
                pnl_usdt = pnl_pct * position["quantity"] * position["entry_price"]
                balance += pnl_usdt
                trades.append({"type": "SELL", "idx": i, "price": row["close"],
                               "pnl_pct": pnl_pct * 100, "pnl_usdt": pnl_usdt,
                               "reason": reason, "sell_type": stype})
                position = None

    sells = [t for t in trades if t["type"] == "SELL"]
    wins = [t for t in sells if t["pnl_usdt"] > 0]
    losses = [t for t in sells if t["pnl_usdt"] <= 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0

    print(f"\n{'='*50}")
    print(f"回测报告")
    print(f"{'='*50}")
    print(f"初始资金: ${initial_balance:.2f} | 最终: ${balance:.2f}")
    print(f"总收益率: {(balance-initial_balance)/initial_balance*100:.2f}%")
    print(f"交易次数: {len(sells)} | 胜率: {win_rate:.1f}% ({len(wins)}胜/{len(losses)}负)")
    if sells:
        avg_win = np.mean([t["pnl_pct"] for t in wins]) * 100 if wins else 0
        avg_loss = np.mean([t["pnl_pct"] for t in losses]) * 100 if losses else 0
        print(f"平均盈利: {avg_win:+.2f}% | 平均亏损: {avg_loss:+.2f}%")
    print(f"{'='*50}")
    return trades, balance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--interval", default="15m")
    args = parser.parse_args()

    cfg = load_config()
    client = Client(cfg["binance"]["api_key"], cfg["binance"]["api_secret"], testnet=True)

    print(f"获取: {args.symbol} | {args.interval} | 近 {args.days} 天")
    df = fetch_klines(client, args.symbol, args.interval)
    print(f"K线: {len(df)} 根")

    strategy = BBRSI_MACDStrategy(cfg["bollinger"], cfg["rsi"], cfg["macd"], cfg["volume"])
    backtest(df, strategy, 10000, cfg["trading"]["position_ratio"])


if __name__ == "__main__":
    main()
