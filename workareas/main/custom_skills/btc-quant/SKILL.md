# btc-quant Skill

> BTC 加密货币量化交易系统 — 多策略并行 + 模拟盘验证

## 系统架构

```
cron: BTC量化交易信号检查 (每时整点)
  └─ /program/crypto/multi_strategy_runner.py
       ├─ Strategy 1: BBRSI_MACD（三合一：布林带+RSI+MACD）
       ├─ Strategy 2: RSI_MACD_Crossover
       ├─ Strategy 3: EMA_Cross（双EMA交叉）
       └─ Strategy 4: RSI_Volume（RSI+成交量）

数据源：
  ├─ Binance K线（1H 小时线）
  ├─ MongoDB: crypto.crypto_news（舆情）
  └─ MongoDB: neo4j（知识图谱决策参考）

输出：
  ├─ 实盘/模拟仓信号（买入/卖出/止损/止盈）
  └─ Telegram 推送通知
```

## 核心参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 交易对 | BTCUSDT | 永续合约 |
| K线周期 | 1H | 小时线 |
| 杠杆 | 20x | 逐仓 |
| 仓位比例 | 30% | 每次使用 30% 保证金 |
| 止损 | 3% | 固定止损 |
| 止盈 | 8% | 固定止盈 |
| 跟踪止损 | 2% | 启动后跟踪 |
| 最大持仓 | 1 | 同时最多1个仓位 |
| 初始本金 | 100 USDT | 模拟盘 |

## 策略说明

### BBRSI_MACD（三合一）
布林带 + RSI + MACD 三指标共振，减少假信号。
- 布林带：周期20，标准差2
- RSI：超卖35 / 超买70，周期14
- MACD：12/26/9

### RSI_MACD_Crossover
RSI 与 MACD 交叉信号。

### EMA_Cross
双EMA交叉：快线与慢线交叉判断趋势。

### RSI_Volume
RSI + 成交量异常放大确认信号。

## 交易流程

```
每整点 cron 触发
    ↓
获取最新 1H K线数据
    ↓
4个策略并行计算
    ↓
任意策略出现信号？
    ├─ 有 → 检查当前仓位
    │        ├─ 无仓位 → 开仓（paper/实盘）
    │        └─ 有仓位 → 止盈/止损/跟踪检查
    └─ 无 → 静默退出
    ↓
信号详情推送到 Telegram
```

## 运行模式

**当前：PAPER_MODE = True（模拟盘）**
- 所有信号仅记录到 MongoDB
- 不真实下单
- 不消耗真实 USDT

**切换实盘**：
```python
# 编辑 /program/crypto/cron_wrapper.py
PAPER_MODE = False
# 确保 Binance 合约账户有 USDT
```

## Cron 信息

| 项目 | 值 |
|------|-----|
| ID | `aac1969f-db89-40f9-89b5-694cd72f1fde` |
| 表达式 | `0 * * * *`（每整点） |
| Session | isolated |
| 执行命令 | `python3 /program/crypto/multi_strategy_runner.py` |

## MongoDB 表

| Collection | 说明 |
|-----------|------|
| `crypto.crypto_news` | 舆情新闻 |
| `crypto.sim_trades` | 模拟交易记录 |
| `crypto.sim_positions` | 模拟持仓 |
| `crypto.BBRSI_MACD` | BBRSI策略信号 |
| `crypto.RSI_MACD_Crossover` | RSI+MACD策略信号 |
| `crypto.EMA_Cross` | EMA交叉策略信号 |
| `crypto.RSI_Volume` | RSI+Volume策略信号 |

## 配置文件

```bash
# /program/crypto/configs/config.yaml
binance:
  api_key: DBmKrqZeeo3U1sejkA6GUKcdAP12tsR2FpDHeXeQnNx45owGa208yhZ0rAKDyB9m
  api_secret: SIvVPi39dXhR7CYAmhuPithEVlvmFaLL6gvyocpvBee5mG4GVoT1T1oFv6AomRKl
  testnet: false
  use_futures: true

trading:
  symbol: BTCUSDT
  interval: 1h
  leverage: 20
  margin_type: ISOLATED
  position_ratio: 0.333
  stop_loss: 0.03
  take_profit: 0.08
  trailing_delta: 0.02
  initial_capital: 100
  max_positions: 1
```

## 手动运行

```bash
# 查看帮助
python3 /program/crypto/multi_strategy_runner.py --help

# 直接运行信号检查
cd /program/crypto && python3 multi_strategy_runner.py

# 查看模拟持仓
python3 -c "
from pymongo import MongoClient
c = MongoClient('mongodb://stock:681123@192.168.1.2:27017/admin')
db = c['crypto']
print('当前持仓:', list(db.sim_positions.find()))
print('交易记录:', list(db.sim_trades.find().sort('opened_at', -1).limit(5)))
"
```

## 策略评价维度

验证 1-2 周后对比各策略：
- 胜率（Win Rate）
- 盈亏比（Profit Factor）
- 夏普比率（Sharpe Ratio）
- 最大回撤（Max Drawdown）

最优策略切换实盘。

## 文件结构

```
/program/crypto/
├── multi_strategy_runner.py   ← 多策略主程序
├── cron_wrapper.py            ← cron 包装器（v3）
├── binance_trader.py          ← 原始交易逻辑
├── backtest.py                ← 回测工具
├── btc_account_summary.py     ← 账户摘要
├── crypto_kg_builder.py        ← 知识图谱构建
├── fetch_crypto_news.py       ← 舆情抓取（多源版）
├── configs/config.yaml         ← 配置文件
└── strategies/
    ├── base_strategy.py
    ├── bbrsi_macd.py
    ├── rsi_macd_crossover.py
    ├── ema_crossover.py
    └── rsi_volume.py
```

## 代码位置

**GitHub**: `mikewang68/mike-openclaw-assistant/tree/main/workareas/main/custom_skills/btc-quant`
