# BTC 量化交易机器人

基于 **BBRSI_MACD 三合一策略** 的币安 BTC/USDT 量化交易机器人。

## 策略说明

- **布林带** (20周期, 2倍标准差) — 识别超买超卖 + 支撑阻力
- **RSI** (14周期, oversold<40) — 动量确认
- **MACD** (12/26/9) — 趋势确认

### 买入条件
- K线触及布林下轨 **AND** RSI < 40

### 卖出条件
- 止损：-2%
- 止盈：+6%
- 移动止损：从盈利4%开始，回撤2%触发
- 技术面：触布林上轨或 RSI>65+MACD<0

## 配置

编辑 `configs/config.yaml` 中的 `binance.api_key` 和 `binance.api_secret`（使用币安测试网）。

## 运行

```bash
# 回测
python3 backtest.py --symbol BTCUSDT --days 30 --interval 15m

# 实盘模拟
python3 binance_trader.py
```
