# 确定性评分规范 v1

状态：Accepted（P0，2026-07-21）

## 原则

- LLM 只判断结构化关系，不生成总分。
- 相同冻结输入和相同结构化判断必须得到完全相同的评分结果。
- 每一步都输出中间值，便于解释和重算。
- confidence 只用于审计和人工校准，v1 不参与计算。

## 根因/分类/分析链策略（当前 KDiag Case 默认）

当 `scoring_strategy.mode=root_category_chain` 时，规范固定为：

- 只有一个 `critical root_cause`，权重100。
- 一个 `classification` 表示问题分类，权重20。
- 每组“证据N/结论N”形成一个 `analysis_chain`，所有分析链等分且权重合计60；每项必须提供 `evidence_keyword` 和 `conclusion`。
- 不建立“直接原因”评分项，也不使用 `direct_cause`。
- 根因只有完全命中和未完全命中两种计分结果；完全命中直接得到100分并停止，不再计算分类或分析链。
- 根因未完全命中时，分类只有完全命中才得20分。
- 分析链每条的满分是 `60 / 链条数`；其中一半由 `evidence_keyword` 在 AI 报告原文中的连续强匹配决定，另一半乘以大模型给出的 `conclusion_similarity`（0～1）。
- 最终分为 `max(0, 分类得分 + 分析链得分 - Forbidden Claim 扣分)`，最高80分；此策略没有幻觉扣分。

该策略允许根因权重100和证据链权重80同时存在，因为它们是互斥的两条计分路径，不相加。

## 通用加权策略

`scoring_strategy.mode=weighted_sum` 时，所有 Gold Claim 与 Causal Edge 的 weight 合计为100。Forbidden Claim 的 penalty 不计入该合计。

Claim 关系系数：

| relation | 系数 |
|---|---:|
| match | 1.0 |
| partial_match | 0.5 |
| missing | 0.0 |
| contradiction | 0.0 |

Candidate 确定程度系数：

| certainty | 系数 |
|---|---:|
| confirmed | 1.0 |
| probable | 0.9 |
| suspected | 0.7 |
| possible | 0.5 |

单个 Claim 得分：claim_weight × relation_factor × certainty_factor。missing 和 contradiction 的 certainty_factor 固定按 1.0 处理，因为关系系数已为 0。

因果边系数：

| relation | 系数 |
|---|---:|
| edge_match | 1.0 |
| edge_partial | 0.5 |
| edge_missing | 0.0 |
| edge_reversed | 0.0 |
| edge_conflict | 0.0 |

只有 Edge 两端 Gold Claim 都找到非 missing/contradiction 的节点对齐后才调用 Edge Judge；否则确定性标记为 edge_missing，避免模型补全不存在的因果链。

## 对齐选择

- 每个 Gold Claim 必须有且只有一个语义判定；它直接针对完整 AI 报告原文，不生成或依赖 Candidate Claim。
- Python 校验 Case 哈希和报告哈希；模型不提取报告原文引用。
- `partial_match` 只有主体和谓词都正确时才允许；不同进程、服务、线程或故障对象必须判 `missing`。
- 分析链的日志 `evidence_keyword` 由 Python 强匹配，语义 Judge 只对 `conclusion` 输出 `conclusion_similarity`。

## 惩罚

推荐执行两类惩罚：

1. 命中 Forbidden Claim：扣除其 penalty；同一 Forbidden Claim 最多扣一次。
2. critical root_cause Gold Claim 被判为 contradiction：额外扣 scoring policy 中的 critical_root_cause_contradiction_penalty；避免错误根因与单纯遗漏得到相同结果。

惩罚总额可配置上限，推荐默认不超过 100。每个惩罚必须保存 Judge 理由和对应的语义判定。

## 门禁和计算顺序

推荐顺序：

1. 汇总 Claim 与 Edge 正向得分，得到 positive_score。
2. 扣除惩罚：penalized_score = max(0, positive_score - penalties)。
3. 计算显式 Forbidden Claim 门禁；根因遗漏或冲突本身不触发直接失败或分数封顶。
4. 若命中 failure_gate Forbidden Claim，passed = false，但 total_score 仍等于 penalized_score，以保留过程诊断分。
5. 否则 total_score = penalized_score。
6. total_score 大于等于 pass_threshold 时 passed = true。

v1 门禁：

- 任一 failure_gate Forbidden Claim 命中：直接失败。
- critical root_cause 为 missing 时该项得 0 分，但过程项正常计分。
- critical root_cause 为 contradiction 时该项得 0 分并扣额外罚分，但不直接失败、不封顶；最终是否通过只由扣分后的总分与 pass_threshold 决定。

## 指标

- total_score：应用惩罚后的 0～100 分；显式 Forbidden Claim 门禁只改变 passed，不覆盖诊断分。
- positive_score：门禁前正向得分。
- claim_coverage：获得大于 0 分的 Claim 权重 / 全部 Claim 权重。
- exact_claim_coverage：match 的 Claim 权重 / 全部 Claim 权重。
- causal_chain_score：实际 Edge 得分 / Edge 总权重；无 Edge 时为 null。
- core_conclusion_score：critical root_cause Claim 实得分 / 其总权重；无此类型时为 null。
- contradiction_count、forbidden_hit_count、missing_critical_count。

所有比例字段使用 0～1，展示层自行转为百分比；分数使用 Decimal 计算，最终保留两位小数，ROUND_HALF_UP。

## 重算

结构化抽取和 Judge 结果可冻结后用新 Scoring Policy 重算，但必须产生新的派生 Result，并标记原始 Run 与重算策略，不能覆盖原结果。
