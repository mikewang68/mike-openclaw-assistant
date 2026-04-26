# system-maintenance Skill

> 系统维护工具箱 — 定时清理、记忆整合、Session 压缩

---

## Cron 任务总览

| 任务 | Cron | 脚本 | 职责 |
|------|------|------|------|
| 每日任务检查 | 每天 08:00 | `check_and_update.py` | 清理过期/僵尸任务，更新 RECENT.md |
| 每周记忆整合 | 周一 09:00 | `weekly_consolidate.py` | 整合上周每日 memory → MEMORY.md |
| 每周工作区清理 | 周一 23:00 | `cleanup_workspace.sh` | 清理旧 paper 文件和临时文件 |
| Session 压缩 | 每天 00:25 | `session_clean.py` | 归档大 session 文件 |

---

## 脚本详情

### 1. check_and_update.py（每日 08:00）
路径：`/home/node/.openclaw/workspace/skills/auto-memory/check_and_update.py`
职责：
- 扫描 cron 任务，删除僵尸任务（连续失败 > 3 次）
- 扫描 session，清理孤儿状态
- 扫描 workspace，删除 30 天前文件
- 更新 `RECENT.md`

### 2. weekly_consolidate.py（周一 09:00）
路径：`/home/node/.openclaw/workspace/skills/auto-memory/weekly_consolidate.py`
职责：
- 读取上周每日 memory 文件
- 提取关键事件和要点
- 追加整合结果到 `MEMORY.md`
- 归档到 `.session_archive/`

### 3. cleanup_workspace.sh（周一 23:00）
路径：`/home/node/.openclaw/workspace/workareas/main/cleanup_workspace.sh`
职责：
- 清理 2603 批次旧 paper 文件
- 清理 7 天前 2604 批次的 paper/reviewer/coach 文件
- 清理 3 天前的 temp review 文件
- 记录日志到 `/obsidian/shared/cleanup.log`

### 4. session_clean.py（每天 00:25）
路径：`/home/node/.openclaw/workspace/session_clean.py`
职责：
- 扫描 `/home/node/.openclaw/sessions/` 中的大 session 文件
- 归档大于 5MB 的 session 到 `.session_archive/`
- 统计当前 session 数量和大小

---

## 手动运行

```bash
# 每日任务检查
python3 /home/node/.openclaw/workspace/skills/auto-memory/check_and_update.py

# 每周记忆整合
python3 /home/node/.openclaw/workspace/skills/auto-memory/weekly_consolidate.py

# 每周工作区清理
bash /home/node/.openclaw/workspace/workareas/main/cleanup_workspace.sh

# Session 压缩
python3 /home/node/.openclaw/workspace/session_clean.py
```

---

## 文件结构

```
system-maintenance/
├── SKILL.md              ← 本文档
└── scripts/
    └── status.py         ← 查看各维护任务状态
```

---

## 相关文件

| 文件 | 路径 |
|------|------|
| RECENT.md | `/home/node/.openclaw/workspace/workareas/main/RECENT.md` |
| MEMORY.md | `/home/node/.openclaw/workspace/workareas/main/MEMORY.md` |
| session_archive | `/home/node/.openclaw/workspace/workareas/main/.session_archive/` |
| cleanup.log | `/obsidian/shared/cleanup.log` |
