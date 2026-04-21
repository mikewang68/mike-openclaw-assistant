# workflow_paper_batch.md - 本地批量论文审阅工作流

**版本**：V5.0
**最后更新**：2026-03-25

---

## 触发条件
Mike提供本地文件夹路径（如 `/path/to/papers/`）

---

## 核心约束
1. **系统默认模型**
2. **LLM调用**：think=false
3. **串行执行**：每篇论文必须完整走完才处理下一篇
4. **中文输出**：审阅意见中文，标题/摘要/引用英文原文
5. **隐私判断**：文件名含`2026-MM-DD-{2-4汉字}`则不上传飞书

---

## 重试机制（所有Agent）
- 最大重试：5次
- 初始等待：30秒
- 指数退避：30s, 60s, 120s, 240s, 480s

---

## 执行流程

```
Mike提供文件夹路径
    ↓
【Main】
1. 扫描文件夹中的PDF文件
2. 识别文件名（arXiv/普通/隐私）
3. 读取status.json（检查已完成）
4. 或初始化新workflow
    ↓
对每篇论文【串行】执行：
┌─────────────────────────────────────┐
│ 步骤A：Reviewer（Part1-3）           │
│ 1. 解析本地PDF获取论文内容           │
│ 2. 保存到{paper_id}_paper.md      │
│ 3. 生成Part1-3 JSON               │
│ 4. 保存到{paper_id}_reviewer.json  │
│ 5. 更新status.json                 │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 【V1检查】Verifier                  │
│ 检查Reviewer JSON                   │
│ ✅通过 → 进入下一步                │
│ ❌失败 → 修复Reviewer，重新V1检查 │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 步骤B：Coach（Part4-5）             │
│ 1. 读取Reviewer Part2-3（含评分）│
│ 2. 生成Part4-5 JSON               │
│ 3. 保存到{paper_id}_coach.json    │
│ 4. 更新status.json                 │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 【V2检查】Verifier                  │
│ 检查Coach JSON                     │
│ ✅通过 → 进入下一步                │
│ ❌失败 → 修复Coach，重新V2检查   │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 步骤C：Code                         │
│ 1. PDF已存在（本地文件）            │
│ 2. 命令: cp "{本地PDF路径}" /obsidian/01_Input/04_PDF/{source_filename}    │
│ 3. 生成Markdown                   │
│ 4. 执行V3检查（10项）                    │
│ 5. 推送飞书（通过V3后）    │
│    调用：python3 /program/paper/scripts/sync_feishu.py <md_path> <pdf_path>    │
│    ✅ 推送：文件名不以日期开头    │
│    ✅ 推送：日期开头 + 非2-4个汉字    │
│    ❌ 不推送：日期开头 + 2-4个汉字（隐私论文）    │
│ 6. 更新status.json                 │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 【V3检查】Verifier                  │
│ 检查Markdown文件                     │
│ ✅通过 → 继续下一篇               │
│ ❌失败 → 修复Code，重新V3检查   │
└─────────────────────────────────────┘
    ↓
继续下一篇论文...
    ↓
┌─────────────────────────────────────┐
│ 【Audit】                           │
│ 记录执行日志                        │
│ 向Mike发送执行报告                 │
└─────────────────────────────────────┘
```

---

## 隐私判断规则

| 文件名格式 | 判断 | 动作 |
|-----------|------|------|
| `2026-MM-DD-{2-4汉字}.pdf` | 隐私论文 | 不上传飞书 |
| `arXiv_*.pdf` | arXiv论文 | 上传飞书 |
| 其他 | 普通论文 | 上传飞书 |

---

## 论文审阅格式

### Part1（基础信息）
```json
{
  "part1": {
    "tags": ["DASecurity"],
    "title": "论文完整标题（英文）",
    "keywords": ["keyword1", "keyword2"],
    "date": "2026-03-25",
    "source": "2026-03-25-Title_First_30_Chars.pdf"
  }
}
```
⚠️ tags：必须是LLM/Blockchain/Deeplearning/Quant/DASecurity之一

### Part2（五维评分）
```json
{
  "part2": {
    "overall_quality": "7/10",
    "research_topic": "研究主题",
    "paper_abstract": "英文摘要原文",
    "focused_area": "聚焦领域",
    "focused_problem": "聚焦问题",
    "technical_approach": "技术路线",
    "experimental_design": "实验设计",
    "experimental_results": "实验结果",
    "innovations": ["创新点1", "创新点2"],
    "limitations": ["局限性1"],
    "journal_level": "7/100 / CCF A / 中科院1区",
    "journal_suggestion": "建议投稿期刊/会议",
    "summary": "150字论文总结（中文）",
    "scores": {
      "breakthrough": 26,
      "rigor": 22,
      "theory": 13,
      "practical": 11,
      "logic": 13,
      "total_50": 85,
      "total_100": 85
    }
  }
}
```

### Part3（深层审讯）
```json
{
  "part3": {
    "domain_criteria_match": { "analysis": "..." },
    "logic_chain_audit": { "analysis": "..." },
    "interpretability_transparency": { "analysis": "..." },
    "experimental_efficiency_stress_test": { "analysis": "..." },
    "generalization_robustness": { "analysis": "..." }
  }
}
```

### Part4（修改建议）
```json
{
  "part4": {
    "p0_mandatory": [{ "issue": "...", "location": "...", "suggestion": "...", "reason": "..." }],
    "p1_enhancement": [{ "issue": "...", "location": "...", "suggestion": "...", "reason": "..." }],
    "p2_polishing": [{ "issue": "...", "suggestion": "..." }],
    "audit_conclusion": "贡献占比及合规审计结论"
  }
}
```

### Part5（范式重构）
```json
{
  "part5": {
    "restructure_items": [
      { "location": "...", "original_text": "...", "restructured_text": "...", "reason": "..." }
    ]
  }
}
```

---

## V3检查项（10项）
| # | 检查项 | 通过标准 |
|---|--------|----------|
| V3-1 | Markdown存在 | os.path.exists() |
| V3-2 | 目录正确 | /obsidian/02_Output/04_论文/ |
| V3-3 | 文件名格式 | {日期}-{标题30字符}-评阅意见.md |
| V3-4 | 无路径残片 | 不含_obsidian_ |
| V3-5 | 五部分完整 | ## 第一部分 至 ## 第五部分 |
| V3-6 | 无占位符 | 不含{{ |
| V3-7 | 非空文件 | >500 bytes |
| V3-8 | PDF文件存在 | /obsidian/01_Input/04_PDF/{source.pdf} |
| V3-9 | source与PDF匹配 | 从source提取文件名，验证PDF存在 |
| V3-10 | 无额外内容 | 第五部分后无禁止标题 |

---

## 状态持久化

路径：`/home/node/.openclaw/workspace/workareas/shared/papers/batch/{date}/status.json`

```json
{
  "workflow": "workflow_paper_batch.md",
  "papers": [
    {"id": "local_xxxx", "filename": "xxx.pdf", "status": "completed", "v1": "pass", "v2": "pass", "v3": "pass", "privacy": false},
    {"id": "local_xxxx", "filename": "2026-03-15-张三.pdf", "status": "completed", "v1": "pass", "v2": "pass", "v3": "pass", "privacy": true},
    {"id": "local_xxxx", "filename": "xxx.pdf", "status": "in_progress", "current_step": "coach"},
    {"id": "local_xxxx", "filename": "xxx.pdf", "status": "failed", "failed_step": "v2", "reason": "xxx", "retries": 3}
  ],
  "current_paper_index": 5,
  "total_papers": 10,
  "started_at": "2026-03-25 14:00:00",
  "last_update": "2026-03-25 14:30:00"
}
```

---

## Mike汇报格式（最终报告）

```
【Main】📋 本地批量论文审阅报告

文件夹：{path}
日期：2026-03-25
工作流：workflow_paper_batch.md

执行概况：
| 项目 | 数值 |
|------|------|
| 扫描论文 | 10篇 |
| 进入审阅池 | 9篇 |
| 完成审阅 | 8篇 |
| 失败 | 1篇 |
| 隐私论文 | 1篇 |

通过论文：
| 文件名 | 评分 | 隐私 | 状态 |
|--------|------|------|------|
| xxx.pdf | 85 | 否 | ✅ |
| 2026-03-15-张三.pdf | 82 | 是 | ✅ |

失败论文：
| 文件名 | 失败环节 | 原因 |
|--------|---------|------|
| xxx.pdf | V2 | xxx |

总耗时：X小时Y分钟
```

---

**状态**：固化 V5.0