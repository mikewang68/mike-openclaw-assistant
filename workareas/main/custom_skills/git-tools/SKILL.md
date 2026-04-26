# git-tools Skill

> 本 agent 的 git 工作流与标准 git 用法不同，存在嵌套目录结构。本 skill 记录所有 git 操作规范，避免升级后丢失。

---

## 仓库结构

```
Git Repo Root:  /home/node/.openclaw/workspace/
                └── .git/
                └── workareas/
                    └── main/          ← 主工作目录（agent 在此目录下操作）

Git Credential: token 已嵌入 remote.origin.url
GitHub:         https://github.com/mikewang68/mike-openclaw-assistant
分支:           main
提交者:         Assistant <assistant@mike.ai>
```

---

## 重要约束

1. **不要在 repo root（`/home/node/.openclaw/workspace/`）直接操作**
2. **所有 git 命令需在 `/home/node/.openclaw/workspace/workareas/main/` 目录下执行**
3. Git 会自动向上查找 `.git` 目录，因此从 `workareas/main/` 内执行 git 命令即可
4. 文件路径显示为相对于 repo root 的路径，如 `workareas/main/custom_skills/...`

---

## 常用操作

### 查看状态（当前目录所有变更）
```bash
cd /home/node/.openclaw/workspace/workareas/main
git status --short
```

### 查看具体目录/文件的变更
```bash
cd /home/node/.openclaw/workspace/workareas/main
git status --short custom_skills/
git status --short memory/
git status --short program_crypto/
```

### 添加文件到暂存区
```bash
# 添加整个目录
git add custom_skills/

# 添加特定文件
git add custom_skills/youtube-summarize/

# 添加多个目录
git add custom_skills/ memory/ program_crypto/

# 添加所有变更
git add -A
```

### 提交
```bash
git commit -m "描述信息"
```

### 推送
```bash
git push origin main
```

### 查看远程仓库
```bash
git remote -v
git config --get remote.origin.url   # 查看token
```

### 查看提交历史
```bash
git log --oneline -10
git log --oneline --all -20
```

### 查看已暂存未提交的文件
```bash
git diff --cached --name-only
```

### 撤销暂存（未commit）
```bash
git restore --staged <file>
git reset HEAD <file>
```

### 撤销工作区修改
```bash
git restore <file>
git checkout -- <file>
```

---

## 提交信息规范

参考 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat(skills): add youtube-summarize skill
fix(cron): resolve BTC pipeline timeout issue
docs: update BTC strategy manual
refactor: simplify multi-strategy runner
chore: add .gitignore for sensitive files
```

格式：`type(scope): description`

常用 type：
- `feat` - 新功能
- `fix` - 修复 bug
- `docs` - 文档
- `refactor` - 重构
- `chore` - 杂项（依赖、配置等）

---

## 添加新文件到 Git

```bash
# 1. 创建文件后，确保父目录存在
# 2. 添加到暂存区
git add <new_file_or_dir>

# 3. 提交
git commit -m "feat(scope): add ..."

# 4. 推送
git push origin main
```

---

## 分支操作（当前不需要）

```bash
# 创建新分支
git checkout -b feature/new-skill

# 切换分支
git checkout main

# 查看所有分支
git branch -a

# 删除分支（已合并）
git branch -d feature/new-skill
```

---

## 常见错误处理

### "nothing to commit, working tree clean"
没有任何变更需要提交。

### "Please tell me who you are"
需要配置身份（当前已配置好）：
```bash
git config user.email "assistant@mike.ai"
git config user.name "Assistant"
```

### "Authentication failed"
Token 过期或无效，检查：
```bash
git config --get remote.origin.url
```

### "Would clobber existing strategy"
多个文件重名，用路径区分：
```bash
git add workareas/main/custom_skills/other/
```

---

## Credential 管理

Token 存储在 `remote.origin.url` 中，格式：
```
https://ghp_TOKEN@github.com/owner/repo.git
```

**不要**在公开场合输出完整 token。查看时用：
```bash
git config --get remote.origin.url | sed 's/.*https:\/\///;s/@github.com.*//'
```

---

## .gitignore 规范

已配置忽略：
- `.openclaw/` - OpenClaw 内部状态
- `.clawhub/` - OpenClaw 插件
- `.session_archive/` - 会话存档
- `agents/` - Agent 配置（含 auth-profiles.json）
- `auth-profiles.json` - API 密钥
- `memory/*TRADE*.md` - 交易相关 memory
- `memory/*secret*.md` - 敏感 memory

如需忽略更多文件，在 repo root 的 `.gitignore` 中添加。

---

## 自动化脚本

使用 `git_tools.py` 简化常用操作：
```bash
python3 /home/node/.openclaw/workspace/workareas/main/custom_skills/git-tools/scripts/git_tools.py <command> [args]
```

支持命令：
- `status` - 显示变更摘要
- `add <path>` - 添加文件
- `commit <message>` - 提交
- `push` - 推送
- `log <n>` - 最近 n 条提交
