"""
币安BTC量化交易机器人
BBRSI_MACD 三合一趋势策略
"""

import os, sys, time, json, yaml, schedule, pandas as pd, numpy as np
from datetime import datetime, timedelta
from binance.client import Client
from binance.exceptions import BinanceAPIException

# 统一依赖检查
sys.path.insert(0, "/home/node/.openclaw/workspace")
import python_deps
python_deps.ensure()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategies.bbrsi_macd import BBRSI_MACDStrategy


class CryptoTrader:
    def __init__(self, config_path: str = None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.cfg = yaml.safe_load(open(os.path.join(base_dir, "configs", "config.yaml")))

        binance_cfg = self.cfg["binance"]
        if binance_cfg.get("testnet"):
            self.client = Client(binance_cfg["api_key"], binance_cfg["api_secret"], testnet=True)
        else:
            self.client = Client(binance_cfg["api_key"], binance_cfg["api_secret"])

        self.symbol = self.cfg["trading"]["symbol"]
        self.interval = self.cfg["trading"]["interval"]
        self.strategy = BBRSI_MACDStrategy(
            self.cfg["bollinger"], self.cfg["rsi"], self.cfg["macd"], self.cfg["volume"]
        )

        self.position = None
        self.daily_pnl = 0.0
        self.last_reset_date = datetime.now().date()
        self.telegram_token = self.cfg["telegram"]["bot_token"]
        self.telegram_chat = self.cfg["telegram"]["chat_id"]

        print(f"[{datetime.now()}] 交易机器人启动 | {self.symbol} | {self.interval}")

    def get_klines(self, limit: int = 100) -> pd.DataFrame:
        try:
            klines = self.client.get_klines(symbol=self.symbol, interval=self.interval, limit=limit)
            df = pd.DataFrame(klines, columns=[
                "open_time","open","high","low","close","volume",
                "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
            ])
            for col in ["open","high","low","close","volume"]:
                df[col] = df[col].astype(float)
            return df
        except BinanceAPIException as e:
            print(f"[错误] 获取K线失败: {e}")
            return None

    def get_balance(self) -> float:
        try:
            account = self.client.get_account()
            for asset in account["balances"]:
                if asset["asset"] == "USDT":
                    return float(asset["free"])
            return 0.0
        except BinanceAPIException:
            return 0.0

    def buy(self, quantity: float, price: float) -> dict:
        try:
            order = self.client.order_market_buy(symbol=self.symbol, quantity=quantity)
            avg_price = float(order["cummulativeQuoteQty"]) / float(order["executedQty"])
            self.position = {"entry_price": avg_price, "quantity": float(order["executedQty"]),
                             "entry_time": datetime.now(), "highest": avg_price}
            msg = f"✅ 买入 | 价格: {avg_price:.2f} | 数量: {quantity} BTC"
            print(f"[{datetime.now()}] {msg}")
            self.send_telegram(msg)
            return order
        except BinanceAPIException as e:
            print(f"[错误] 买入失败: {e}")
            self.send_telegram(f"❌ 买入失败: {e}")
            return None

    def sell(self, quantity: float, reason: str = "") -> dict:
        if not self.position:
            return None
        try:
            order = self.client.order_market_sell(symbol=self.symbol, quantity=quantity)
            avg_price = float(order["cummulativeQuoteQty"]) / float(order["executedQty"])
            pnl_pct = (avg_price - self.position["entry_price"]) / self.position["entry_price"] * 100
            pnl_usdt = (avg_price - self.position["entry_price"]) * self.position["quantity"]
            msg = f"🚪 卖出 | 价格: {avg_price:.2f} | 盈亏: {pnl_pct:+.2f}% ({pnl_usdt:+.2f} USDT) | {reason}"
            print(f"[{datetime.now()}] {msg}")
            self.send_telegram(msg)
            self.daily_pnl += pnl_usdt
            self.position = None
            return order
        except BinanceAPIException as e:
            print(f"[错误] 卖出失败: {e}")
            self.send_telegram(f"❌ 卖出失败: {e}")
            return None

    def run_strategy(self):
        now = datetime.now()
        if now.date() != self.last_reset_date:
            self.daily_pnl = 0.0
            self.last_reset_date = now.date()

        df = self.get_klines(limit=100)
        if df is None or len(df) < 30:
            return

        df = self.strategy.add_indicators(df)
        row = df.iloc[-1]
        prev_row = df.iloc[-2]

        if self.position:
            self.position["highest"] = max(self.position["highest"], row["close"])
            sell_signal, reason, stype = self.strategy.check_sell(
                row=row, entry_price=self.position["entry_price"],
                highest_since_entry=self.position["highest"]
            )
            if sell_signal:
                self.sell(self.position["quantity"], f"{stype}:{reason}")
                return
            profit_pct = (row["close"] - self.position["entry_price"]) / self.position["entry_price"] * 100
            print(f"[{now}] 持仓 | 现价: {row['close']:.2f} | 浮盈: {profit_pct:+.2f}%")
        else:
            buy_signal, reason = self.strategy.check_buy(row, prev_row)
            if buy_signal:
                balance = self.get_balance()
                position_size = balance * self.cfg["trading"]["position_ratio"]
                quantity = round(position_size / row["close"], 5)
                if quantity >= 0.00001:
                    self.send_telegram(f"📋 买入信号\n{reason}\n金额: {position_size:.2f} USDT")
                    self.buy(quantity, row["close"])

    def send_telegram(self, message: str):
        if not self.telegram_token or self.telegram_token == "YOUR_TELEGRAM_BOT_TOKEN":
            return
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            requests.post(url, json={
                "chat_id": self.telegram_chat,
                "text": f"[BTC量化 {datetime.now().strftime('%H:%M')}]\n{message}"
            }, timeout=10)
        except Exception as e:
            print(f"[警告] Telegram通知失败: {e}")

    def status(self):
        df = self.get_klines(limit=20)
        if df is None:
            return
        df = self.strategy.add_indicators(df)
        row = df.iloc[-1]
        print(f"\n{'='*50}")
        print(f"BTC: {row['close']:.2f} | RSI: {row['rsi']:.1f}")
        print(f"布林: {row['bb_lower']:.2f}~{row['bb_middle']:.2f}~{row['bb_upper']:.2f}")
        print(f"MACD: {row['macd']:.2f} | Signal: {row['macd_signal']:.2f}")
        print(f"持仓: {self.position} | 日盈亏: {self.daily_pnl:+.2f} USDT")
        print(f"{'='*50}\n")


def main():
    trader = CryptoTrader()
    trader.status()
    trader.run_strategy()
    schedule.every(15).minutes.do(trader.run_strategy)
    print("[启动] 每15分钟自动执行 | Ctrl+C 退出")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
