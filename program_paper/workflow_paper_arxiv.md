# workflow_paper_arxiv.md - 每日arXiv论文审阅工作流

**版本**：V8.27
**最后更新**：2026-04-09

**变更记录**：
- V8.27：**修复文件名算法不一致 + 原子化保证** - workflow文件名算法改为`re.sub(r'[^a-zA-Z0-9]', '_', title[:30])`（与markdown_generator.py的safe_name()完全一致）；新增Code阶段四步中途失败的原子清理逻辑
- V8.26：**修复文件名日期bug** - 输出文件名改用`$(date +%Y-%m-%d)`（pipeline运行日期），不再使用论文目录`{date}`；论文提交日期保存到md内部`**Date**`字段
- V8.25：**Code阶段完全固化** - 明确规定Code阶段=exec脚本（markdown_generator+verify_v3+sync_feishu），禁止使用subagent；预删除旧文件防残留；V3失败强制修复
- V8.24：修复核心bug - Phase1b改用直接MiniMax API调用，替代挂起的`openclaw agent --local`
- V8.23：固化V1/V2验证规则（失败2次后Main接管）
- V8.22：新增主动监控规则 - 防止Pipeline卡死
- V8.21：修复7个问题（见问题核查报告）：spawn模板改JSON、runTimeoutSeconds修正、Agent术语统一、V1/V2执行角色明确、Phase1执行方式明确、轮询间隔说明、AGENTS.md串行矛盾删除
- V8.20：加入监管机制 - started_at追踪、15分钟超时检测、自动重启killed论文、每15分钟进度通报
- V8.18：移除所有 sessions_spawn，改为 Main Agent 直接执行
- V8.17：Phase1 脚本移至 `agents/research/` 目录（search_arxiv_24h.py、phase1b_llm_score.py）
- V8.16：Phase1 明确由 Research Agent 执行；Phase1b 评分门槛从≥6分提升至≥8分
- V8.15：Phase1b 正式规定使用 `phase1b_llm_score.py`（LLM评分，禁止规则匹配）；量化交易扩展为q-fin.TR+ST+PM独立查询
- V8.14：量化交易方向从 `q-fin.TR` 扩展为 `q-fin.TR OR q-fin.ST OR q-fin.PM`（全面覆盖量化交易）
- V8.13：Phase1b 使用正确的4维评分标准（Novelty/Significance/Soundness/Clarity），替代之前的5方向各1分评分
- V8.12：Phase1b 强制 LLM 评分，禁止规则匹配；要求汇报 LLM 评分理由作为证据
- V8.11：Phase1 搜索改用 arXiv API（顺序查询防429），强化去重逻辑
- V8.10：固化sync_feishu.py正则修复（支持列表项完整提取）；新增飞书去重步骤
- V8.9：修正文件名算法与V8.0一致；新增V1/V2验证环节

---

## 触发条件
- Cron `0 0 * * *` 定时触发（每日00:00 Asia/Shanghai）
- Mike 说"查arXiv论文"

## 主动监控规则（必须遵守）

### 监控时机
- **每15分钟主动轮询** subagent 状态（`subagents(action=list)`）
- 每次收到 subagent 完成事件后，立即检查其他 subagent 是否有超时/卡死
- **不等事件推送**：即使没有收到完成通知，也要每15分钟检查一次

### 监控检查项
| 检查项 | 阈值 | 动作 |
|--------|------|------|
| subagent 运行时间 | > 15分钟 | 检查是否卡死，必要时kill重启 |
| Reviewer队列积压 | > 5个待处理 | 补充新Reviewer |
| Coach队列积压 | > 3个待处理 | 立即执行Code |
| Phase2总进度 | 每30分钟汇报Mike一次 | 主动汇报 |
| 异常subagent | 连续失败3次 | 报告Mike，暂停该论文 |

### 卡死检测与恢复
```
每15分钟执行：
1. subagents(action=list) → 获取所有subagent状态
2. 检查 started_at，若距今 > 15分钟 且状态为running → 判定为卡死
3. subagents(action=kill, target="<session_key>") → 杀死卡死subagent
4. 重新 spawn 该论文的 Reviewer
5. 汇报Mike：论文X超时被杀，已重启
```

### 主动汇报格式（每30分钟）
```
【Main】📊 arXiv论文审阅进度汇报

工作流：workflow_paper_arxiv.md V8.22
日期：{date}
状态：进行中

Phase1：✅ 完成（90篇 → 33篇通过）
Phase2：🔄 进行中（X/Y完成）
- ✅ 完成（≥80分）：N篇
- ⏳ 进行中：M篇（Reviewer/Coach）
- ❌ 跳过（<80分）：K篇

当前并行：5/5
耗时：X分钟
预计剩余：Y分钟

无阻塞问题。
```

### 汇报时机
| 时机 | 内容 | 方式 |
|------|------|------|
| 工作流启动 | 启动通知 | 一次性 |
| 每30分钟 | 进度汇报 | 主动推送 |
| 每步完成 | 阶段完成 | 完成后一次 |
| 发现异常 | 异常警报 | **立即报告Mike** |
| 全流程完成 | 最终报告 | 完成后一次 |

---

## 核心约束

1. **系统默认模型**：禁止指定模型版本
2. **流水线并行执行**：Reviewer/Coach/Code三个阶段流水线并行，总共最多5个subagent同时运行
   - 任何Reviewer完成 → 立即启动新Reviewer（保持5个并行）
   - 任何Coach完成 → 立即启动该论文的Coach + 新Reviewer
   - 任何Coach完成 → 立即执行Code（不占subagent slot）
3. **中文输出**：审阅意见中文，标题/摘要/引用英文原文
4. **80分门槛**：Reviewer 评分 ≥80 才进 Coach/Code，<80 直接跳过
5. **搜索工具**：只用 arXiv API（http://export.arxiv.org/api/query），不用 Firecrawl
   - **防429策略**：5个方向**顺序查询**，每方向间隔10秒
   - **超时重试**：指数退避（30s → 60s → 120s → 240s → 480s），最多5次
6. **Subagent 执行**：Reviewer/Coach阶段用subagent；Code阶段用exec脚本（不用Agent）
7. **API调用必须封装脚本**：所有外部API调用必须通过现成脚本执行，禁止子Agent自行构造API调用
   - **search_arxiv_24h.py**：arXiv API 搜索脚本（顺序查询 + 防429 + 去重）
   - **sync_feishu.py**：已修复 `extract_field` 正则，支持列表项完整提取
8. **Reviewer/Coach Prompt 规范**：
   - **Reviewer**：使用 `reviewer-prompt.md`（Part1-3），输入 paper.md 全文 + prompt模板 → 输出 Part1-3 JSON
   - **Coach**：使用 `coach-prompt.md`（Part4-5），输入 paper.md + reviewer part2/part3 → 输出 Part4-5 JSON
   - **禁止行为**：绝对禁止只用passed.json的title/abstract生成评阅，必须先下载论文全文到paper.md
   - **Reviewer prompt 路径**：`/obsidian/00_Auxiliary/03_Prompt/reviewer-prompt.md`
   - **Coach prompt 路径**：`/obsidian/00_Auxiliary/03_Prompt/coach-prompt.md`

---

## 执行流程（Phase1 → Phase2）

```
Cron/Mike 触发
    ↓
【Phase1】Research（**Main Agent exec 执行，不走 subagent**）
    - 阶段1a：arXiv API 顺序搜索5方向 → 合并去重 → all_raw_papers.json
      **执行方式**：Main Agent exec 调用 `python3 /program/paper/scripts/search_arxiv_24h.py`
      工具：search_arxiv_24h.py（arXiv API）
      时间：动态日期（昨天 ~ 今天，最近24小时内）
      每方向最多50篇，**顺序查询**，每方向查询间隔10秒
      **去重**：按 arXiv ID（从URL提取）去重，相同ID只保留第一条
    - 阶段1b：基于 title + summary 快速评分 → ≥8分 → passed.json
      **⚠️ 强制约束：必须用 LLM，禁止规则匹配 ⚠️**
      
      **执行方式**：Main Agent exec 调用 `python3 /program/paper/scripts/phase1b_llm_score.py`（动态计算日期，不传参数）
      - 分批并行调用 `openclaw agent --local`（每批20篇，最多5个并行）
      - 逐篇 LLM 评分，4维标准（Novelty/Significance/Soundness/Clarity）
      - 输出 `passed.json`（≥8分）+ `phase1b_evidence.json`（完整评分证据）
      
      **评分标准（4维，0-10分）**：
      | 维度 | 分值 | 说明 |
      |------|------|------|
      | Novelty（创新性） | 0-3 | 实质新观点/方法 |
      | Significance（重要性） | 0-2 | 问题重要性 |
      | Soundness（严谨性） | 0-3 | 方法逻辑自洽 |
      | Clarity & Results（清晰度） | 0-2 | 数据支撑 |
      | **总分** | **0-10** | **≥8分通过** |
      
      **输出**：`passed.json`（≥8分论文）+ `phase1b_evidence.json`（全部评分证据）
      
      **违规判定**：发现规则匹配代替 LLM → Phase1b 执行失败，必须重新执行
    ↓
【Phase2a】Main批量下载（exec执行）
    - Main读取passed.json，逐篇调用Firecrawl下载论文PDF全文
    - **执行方式**：`python3 /program/paper/scripts/phase2a_firecrawl.py {date}`
    - 保存到：{PAPER_DIR}/{arXiv_id}/paper.md
    - 验证所有文件存在且 > 1000字符
    - 如有下载失败，记录error并继续
    ↓
【Phase2b】流水线审阅（spawn并行）
    
    **核心原则**：Main Agent 是唯一的调度中心，通过 sessions_spawn 启动 subagent，不直接调用 LLM。
    
    **监管机制**：
    1. **started_at记录**：每次spawn时记录当前时间到status.json
    2. **超时检测**：每5分钟扫描status.json，发现 >15分钟无更新的论文标记为failed
    3. **自动重启**：failed论文重新spawn对应步骤
    4. **进度通报**：每15分钟向Mike报告（X/Y完成，Z失败，N次重启）
    5. **异常告警**：任何阶段失败立即通报Mike
    
    **spawn调用规则**：
    - 必须显式传递 `runTimeoutSeconds: 900`（subagent超时保底）
    - 不要传递 `timeoutSeconds`（这是LLM请求超时，会被配置文件覆盖）
    - `runTimeoutSeconds` 是 sessions_spawn 工具的参数，不是 LLM 配置
    
    **Main Agent 调度循环**（直到所有论文处理完）：
    
    ┌──────────────────────────────────────────────────────────────┐
    │  流水线调度规则（最多5个Reviewer subagent并行）：              │
    │                                                               │
    │  1. 【监控检查】检查疑似被kill的论文（>15分钟无更新）         │
    │     - 读取 status.json 中所有 in_progress 论文                │
    │     - 如果 当前时间 - started_at > 15分钟 → 标记为failed     │
    │     - 重新 spawn 该论文的对应步骤                              │
    │                                                               │
    │  2. 补充 Reviewer（保持最多5个并行）                          │
    │     - 读取 passed.json 中未处理的论文                         │
    │     - 对每篇论文 spawn 一个 Reviewer subagent                  │
    │     - **记录 started_at 到 status.json**                      │
    │                                                               │
    │  3. 检查 Reviewer 完成 → V1验证 → 分数判断                    │
    │     - V1失败 → 重新spawn该论文的Reviewer                       │
    │     - 分数<80 → 跳过，继续下一 Reviewer                        │
    │     - 分数≥80 → spawn Coach subagent                          │
    │                                                               │
    │  4. 检查 Coach 完成 → V2验证 → exec Code                      │
    │     - V2失败 → 重新spawn Coach                                │
    │     - V2通过 → exec Code阶段（PDF+MD+V3+飞书）                │
    │                                                               │
    │  5. 【监管报告】每15分钟向Mike通报进度                         │
    │     - X/Y篇已完成，Z篇失败，W篇进行中                        │
    │     - 疑似被kill已重启N次                                     │
    │                                                               │
    │  示例流水线（同一时刻）：                                       │
    │  [Reviewer-1] → [V1] → [Coach-1] → [V2] → [Code-1]         │
    │  [Reviewer-2] → [V1] → [Coach-2] → [V2]                     │
    │  [Reviewer-3] → [V1] → [Coach-3]                             │
    │  [Reviewer-4]                                                 │
    │  [Reviewer-5]                                                 │
    └──────────────────────────────────────────────────────────────┘
    
    **Main Agent 调度步骤（精确执行）**：
    
    ① **读取待处理队列**：从 `{BASE}/passed.json` 读取论文列表
    
    ② **Spawn Reviewer（最多5个并行）**：
       使用 `sessions_spawn` 启动 Reviewer subagent：
       
       ```json
       {
         "task": "执行论文审阅 - Reviewer阶段（Part1-3）\n\n论文ID：{arxiv_id}\npaper.md 路径：{paper_dir}/paper.md\nreviewer-prompt.md 路径：/obsidian/00_Auxiliary/03_Prompt/reviewer-prompt.md\n输出路径：{paper_dir}/reviewer.json\n\n步骤：\n1. 读取 paper.md（论文全文）\n2. 读取 reviewer-prompt.md\n3. 构建 prompt：reviewer-prompt.md + paper.md 全文\n4. 调用 LLM 生成 Part1-3 JSON\n5. 解析 LLM 返回结果，提取 JSON\n6. 保存到 reviewer_path\n\n注意：必须读取 paper.md，不能只用 title/abstract",
         "runtime": "subagent",
         "runTimeoutSeconds": 900,
         "label": "reviewer-{arxiv_id}"
       }
       ```
    
    ③ **检查 Reviewer 完成**（用 `subagents(action=list)`）：
       - Reviewer 完成 → 读取 reviewer.json → **Main Agent 执行 V1 验证**
       - V1失败 → 重新 spawn Reviewer
       - 分数<80 → 跳过（标记skipped）
       - 分数≥80 → spawn Coach subagent
    
    ④ **Spawn Coach**：
       ```json
       {
         "task": "执行论文审阅 - Coach阶段（Part4-5）\n\n输入：\n- paper.md：{paper_dir}/paper.md\n- reviewer.json（只需part2分数≥80）：{paper_dir}/reviewer.json\n- coach-prompt.md：/obsidian/00_Auxiliary/03_Prompt/coach-prompt.md\n输出：{paper_dir}/coach.json\n\n步骤：\n1. 读取 reviewer.json，确认 part2 scores.total_100 ≥ 80\n2. 读取 coach-prompt.md\n3. 生成 Part4-5 JSON（阶梯式修改建议 + 范式重构）\n4. 保存到 coach_path",
         "runtime": "subagent",
         "runTimeoutSeconds": 900,
         "label": "coach-{arxiv_id}"
       }
       ```
    
    ⑤ **检查 Coach 完成 → V2验证 → exec Code**：
       ```bash
       # V2 验证
       python3 /program/paper/scripts/verify_v3.py {coach_json}
       
       # Code 阶段（exec，不占 subagent slot）
       # 使用 run_code_phase.py（内置正确文件名算法 + 原子性保证）
       python3 /program/paper/scripts/run_code_phase.py {date}
       ```
    
    ⑥ **补充新 Reviewer**：保持5个并行，直到队列为空
    
    **轮询间隔说明**：
    - Main Agent 发起 spawn 后，**必须等待 subagent 完成**
    - 用 `subagents(action=list)` 检查完成状态
    - **每次检查间隔至少 30 秒**（避免 busy polling）
    - 发现完成 → 处理结果 → 立即补充新 subagent（保持并行）
    
    **V1/V2 验证角色**：
    - V1/V2 由 **Main Agent exec** 调用 `/program/paper/scripts/verify_v1.py` / `/program/paper/scripts/verify_v2.py`，**不是独立的 verifier subagent**
    - subagent 只负责生成 JSON，Main Agent 负责验证和调度

    ### V1失败处理（最多2次机会）
    ```
    Reviewer生成reviewer.json
         │
         ▼
    Main Agent exec /program/paper/scripts/verify_v1.py reviewer.json
         │
    ┌────▼────┐
    │ V1 通过  │ → 进入Coach
    │ V1 失败  │
    └────┬────┘
         │
    ┌────▼────┐
    │ 第1次失败 │ → respawn Reviewer（重试1次）
    └────┬────┘
         │
    ┌────▼────┐
    │ 第2次失败 │ → Main Agent亲自接管，生成Part1-3 JSON写入reviewer.json
    └────┬────┘
         │
         ▼
      进入Coach（无论成功与否继续）
    ```

    ### V2失败处理（最多2次机会）
    ```
    Coach生成coach.json
         │
         ▼
    Main Agent exec /program/paper/scripts/verify_v2.py coach.json
         │
    ┌────▼────┐
    │ V2 通过  │ → 进入Code
    │ V2 失败  │
    └────┬────┘
         │
    ┌────▼────┐
    │ 第1次失败 │ → 先修复JSON（引号/格式），再respawn Coach
    └────┬────┘
         │
    ┌────▼────┐
    │ 第2次失败 │ → Main Agent亲自接管，生成Part4-5 JSON写入coach.json
    └────┬────┘
         │
         ▼
      进入Code（无论成功与否继续）
    ```

    ### V3失败处理
    ```
    Code生成Markdown → V3验证
         │
    ┌────▼────┐
    │ V3 通过  │ → 飞书推送
    │ V3 失败  │
    └────┬────┘
         │
      修复Markdown内容 → 重验证
         │
      仍失败 → 报告Mike
    ```

    ⑦ **汇报时机**：
       - 启动时：通知 Mike 开始处理 X 篇
       - 每15分钟：进度汇报（完成/跳过/失败）
       - 异常时：立即报告
       - 全部完成：最终汇总
    
【Audit】生成最终报告，向 Mike 汇报

---

## Phase1a 搜索脚本（search_arxiv_24h.py）

**脚本路径**：`/program/paper/scripts/search_arxiv_24h.py`

**功能**：
- arXiv API 查询5个方向（顺序，每方向间隔10秒防429）
- 精准24小时内过滤（`submittedDate:[YYYYMMDDHHMMSS TO YYYYMMDDHHMMSS]`）
- 按 arXiv ID **去重**（相同ID只保留第一条，避免重复审阅）
- 输出 `all_raw_papers.json`

**5个方向配置**：

| 方向 | arXiv 类别 | 关键词 |
|------|-----------|--------|
| 机器学习 | `cs.LG` | 无 |
| 大模型 | `cs.CL` | 无 |
| 区块链 | `cs.CR` | `blockchain OR distributed ledger OR DeFi OR decentralized finance OR smart contract` |
| 量化交易 | `q-fin.TR` + `q-fin.ST` + `q-fin.PM` | 无（组合查询） |
| 数字资产安全 | `cs.CR` | `cryptocurrency OR crypto-asset OR token OR wallet OR digital asset OR LLM security OR large language model security OR prompt injection OR AI safety` |

```bash
# 执行搜索（输出 all_raw_papers.json）
python3 /program/paper/scripts/search_arxiv_24h.py
```

---

## 飞书去重（每日一次）

**脚本**：`/program/paper/scripts/feishu_dedup_v2.py`

**说明**：
- 按论文标题（题目字段）归类重复记录
- 每篇论文只保留最新一条记录，删除旧记录
- **执行时机**：每日审阅完成后自动执行（或手动触发）

```bash
# 手动执行去重
python3 /program/paper/scripts/feishu_dedup_v2.py
```

---

## 关键命令

| 步骤 | 命令 |
|------|------|
| Phase1a 搜索 | `python3 /program/paper/scripts/search_arxiv_24h.py` |
| PDF 下载 | `curl -L -o "/obsidian/01_Input/04_PDF/{YYYY-MM-DD}-{标题前30字符}.pdf" https://arxiv.org/pdf/{arXiv_id数字部分}.pdf` |
| Markdown 生成 | `python3 /program/paper/scripts/markdown_generator.py <reviewer_json> <coach_json> <output_dir>` |
| 飞书推送 | `python3 /program/paper/scripts/sync_feishu.py <md_path> <pdf_path>` |
| 飞书去重 | `python3 /program/paper/scripts/feishu_dedup_v2.py` |

---

## Phase2 Subagent Spawn 模板（精确指令，禁止修改）

### Reviewer Spawn（复制使用）

```json
{
  "task": "执行论文审阅 - Reviewer阶段（Part1-3）\n\n论文ID：{arxiv_id}\npaper.md：{paper_dir}/paper.md\n输出：{paper_dir}/reviewer.json\n\n步骤：\n1. 读取 paper.md\n2. 读取 /obsidian/00_Auxiliary/03_Prompt/reviewer-prompt.md\n3. 构建 prompt + 调用 LLM 生成 Part1-3 JSON\n4. 保存到 reviewer_path",
  "runtime": "subagent",
  "runTimeoutSeconds": 900,
  "label": "reviewer-{arxiv_id}"
}
```

### Coach Spawn（复制使用）

```json
{
  "task": "执行论文审阅 - Coach阶段（Part4-5）\n\n输入：\n- paper.md：{paper_dir}/paper.md\n- reviewer.json（part2分数≥80）：{paper_dir}/reviewer.json\n- coach-prompt.md：/obsidian/00_Auxiliary/03_Prompt/coach-prompt.md\n输出：{paper_dir}/coach.json\n\n步骤：\n1. 读取 reviewer.json，确认 total_100 ≥ 80\n2. 读取 coach-prompt.md\n3. 生成 Part4-5 JSON\n4. 保存到 coach_path",
  "runtime": "subagent",
  "runTimeoutSeconds": 900,
  "label": "coach-{arxiv_id}"
}
```
```


### Code Phase（纯脚本执行，禁止使用Agent）

**执行方式**：直接调用 `run_code_phase.py`，该脚本内置了正确的文件名算法和原子性保证。

**文件名算法**（`run_code_phase.py` 内置，与 `markdown_generator.py` 的 `safe_name()` 一致）：
```python
import re
title_safe = re.sub(r'[^a-zA-Z0-9]', '_', title[:30])  # 先截断30字符，再替换所有非字母数字为下划线
```

**原子性保证**（`run_code_phase.py` 内置）：
四步（PDF下载 → MD生成 → V3验证 → 飞书推送）中途任何一步失败，自动删除该论文已生成的所有文件（PDF + MD），保持要么全有、要么全无。

```bash
# 单篇执行
python3 /program/paper/scripts/run_code_phase.py {date}

# 示例（处理 2026-04-08 目录下的所有 score>=80 论文）：
python3 /program/paper/scripts/run_code_phase.py 2026-04-08
```

> ⚠️ 旧版内联 bash 示例（V8.26）已废弃，不再维护。请使用 `run_code_phase.py` 作为唯一执行入口。

---

## 输出目录

| 类型 | 路径 |
|------|------|
| 原始 JSON | /home/node/.openclaw/workspace/workareas/shared/papers/{date}/ |
| PDF | /obsidian/01_Input/04_PDF/ |
| Markdown | /obsidian/02_Output/04_论文/ |
| 飞书 | 多维表格（按 arXiv ID 匹配更新） |

---

## 状态持久化

路径：`/home/node/.openclaw/workspace/workareas/shared/papers/{{date}}/status.json`

```json
{
  "workflow": "workflow_paper_arxiv.md",
  "papers": [
    {
      "id": "arXiv:2603.xx",
      "status": "completed",
      "v1": "pass",
      "v2": "pass",
      "v3": "pass",
      "started_at": "2026-03-28 00:00:00",
      "completed_at": "2026-03-28 00:15:00"
    },
    {
      "id": "arXiv:2603.xx",
      "status": "in_progress",
      "current_step": "reviewer",
      "started_at": "2026-03-28 00:10:00",
      "updated_at": "2026-03-28 00:10:00",
      "retries": 0
    },
    {
      "id": "arXiv:2603.xx",
      "status": "score_too_low",
      "reason": "Reviewer score < 80",
      "started_at": "2026-03-28 00:00:00",
      "completed_at": "2026-03-28 00:05:00"
    },
    {
      "id": "arXiv:2603.xx",
      "status": "failed",
      "failed_step": "v2",
      "reason": "timeout or error",
      "started_at": "2026-03-28 00:00:00",
      "retries": 3,
      "max_retries": 5
    },
    {
      "id": "arXiv:2603.xx",
      "status": "killed",
      "last_step": "coach",
      "reason": "subagent timeout (>15min no update)",
      "started_at": "2026-03-28 00:00:00",
      "detected_at": "2026-03-28 00:20:00",
      "retries": 1
    }
  ],
  "started_at": "2026-03-28 00:00:00",
  "last_update": "2026-03-28 01:30:00",
  "last_health_check": "2026-03-28 01:30:00",
  "restarts_total": 5
}
```

**状态说明**：
- `in_progress`: 正在处理（需监控是否超时）
- `completed`: 已完成
- `score_too_low`: 分数<80已跳过
- `failed`: 失败（已达最大重试次数）
- `killed`: 检测到subagent超时被kill，已自动重启

---

## V1/V2 验证规则

### V1 验证（Reviewer JSON）

| 检查项 | 要求 |
|--------|------|
| JSON可解析 | 无语法错误 |
| Part1/Part2/Part3完整 | 三部分字段都存在 |
| scores数组非空 | 至少5个评分维度 |
| total_score范围 | 0-100 |
| level与total_score匹配 | 90+=优秀，80-89=良好，70-79=可投稿，60-69=返修，<60=拒稿 |
| overall_quality与level一致 | 两处等级描述必须相同 |
| institution/authors | 不能是"arXiv:xxx"或"未知"等幻觉值 |

### V2 验证（Coach JSON）

| 检查项 | 要求 |
|--------|------|
| JSON可解析 | 无语法错误 |
| Part4/Part5完整 | 两部分字段都存在 |
| compliance_audit | 5个子字段全部非空 |
| restructure_items | 每项reason非空 |
| restructured_text | 必须为英文 |

### 重试机制

- **LLM调用失败**：重试5次（30s/60s/120s/240s/480s指数退避）
- **PDF下载失败**：重试5次，仍失败则跳过该论文
- **arXiv API 429/超时**：重试5次（30s/60s/120s/240s/480s指数退避）

## Mike 汇报格式（最终报告）

```
【Main】📋 每日论文审阅报告

日期：2026-03-28
工作流：workflow_paper_arxiv.md

执行概况：
| 项目 | 数值 |
|------|------|
| 原始论文 | X篇 |
| 通过初筛 | X篇（≥8分） |
| 完成审阅 | X篇 |
| Coach跳过（<80分） | X篇 |
| 失败 | X篇 |

通过论文（≥80分）：
| arXiv ID | 评分 | 标题 |
|----------|------|------|
| 2603.xx | 85 | xxx |

Coach跳过论文（<80分）：
| arXiv ID | 评分 | 标题 |
|----------|------|------|
| 2603.xx | 75 | xxx |

失败论文：
| arXiv ID | 失败环节 | 原因 |
|----------|---------|------|
| 2603.xx | V2 | xxx |

总耗时：X小时Y分钟
```

---

## 各 Agent 详细规则

| 角色 | 执行方式 | 职责 |
|-------|---------|------|
| Main Agent | 直接 exec | Phase1（Python脚本）、Phase2b 调度、V1/V2/V3 验证 |
| Reviewer subagent | sessions_spawn | Part1-3 JSON，含评分表格 |
| Coach subagent | sessions_spawn | Part4-5 JSON（仅≥80分） |
| Code phase | **exec脚本（禁止subagent）** | markdown_generator + verify_v3 + sync_feishu |

---

**状态**：V8.25（Code阶段完全固化=exec脚本，禁止subagent；预删除防残留；V3失败强制修复）