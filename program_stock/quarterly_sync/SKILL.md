# SKILL.md - 季度财务数据同步

## 触发条件
- Mike 要求运行季度财务同步
- 财报公布后（年报/一季报/半年报/三季报）

## 关键文件
- 主程序：`/home/node/.openclaw/workspace/workareas/quant/quarterly_sync/quarterly_sync.py`
- 状态文件：`/home/node/.openclaw/workspace/workareas/quant/quarterly_sync/.sync_state.json`

## 使用方式

### 1. 首次运行（全量同步）
```bash
python3 /home/node/.openclaw/workspace/workareas/quant/quarterly_sync/quarterly_sync.py --type all --workers 10
```

### 2. 断点续传
```bash
python3 /home/node/.openclaw/workspace/workareas/quant/quarterly_sync/quarterly_sync.py --type all --resume
```

### 3. 分类型同步
```bash
# 仅财务报表（资产负债表+利润表+现金流量表）
python3 quarterly_sync.py --type fin_stmt --workers 10

# 仅财务分析指标（ROE/ROA/毛利率等）
python3 quarterly_sync.py --type indicator --workers 10

# 仅公司基本信息
python3 quarterly_sync.py --type profile --workers 10

# 仅行业分类（一次性，全量）
python3 quarterly_sync.py --type industry
```

## 数据目标（MongoDB stock 数据库）

| Collection | 来源 | 内容 | 记录规模 |
|---|---|---|---|
| fin_zcfz | stock_balance_sheet_by_report_em | 资产负债表 | ~5000股×4季/年 |
| fin_lrb | stock_profit_sheet_by_report_em | 利润表 | ~5000股×4季/年 |
| fin_xjll | stock_cash_flow_sheet_by_report_em | 现金流量表 | ~5000股×4季/年 |
| fin_indicator | stock_financial_analysis_indicator_em | 财务指标（ROE/ROA/毛利率等） | ~5000股×4季/年 |
| company_profile | stock_profile_cninfo | 公司基本信息 | ~5000股（一次性） |
| industry_class | stock_industry_category_cninfo | 证监会行业分类 | ~2000条（一次性） |

## akshare 接口映射

```
财务报表 → stock_balance/profit/cash_flow_sheet_by_report_em
财务指标 → stock_financial_analysis_indicator_em
公司信息 → stock_profile_cninfo
行业分类 → stock_industry_category_cninfo
```

## 并行策略
- 10线程，每秒最多5次API调用
- 全量同步 ~5000只股票约需 5000/5 = 1000秒 ≈ 17分钟
- 所有写入使用 upsert（InsertOrUpdate），可重复运行不丢数据

## 断点续传
- 状态文件 `.sync_state.json` 自动保存每批完成的股票代码
- `--resume` 参数可从断点继续（不重复同步已完成股票）
- 进程被中断后，直接重新运行 `--resume` 即可

## Cron 配置（每季度运行一次）

### 时间点（Asia/Shanghai）

| 财报类型 | 运行时间 | 说明 |
|---|---|---|
| 年报 + 一季报 | 5月10日 10:00 | 年报（4月底截止）+ 一季报同步完成 |
| 半年报 | 9月10日 10:00 | 半年报8月底截止 |
| 三季报 | 10月31日 10:00 | 三季报10月底截止 |
| 年报预告 | 2月15日 10:00 | 部分公司提前发年报预告 |

```bash
# 飞书通知脚本（sync_notify.py）
python3 /home/node/.openclaw/workspace/workareas/quant/quarterly_sync/quarterly_sync.py --type all --workers 10
```

## 注意事项
1. akshare 有频率限制，不要超过 5 calls/s
2. 公司信息（company_profile）和行业分类（industry_class）只需同步一次，后续财报更新只用 `--type fin_stmt` 或 `--type indicator`
3. 先运行 `--type industry` 建立行业分类基础数据
4. MongoDB 需要认证，URI 已硬编码在脚本中
