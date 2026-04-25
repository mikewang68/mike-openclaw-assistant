"""
BBRSI_MACD 三合一趋势策略
结合布林带 + RSI + MACD 三个指标，减少假信号
"""

import pandas as pd
import numpy as np
from ta.trend import MACD
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator as RSI


class BBRSI_MACDStrategy:
    name = "BBRSI_MACD"

    def __init__(self, bollinger_cfg: dict, rsi_cfg: dict, macd_cfg: dict, volume_cfg: dict):
        self.bollinger_period = bollinger_cfg.get("period", 20)
        self.bollinger_std = bollinger_cfg.get("std_dev", 2)
        self.rsi_period = rsi_cfg.get("period", 14)
        self.rsi_oversold = rsi_cfg.get("oversold", 35)
        self.rsi_overbought = rsi_cfg.get("overbought", 70)
        self.macd_fast = macd_cfg.get("fast", 12)
        self.macd_slow = macd_cfg.get("slow", 26)
        self.macd_signal = macd_cfg.get("signal", 9)
        self.volume_period = volume_cfg.get("period", 20)
        self.volume_multiplier = volume_cfg.get("multiplier", 1.5)

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算所有指标"""
        close = df["close"]

        bb = BollingerBands(close=close, window=self.bollinger_period, window_dev=self.bollinger_std)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_middle"] = bb.bollinger_mavg()
        df["bb_lower"] = bb.bollinger_lband()

        df["rsi"] = RSI(close=close, window=self.rsi_period).rsi()

        macd = MACD(close=close, window_fast=self.macd_fast,
                    window_slow=self.macd_slow, window_sign=self.macd_signal)
        df["macd"] = macd.macd()
        df["macd_signal"] = macd.macd_signal()
        df["macd_hist"] = macd.macd_diff()

        df["volume_ma"] = df["volume"].rolling(window=self.volume_period).mean()

        return df

    def check_buy(self, row: pd.Series, prev_row: pd.Series = None) -> tuple:
        """检查买入信号"""
        at_lower = row["close"] <= row["bb_lower"]
        rsi_buy = row["rsi"] < 40

        macd_better = False
        if prev_row is not None:
            macd_better = row["macd_hist"] > prev_row["macd_hist"] > 0

        if at_lower and rsi_buy:
            reason = f"触布林+RSI={row['rsi']:.1f}"
            if macd_better:
                reason += "+MACD改善"
            return True, reason

        return False, ""

    def check_sell(self, row: pd.Series, entry_price: float = None,
                   highest_since_entry: float = None) -> tuple:
        """检查卖出信号"""
        if entry_price and highest_since_entry:
            profit_pct = (highest_since_entry - entry_price) / entry_price
            curr_profit = (row["close"] - entry_price) / entry_price

            if curr_profit <= -0.02:
                return True, f"止损({curr_profit*100:+.1f}%)", "stop_loss"

            if profit_pct >= 0.06:
                return True, f"止盈({profit_pct*100:+.1f}%)", "take_profit"

            if profit_pct >= 0.04 and curr_profit < profit_pct - 0.02:
                return True, f"移动止损({curr_profit*100:+.1f}%)", "trailing"

        at_upper = row["close"] >= row["bb_upper"]
        strong_overbought = row["rsi"] > 65 and row["macd"] < 0

        if at_upper or strong_overbought:
            return True, f"技术({at_upper and '触上轨' or ''}{strong_overbought and 'RSI超买' or ''})".strip(), "technical"

        return False, "", ""
