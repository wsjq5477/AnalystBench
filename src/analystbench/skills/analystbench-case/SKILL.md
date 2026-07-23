---
name: analystbench-case
description: 将一份人工标准答案转换为 AnalystBench Case JSON，或把已有 Case JSON 交互式审核并发布到本地基准库。适用于用户说"生成 Case""导入标准答案""审核 Case""加入 benchmark 库"等场景；不需要 AI 报告。
---

# AnalystBench Case 生成与入库

标准答案转换和 Case 入库是两个独立阶段。只处理 Case，不索取 AI 报告，也不在 Case 发布时进行报告评分。

## 阶段一：生成 Case JSON

当用户提供人工标准答案原文或文件、但还没有 Case JSON 时：

1. 读取 AnalystBench 项目根目录的 `docs/scoring-input.md`。
2. 按其中"人工标准答案 Case"格式生成 JSON，顶层键只能是 `case` 和 `eval_spec_draft`。
3. 原样保留 `reference_answer`；每个 Claim 的 `quote` 必须是其中的连续原文。
4. 当答案是"问题分类 + 问题根因 + 证据N/结论N"时，只生成一个 `critical root_cause`、一个 `classification` 和每组一个 `analysis_chain`；不生成"直接原因"。
5. 根因权重固定100，分类权重固定20；所有分析链等分且合计60。每条分析链必须写 `evidence_keyword`（证据原文）和 `conclusion`（结论原文）；`causal_edges=[]`；使用 `root_category_chain` 评分策略。
6. 根因 ID 固定为 `root`，问题分类固定为 `category`，分析链依次使用 `chain-1`、`chain-2`；其他通用评分项使用 `claim-1`、`claim-2`。禁止旧的 `g1`、`g2` 格式。
7. 不确定信息写入 `unresolved_items`，不能替用户猜测。
8. 用户只要求转换时，输出或按用户指定路径保存 JSON 后停止，不自动入库。
9. `case.case_key` 为纯数字用例编号（如 `1`），由脚本或用户传入，不由 AI 生成。输出文件名格式为 `<测试集>-<问题类型>-<编号>.json`（如 `kdiag-SYSMGR_PANIC-1.json`）。
10. 确认测试集、问题类型和用例编号，分别写入 `case.test_set`（测试集标识，如 `kdiag`）、`case.category`（问题类型，如 `SYSMGR_PANIC`）、`case.case_key`（编号）。这三项由脚本生成或用户填入。
11. 生成的 Case JSON 中**不得包含** `domain` 和 `tags` 字段。

## 阶段二：审核并发布

当用户明确要求把某个 Case JSON 加入本地基准库时，在项目根目录执行：

```bash
.venv/bin/analystbench db-upgrade
.venv/bin/analystbench case-draft-create <case.json> \
  --test-set <测试集key> \
  --test-set-name <测试集名称> \
  --category <分类key> \
  --category-name <分类名称>
```

读取返回的 `questions`：

- 每次只向用户展示第一项的字段路径、问题、当前值、建议值和可选值。
- 字段问题必须由用户回答；不得直接编辑原 JSON 冒充用户确认。
- `approve_case` 是一次整体审核，必须把后端给出的评分项数量和关键根因展示给用户，等用户明确同意。

提交答案：

```bash
.venv/bin/analystbench case-draft-answer <draft-id> <question-id> '<JSON值>'
```

重复到状态为 `ready`，再执行：

```bash
.venv/bin/analystbench case-draft-publish <draft-id>
```

最终只向用户报告 `case_key`、测试集、分类、版本和发布状态。内部 ID 不要求用户理解或保存。

## 交互规则

- JSON 合法时只进行一次整体确认，不逐个确认 `review_required`。
- 后端指出字段错误时，原样展示 Claim 上下文和字段含义。
- 后端返回 `failed` 时，原样说明 `error.code` 和 `error.message`，不猜测修复值。
- `case_key` 为纯数字用例编号，不由 AI 自行创造语义化名称。
- 发布前必须确认项目标准化后的评分项数量与原始结构一致。
