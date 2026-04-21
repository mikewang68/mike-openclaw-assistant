# workflow_paper_personal.md - 个人论文审阅工作流

**版本**：V6.3
**最后更新**：2026-04-05

---

**最后更新**：2026-04-05

- V6.3：PDF文件名={date}-{author}.pdf，MD文件={date}-{author}-评阅意见.md，source指向实际PDF文件名

---

## 与arXiv审阅的区别（必须遵守）
1. **PDF来源**：本地PDF附件（非arXiv）
2. **无分数门槛**：Reviewer不论多少分都进Coach（无≥80门槛）
3. **不推飞书**：始终跳过飞书推送（本地文件含中文姓名，属隐私）

---

## 触发条件
Mike发送PDF附件 + "审阅这篇"

---

## 核心约束
1. **系统默认模型**
2. **LLM调用**：think=false
3. **中文输出**：审阅意见中文，标题/摘要/引用英文原文
4. **单篇执行**：只有一篇论文，无需串行

---

## 重试机制（所有Agent）
- 最大重试：5次
- 初始等待：30秒
- 指数退避：30s, 60s, 120s, 240s, 480s

---

## 执行流程

```
Mike发送PDF附件
    ↓
【Main】
1. 复制PDF到/obsidian/01_Input/04_PDF/{date}-{author}.pdf
   - PDF命名：{YYYY-MM-DD}-{author}.pdf（如 `2026-04-05-曹德龙.pdf`）
   - MD命名：{YYYY-MM-DD}-{author}-评阅意见.md
   - source字段：指向实际PDF文件名（如 `[[2026-04-05-曹德龙.pdf]]`）
2. 提取文本到paper.md（存入工作目录）
3. 创建工作目录：/home/node/.openclaw/workspace/workareas/shared/papers/personal/{date}-{author}/
4. 中间文件（reviewer.json、coach.json）存入工作目录，**禁止写入Obsidian**
5. 初始化status.json
    ↓
┌─────────────────────────────────────┐
│ 步骤A：Reviewer（Part1-3）           │
│ Prompt：reviewer-prompt.md          │
│ 1. 读取paper.md生成Part1-3        │
│ 2. 保存到{工作目录}/{author}_reviewer.json │
│ 3. 更新status.json                 │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 【V1检查】Verifier                  │
│ 执行：python3 /program/paper/scripts/verify_v1.py {reviewer_json}    │
│ ✅通过 → 进入Coach                 │
│ ❌失败 → 修复Reviewer，重新V1检查 │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 步骤B：Coach（Part4-5）             │
│ Prompt：coach-prompt.md             │
│ 1. 读取Reviewer Part2-3（含评分）│
│ 2. 生成Part4-5 JSON               │
│ 3. 保存到{工作目录}/{author}_coach.json   │
│ 4. 更新status.json                 │
│ **不论分数多少，始终执行Coach**     │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 【V2检查】Verifier                  │
│ 执行：python3 /program/paper/scripts/verify_v2.py {coach_json}    │
│ ✅通过 → 进入Code                 │
│ ❌失败 → 修复Coach，重新V2检查   │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 步骤C：Code                         │
│ **重要**：`{author}` = 原始PDF文件名中提取的作者姓名（如 `2026-04-05-曹德龙---d0b0a38c...pdf` → `曹德龙`）    │
│ 1. 生成Markdown（markdown_generator.py）    │
│    python3 /program/paper/scripts/markdown_generator.py {reviewer_json} {coach_json} {output_dir} [pdf] [date] '' {author}    │
│ 2. 执行V3检查（verify_v3.py）    │
│ 3. **不推飞书**（跳过）           │
│ 4. 更新status.json                 │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 【V3检查】Verifier                  │
│ 执行：python3 /program/paper/scripts/verify_v3.py {md_path} {arxiv_id}    │
│ ✅通过 → 完成                     │
│ ❌失败 → 修复Code，重新V3检查   │
└─────────────────────────────────────┘
    ↓
【Audit】记录日志，向Mike汇报结果
```
    ↓
┌─────────────────────────────────────┐
│ 【V3检查】Verifier                  │
│ 检查Markdown文件                     │
│ ✅通过 → 完成                      │
│ ❌失败 → 修复Code，重新V3检查   │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ 【Audit】                           │
│ 记录执行日志                        │
│ 向Mike发送执行报告                 │
└─────────────────────────────────────┘
```

---

## 论文审阅格式

### Part1（基础信息）
```json
{
  "part1": {
    "tags": ["DASecurity"],
    "title": "论文完整标题（英文）",
    "keywords": ["keyword1", "keyword2"],
    "date": "2026-04-05",
    "source": "2026-04-05-曹德龙.pdf"
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
| V3-3 | 文件名格式 | {YYYY-MM-DD}-{author}-评阅意见.md（如 2026-04-05-曹德龙-评阅意见.md） |
| V3-4 | 无路径残片 | 不含_obsidian_ |
| V3-5 | 五部分完整 | ## 第一部分 至 ## 第五部分 |
| V3-6 | 无占位符 | 不含{{ |
| V3-7 | 非空文件 | >500 bytes |
| V3-8 | PDF文件存在 | /obsidian/01_Input/04_PDF/{source.pdf} |
| V3-9 | source与PDF匹配 | 从source提取文件名，验证PDF存在 |
| V3-10 | 无额外内容 | 第五部分后无禁止标题 |

---

## 状态持久化

路径：`/home/node/.openclaw/workspace/workareas/shared/papers/personal/{date}/status.json`

```json
{
  "workflow": "workflow_paper_personal.md",
  "paper_id": "personal_xxxxx",
  "status": "completed",
  "v1": "pass",
  "v2": "pass",
  "v3": "pass",
  "started_at": "2026-03-25 14:00:00",
  "completed_at": "2026-03-25 14:30:00"
}
```

---

## Mike汇报格式

```
【Main】📋 个人论文审阅报告

论文：{标题}
评分：{总分}/100
审阅状态：✅完成

| 环节 | 状态 |
|------|------|
| Reviewer | ✅ |
| V1检查 | ✅ |
| Coach | ✅ |
| V2检查 | ✅ |
| Code | ✅ |
| V3检查 | ✅ |

输出文件：/obsidian/02_Output/04_论文/{文件名}
总耗时：X分钟
```

---

**状态**：固化 V5.0