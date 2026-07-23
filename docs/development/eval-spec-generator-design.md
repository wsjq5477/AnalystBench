# 自然语言标准答案转 Eval Spec 功能设计

## 1. 功能目标

将人工编写的自然语言标准答案转换为结构化、可编辑、可版本化的评分标准 Eval Spec。

该模块只生成评分标准草稿，不直接评价 Agent 报告。

## 2. 输入

必选输入：

- 问题描述
- 人工标准答案

可选输入：

- 原始日志、堆栈或代码片段
- 指标、工单或事件记录
- 问题领域与任务类型
- 用户补充说明

## 3. 输出

Eval Spec 至少包含：

```json
{
  "claims": [],
  "causal_edges": [],
  "forbidden_claims": [],
  "scoring_policy": {}
}
```

## 4. Claim

Claim 表示标准答案中的一个原子结论。

```json
{
  "id": "root",
  "type": "root_cause",
  "statement": "造成问题的核心机制或关键结论",
  "importance": "critical",
  "weight": 30,
  "source_quote": "标准答案中的对应原文"
}
```

推荐类型：

- `trigger`：直接触发问题或结果的事件
- `symptom`：观察到的异常现象
- `localization`：问题所在组件、模块、流程或对象
- `root_cause`：底层根因
- `mechanism`：根因产生影响的机制
- `impact`：最终影响
- `evidence`：支撑结论的关键证据
- `action`：建议的排查、修复或后续动作

要求：

- 每个 Claim 只表达一个结论
- 根因和直接触发原因必须分开
- Claim 必须能够追溯到标准答案原文
- 不将完整分析段落直接作为一个 Claim

## 5. 因果关系

Causal Edge 表示两个 Claim 之间的关系。

```json
{
  "id": "edge-1",
  "from": "claim-1",
  "to": "claim-2",
  "relation": "causes",
  "weight": 10
}
```

第一版只需要支持：

- `causes`
- `leads_to`
- `explains`

因果关系必须由用户确认，自动生成结果不能直接用于正式 Benchmark。

## 6. 已知错误结论

Forbidden Claim 用于记录常见误判或与标准答案冲突的结论。

```json
{
  "id": "forbidden-1",
  "statement": "常见但与标准答案冲突的错误结论",
  "severity": "high",
  "penalty": 15
}
```

该字段不是强制要求用户穷举所有错误答案。

## 7. 生成流程

```text
人工标准答案
→ 原子 Claim 抽取
→ Claim 类型分类
→ 根因与直接触发原因分离
→ 因果关系生成
→ 建议权重生成
→ Schema 校验
→ 用户编辑确认
→ 冻结 Eval Spec 版本
```

## 8. 实现边界

LLM 或 Skill 负责：

- 理解自然语言
- 拆解 Claim
- 判断 Claim 类型
- 生成因果关系草稿
- 推荐权重

平台代码负责：

- JSON Schema 校验
- Claim ID 唯一性
- 权重检查
- 因果边引用检查
- 用户编辑
- 版本管理
- 正式版本冻结

## 9. 质量要求

生成器必须：

- 保留标准答案中的关键结论
- 不增加标准答案没有表达的新结论
- 区分“确认”“怀疑”和“可能”
- 为每个 Claim 提供标准答案原文引用
- 在无法确认时标记为待人工确认


## 10. 领域模板

Eval Spec Generator 采用“通用结构 + 领域模板”设计。

第一阶段建议提供：

- `generic-analysis`：通用问题分析
- `incident-rca`：生产事故和故障根因分析
- `code-analysis`：代码缺陷和设计问题分析
- `security-analysis`：安全问题分析
- `kernel-diagnosis`：内核问题分析

领域模板只定义推荐 Claim 类型、默认权重和生成提示，不修改 AnalystBench Core 的数据结构。
