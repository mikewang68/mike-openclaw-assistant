# Knowledge Pipeline

基于 MongoDB + Neo4j 的论文知识图谱流水线。

## 架构

```
PDF → MD（MinerU pdftext） → documents/triples（MongoDB） → Neo4j → Reasoner
```

## 数据结构

### documents（1条/篇）
| 字段 | 说明 |
|------|------|
| doc_id | PDF 文件名（不含扩展名） |
| path | 评阅MD（有评阅时）/ 原始MD（无评阅时） |
| pdf_path | PDF 完整路径 |
| has_review | True = 有评阅意见，False = 仅原始MD |
| title | 论文标题（从MD提取） |
| tags | 领域标签 |
| summary | 摘要 |

### triples
| 字段 | 说明 |
|------|------|
| source | PDF 文件名 |
| subject / relation / object | 三元组 |
| inferred | True = 推理得出，False = 原始抽取 |

## 核心脚本

| 脚本 | 作用 |
|------|------|
| `pdf_to_md.py` | PDF → MD，使用 MinerU pdftext（~1秒/篇） |
| `batch_process.py` | 批量处理入口：PDF转换 → 索引 → triples抽取 → Neo4j同步 |
| `review_extractor.py` | 评阅MD → regex 三元组抽取 |
| `mongo_writer.py` | MongoDB 写入（triples + documents） |
| `neo4j_writer.py` | Neo4j 写入 |
| `sync_neo4j.py` | MongoDB → Neo4j 同步 |
| `reasoner_v2.py` | 推理引擎 v2（组合规则 + 置信度衰减） |
| `llm_batch_extract.py` | 原文MD → LLM 三元组抽取（需 subagent） |

## 双路抽取逻辑

```
论文处理时：
├── 02_Output 有对应评阅意见 → 双路：评阅regex + 原文LLM
└── 02_Output 无评阅意见   → 单路：仅原文LLM
```

## PDF → MD frontmatter 格式

```yaml
---
source: "[[文件名.pdf]]"
type: text
---

[正文...]
```

注意：`source` 必须用 `type: text` + 外层引号 `""` 包裹 wiki link，否则 Obsidian Properties 面板会错误渲染。

## 使用方法

```bash
# 批量处理（PDF转换 + 索引 + triples + Neo4j + Reasoner）
cd /program/knowledge && python3 batch_process.py

# 仅 PDF → MD
python3 pdf_to_md.py

# 仅评阅抽取
python3 -c "from batch_process import batch_review_extraction; print(batch_review_extraction())"
```

## MongoDB 状态

```bash
cd /program/knowledge && python3 -c "from mongo_writer import MongoWriter; w=MongoWriter(); print(w.stats()); w.close()"
```

## 修改记录

### 2026-04-15
- **reasoner_v2.py**：新增自环过滤 `if current["subject"] == next_t["object"]: continue`，防止 `A --USES--> A` 等自环推理
- **mongo_writer.py**：回退 `write_inferred()` 为 `insert_one` 版本（原 upsert 版本会被 `$set` 覆盖 `created_at`）
- **文件权限修复**：NAS 挂载 owner 问题导致部分 py 文件变为 `nobody:nogroup`，已全部恢复为 `node:node`
- **workflow_knowledge_pipeline.md**：更新 `reasoner.py` → `reasoner_v2.py`，`pipeline.py` 导入改为 `from reasoner_v2 import forward_chain`
- **清理**：删除 Gemini 3.1 Pro 生成的废脚本 `find_insights.py`、`query_db.py`、`fix_mongo.py`
- **reasoner_v2 执行结果**：10357 条 triples → 生成 100 条 inferred（置信度 0.35+），78 条写入 MongoDB
- **Neo4j 同步**：reasoner_v2 结果 118 条 inferred edges 已写入 Neo4j（但 node 未创建，edge 挂载在已有节点上）

### 2026-04-14
- **entity_aligner.py**：新增 `"this paper"` `"our model"` `"our method"` → `"DELETED_REDUNDANT"` 过滤规则，防止无意义自指实体写入图谱
- **entity_discovery.py**：新增 `is_protected_acronym()` 函数，保护 2-6 字符全大写词不被 fuzzy match 误替换
- **YAML alias 规则**：FHE → Homomorphic Encryption、CKKS 保持不变（受保护 acronym）
- **entity_aligner fuzzy matching**：已接入主流程，阈值 0.85（普通）/ 0.95（acronym），len_diff > 15 跳过
- **Neo4j sync**：MongoDB 8648 nodes / 8364 edges（sync_neo4j 后）

### 2026-04-13
- **MinerU pdftext 替换 pdfminer**：速度更快（~1秒/篇 vs ~2秒/篇），保留字体/段落结构
- **PDF转换脚本** `pdf_to_md.py`：改用 MinerU `pdftext`，frontmatter 格式改为 `source: "[[filename.pdf]]"`（外层引号）
- **权限修复**：MD 文件权限固化为 644
- **索引逻辑修正**：`documents` 始终指向 PDF；`path` 优先使用评阅MD，无则用原始MD
- **batch_process.py 重写**：完整实现索引优先级 + 双路抽取逻辑
