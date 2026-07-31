# P11 Case 基准库与多报告批量评测设计

状态：Accepted（2026-07-22）

## 目标

把原来混合在 `analystbench score case.json report.json...` 中的 Case 审核、Case 发布和报告评分拆开。标准答案只审核一次；发布后的 Case 可以反复评测任意数量的 AI 报告。

## 用户流程

### Case

1. 用户通过 claude/OpenCode Skill，或按 `docs/scoring-input.md`，把人工标准答案转换成 Case JSON。
2. 用户执行 `analystbench case-import case.json`，或由前端调用 Case Draft API。
3. 后端检查字段。只有确实有歧义或格式错误的字段才逐项询问，并在问题中展示 Claim ID、结论和字段含义。
4. 字段全部有效后，用户只做一次整体确认。
5. 后端发布不可变 Case，返回稳定的 `case_key`。后续评测不再确认 Case。

### 报告

1. 每份 AI 报告直接保留为原始文本文件，不要求转换为 Report JSON。
2. 用户执行 `analystbench evaluate <case_key> report1.md report2.txt ...`，或由前端把原文放入 `raw_reports` 创建 Evaluation Batch。
3. 后端把每份报告分别评分，并以第一份报告为基线自动生成对比结果。
4. 语义 Judge 直接读取完整报告；日志关键字也在完整原文中强匹配，不需要预先提取 `claim_hints`。

## 后端对象

- `CaseDraft`：保存待审核 Case JSON、字段问题、审核记录和发布后的资源引用。
- `ReportDraft`：内部保存原始报告及可选元数据；用户无需手动创建。
- `EvaluationBatch`：绑定一个已发布 Case 与多份 Report Draft，保存各自 Run 和自动对比结果。

Skill、CLI 和未来前端使用同一服务与 REST API，不在 Skill 中复制发布或评分逻辑。

## API 契约

- `POST /api/v1/case-drafts`：载入 Case JSON 并预检。
- `POST /api/v1/case-drafts:generate`：提交标准答案原文，选择 claude 或 OpenCode 后台生成 Case Draft。
- `GET /api/v1/case-drafts/{id}`：读取审核状态和待确认字段。
- `POST /api/v1/case-drafts/{id}/answers`：提交字段答案或整体确认。
- `POST /api/v1/case-drafts/{id}:publish`：发布已确认 Case。
- `GET /api/v1/benchmark-cases`：列出已发布 Case；前端以 `case_key` 展示和选择。
- `POST /api/v1/report-drafts`：载入一份报告 JSON 并预检。
- `POST /api/v1/report-drafts:convert`：将报告原文与候选名称确定性包装成 Report Draft，无需调用模型。
- `POST /api/v1/evaluation-batches`：以 `case_key` 和 `raw_reports` 创建多报告后台评测；仍兼容已有 Report JSON。
- `GET /api/v1/evaluation-batches/{id}`：读取批次状态。
- `GET /api/v1/evaluation-batches/{id}/result`：读取各报告分数和自动对比。

## 状态与后台任务

Case Draft 状态为 `generating`、`needs_confirmation`、`ready`、`published` 或 `failed`。原始标准答案转换使用本地 Worker 的持久化任务，可选择 `claude -p` 或 `opencode run`。Evaluation Batch 创建后为 `queued`，沿用持久化 Benchmark Case Run 任务，最终进入 `completed` 或 `failed`。CLI 可同步处理当前批次，前端则轮询状态。

## 兼容性

旧的 `analystbench score` 和 Evaluation Session API 暂时保留，作为兼容入口；新文档和 Skill 默认使用 `case-import` 与 `evaluate`。

## 验收条件

- 合法 Case JSON 只需一次整体确认即可发布。
- 有问题时，问题明确包含字段路径、Claim 内容、当前值和可选值。
- 同一已发布 Case 可在不重复确认的情况下评测两份以上报告。
- 结果按报告分别给分，并自动给出相对第一份报告的差值。
- API 和 CLI 共用相同后端服务；API 评分使用本地后台 Worker。
