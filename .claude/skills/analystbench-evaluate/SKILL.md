---
name: analystbench-evaluate
description: 使用本地 Case JSON 或已发布的 Case，对一份或多份 AI 报告评分和对比。Python 匹配分析链日志证据，当前 Claude 只确认根因、分类和结论语义。
---

# AnalystBench 报告评分

报告直接使用 `.md`、`.txt`、`.log` 等 UTF-8 原文；不转换为 Report JSON，不切分报告，也不创建 Candidate Claim。

## 本地 Case JSON：推荐流程

草稿路径必须包含 `${case_key}` 和 8 位随机 hex 后缀（如 `alignment-draft-case-1-a3f7b2c1.json`），避免并发冲突。此处 `${case_key}` 是本地 Case JSON 文件名的 stem，仅用于本地临时结果目录命名，与数据库中用户命名的发布 `case_key` 无关。

### 第 1 步：生成 Python 评分草稿

```bash
DRAFT=./data/workspaces/alignment-draft-${case_key}-$(openssl rand -hex 4).json
.venv/bin/analystbench prepare-alignment \
  <case.json> \
  <report1.md> [report2.md ...] \
  --output "$DRAFT"
```

该命令不会调用大模型。`python_keyword_audits` 是 Python 对每条分析链日志证据的强匹配结果和证据半分，Claude 不读取、不判断、不修改它。

### 第 2 步：Claude 只填写结论语义

读取完整报告、`case.gold_claims` 与草稿，只填写：
`reports.<报告文件名>.semantic_alignment.alignments`。

不要修改哈希或 `python_keyword_audits`，不要定位日志、不提取 quote、不生成 Candidate Claim。

每个 Gold Claim 填写：

```json
{
  "gold_claim_id": "chain-1",
  "relation": "match",
  "confidence": 0.95,
  "reason": "报告明确得出休眠超时结论。",
  "subject_match": true,
  "predicate_match": true,
  "causal_direction_match": null,
  "missing_essential_facts": [],
  "conclusion_similarity": 1.0
}
```

规则：

- 模型只判断根因、问题分类或分析链 `conclusion` 的语义，不找日志证据。
- 分析链必须填写 `conclusion_similarity`（0 到 1）。关键字命中与否不影响模型的语义判断：关键字命中而结论错误，仍应为 `missing`；关键字未命中而结论正确，语义仍可为 `match`，但证据半分为 0。
- 根因只有完整覆盖机制和因果方向才得分。
- 分类使用语义及领域别名归一化，不要求编码逐字一致；`HM_PANIC_SYSMGR` 与 `sysmgr panic` 必须 `match`。只有泛化的 `panic` 不足以命中。
- `partial_match` 必须同时满足主体和谓词匹配；不同进程、服务、线程或故障对象应为 `missing`。

### 第 3 步：Python 计分

```bash
.venv/bin/analystbench score-with-alignment \
  <case.json> \
  "$DRAFT" \
  <report1.md> [report2.md ...]
```

Python 会校验 Case 和报告哈希，并按固定规则生成 Markdown 与 JSON 结果；此命令不调用 Claude 或 OpenCode。

### 第 4 步：清理草稿

评分完成后删除草稿文件：

```bash
rm "$DRAFT"
```

## 数据库模式

```bash
.venv/bin/analystbench evaluate <case_key> <report1.md> [report2.md ...]
```

默认由本机 `claude -p` 对完整报告做语义判断；`--judge opencode` 可切换到 OpenCode。`--judge lexical` 仅用于开发排障，不作为正式结果。
