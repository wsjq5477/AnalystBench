# Eval Spec v1 Schema 设计

状态：Accepted（P0，2026-07-21）

## 顶层结构

Eval Spec Version 是与单个 Case Revision 绑定的不可变评分标准。建议顶层字段如下：

    {
      "schema_version": "1.0",
      "case_revision_id": "uuid",
      "suite": {"id": "generic-analysis", "version": "1.0.0"},
      "claims": [],
      "causal_edges": [],
      "forbidden_claims": [],
      "scoring_policy_version_id": "uuid",
      "review": {"status": "draft", "unresolved_items": []}
    }

冻结时 review.status 必须为 approved，且 unresolved_items 为空。

## Gold Claim

    {
      "id": "root",
      "type": "root_cause",
      "statement": "底层根因的原子化描述",
      "importance": "critical",
      "weight": 30,
      "source_ref": {
        "content_hash": "sha256:...",
        "start": 120,
        "end": 158,
        "quote": "标准答案中的逐字原文"
      },
      "review_required": false,
      "notes": null
    }

约束：

- id 在当前 Spec 内唯一：根因固定为 `root`，证据链为 `chain-N`，其他通用评分项为 `claim-N`。
- statement 去除首尾空白后非空，表达单一结论。
- type 使用 Core 类型或当前 Suite 注册的扩展类型。
- importance 为 critical、high、normal、low 之一。
- weight 为正整数。
- source_ref 的内容哈希必须等于绑定的标准答案，start/end 使用 Unicode code point 下标。
- quote 必须逐字等于原文区间；冻结时不允许仅靠模糊匹配修复。
- review_required 为 true 的 Claim 不允许冻结。

Core 类型为 trigger、symptom、localization、root_cause、mechanism、impact、evidence、action。

## Causal Edge

    {
      "id": "edge-1",
      "from": "root",
      "to": "claim-1",
      "relation": "causes",
      "weight": 10,
      "review_required": false
    }

约束：

- id 唯一，from 和 to 必须引用现有且不同的 Claim。
- relation v1 仅支持 causes、leads_to、explains。
- 相同 from、to、relation 不可重复。
- 默认禁止有向环；若后续领域需要反馈环，应在新 Schema 版本引入。
- 所有自动生成的边必须经人工确认后才能冻结。

## Forbidden Claim

    {
      "id": "forbidden-1",
      "statement": "常见但错误的结论",
      "severity": "high",
      "penalty": 15,
      "failure_gate": false,
      "notes": null
    }

severity 为 critical、high、medium、low。penalty 为非负整数；failure_gate 表示命中后是否直接失败。

## 冻结校验

冻结必须同时满足：

1. JSON Schema 和所有语义约束通过。
2. 至少存在一个 Claim，至少一个 Claim 为 critical。
3. Claim 与 Edge 的正向权重满足评分策略的归一化规则。
4. 所有 source_ref 逐字校验通过。
5. 不存在 review_required 项和未解决问题。
6. Scoring Policy、Suite 和 Case Revision 均存在且可用。
7. canonical JSON 可稳定序列化并生成 content_hash。

## 演进规则

- 新增可选字段可保持 1.x；删除字段、改变含义或枚举兼容性使用新的主版本。
- 读取端必须拒绝未知主版本，不能静默忽略。
- 已冻结版本不自动迁移；迁移会创建新的 Eval Spec Version 并保留来源关系。
