# Eval Spec 与 AI 报告评分器设计

## 1. 功能目标

对完整 AI 分析报告进行可解释评分。报告不拆句、不抽取 Candidate Claim，也不要求先转换为 Report JSON。

```text
Case JSON + 完整 AI 报告
→ Python 强匹配分析链日志关键字，生成对齐草稿
→ claude/OpenCode 对完整报告与 Gold Claim 做语义判定
→ Python 校验哈希，按固定公式计分
```

## 2. 对齐草稿

`prepare-alignment` 创建一个由 Python 拥有的 JSON：

- Case 内容哈希、报告内容哈希；
- Gold Claim 的 `id`、类型、标准结论；
- 每条分析链的 `python_keyword_audits`：关键字、是否强匹配、关键字分；
- 留空的 `semantic_alignment.alignments`。

大模型只能填写 `semantic_alignment.alignments`，不得更改哈希、Python 关键字结果或报告原文。

## 3. 语义对齐

每个 Gold Claim 恰有一个对齐结果：

```json
{
  "gold_claim_id": "chain-1",
  "relation": "partial_match",
  "confidence": 0.82,
  "reason": "核心结论正确，但缺少关键限定。",
  "subject_match": true,
  "predicate_match": true,
  "causal_direction_match": null,
  "missing_essential_facts": ["关键限定"],
  "conclusion_similarity": 0.5
}
```

- `relation` 为 `match`、`partial_match`、`missing` 或 `contradiction`。
- `partial_match` 必须同时满足主体和谓词匹配。不同进程、服务、线程或故障对象不得给部分命中。
- 根因必须完整覆盖机制和因果方向才是 `match`；根因不计算部分分。
- 分类必须完全正确才是 `match`。
- 分析链语义仅比较 `conclusion`，必须给 `conclusion_similarity`（0 到 1）；日志关键字不交给大模型判断。

## 4. KDiag 根因/分类/分析链策略

- 根因完全命中：直接 100 分，停止后续评分。
- 否则分类正确得 20 分。
- 分析链总分 60，按链条数均分；每条一半来自 Python 日志关键字强匹配，另一半为 `conclusion_similarity` 乘以该半分。
- 该策略没有幻觉扣分；根因未完整命中时最高 80 分。`forbidden_claims` 仍可按配置扣分或门禁。

## 5. 审计与可复现性

结果保存 Case/报告哈希、每条关键字审计、语义判定、Judge 运行器和 Prompt/响应哈希、时长及最终分数。报告或 Case 变化后，`score-with-alignment` 会拒绝复用旧草稿。

## 6. 前端/API 接口

前端按三步调用即可：

1. 提交 Case 与报告，调用 `prepare-alignment`；
2. 展示 Python 已完成的关键字结果，并让语义 Agent 填写对齐；
3. 提交同一草稿给 `score-with-alignment`，展示每条得分、引用和总分。

API 不暴露或要求用户理解 Candidate Claim、句子切分或内部 ID。
