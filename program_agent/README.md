# /program/agent/ — Mike-AI 双向学习系统

## 定位
Mike 和 AI 的共同进化记忆系统，与量化交易业务分离。

## 文件
- `agent_memory_manager.py` — 主程序（记忆/画像/预测/提醒）

## 数据库
- MongoDB: `agent_memory`（独立数据库）
  - `persona` — Mike 画像
  - `interactions` — 对话洞察
  - `growth_log` — 成长轨迹
  - `predictions` — 预测追踪

## 使用方式
```bash
# 搜索记忆
python3 /program/agent/agent_memory_manager.py --search "量化交易"

# 写入记忆
python3 /program/agent/agent_memory_manager.py --save "今天讨论了..." --type conversation_insight

# 查看画像
python3 /program/agent/agent_memory_manager.py --persona

# 主动提醒
python3 /program/agent/agent_memory_manager.py --alerts
```

## 在 OpenClaw 中调用
```python
import sys
sys.path.insert(0, '/program/agent')
from agent_memory_manager import memory_search, log_interaction, get_persona, record_prediction
```

## 目录结构（未来扩展）
```
/program/agent/
├── agent_memory_manager.py   # P0核心
├── memory_search.py           # 搜索增强（未来）
├── prediction_tracker.py      # 预测追踪（未来）
├── growth_visualizer.py       # 成长可视化（未来）
└── README.md
```
