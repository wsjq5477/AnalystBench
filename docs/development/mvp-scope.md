# Phase 1 MVP 范围

状态：Accepted（P0.1 Agent Execution 范围修订，2026-07-21）

## 目标

提供一个本地优先、API/CLI 驱动的开放式分析报告 Benchmark 后端，使同一组 Case、Eval Spec 和评分策略可以稳定评估多个 Candidate 版本，并解释每个得分来源。Candidate Report 既可导入，也可由 Claude Code 或 OpenCode 在本地后台生成。

## 必须完成的用户闭环

1. 创建 Dataset，并添加 Case、问题材料和人工标准答案。
2. 为 Dataset 导入 Candidate Report，或选择 Claude Code/OpenCode Execution Profile 后台生成报告。
3. 从标准答案生成 Eval Spec 草稿，人工编辑、校验并冻结版本。
4. 选择 Dataset Version、Candidate Version、Eval Spec Version、模型配置和评分策略，启动 Benchmark Run。
5. 查看每个 Case 的 Claim 对齐、因果边对齐、遗漏、冲突、引用和确定性得分。
6. 查看 Run 汇总，并对两个可比较的 Candidate 版本进行 A/B 对比。

## MVP 功能

- Dataset、Case、Candidate、Eval Spec、Scoring Policy、Benchmark Run 的本地持久化与版本化。
- `claude -p` 与 `opencode run` Agent Runner，通过持久化后台任务批量生成 Candidate Report。
- Agent CLI 版本、模型、Prompt、工作区、权限、超时、退出状态和原始事件的审计记录。
- 文本类问题材料、标准答案和 Candidate Report 的 JSON/CLI/API 导入导出。
- Eval Spec 草稿生成、确定性校验、人工修订接口和冻结。
- 完整报告语义关系判断，以及分析链日志关键字的确定性强匹配。
- 由纯代码执行的权重聚合、冲突惩罚、门禁和通过判定。
- 模型/Prompt/策略/输入版本完整记录，抽取产物缓存并冻结。
- Benchmark 批量执行、失败重试、部分失败展示和 A/B 对比。
- 通用 Suite 与最小 KDiag Suite 示例。
- OpenAPI 文档、CLI 和本地 Python 自托管方式。

## 明确非目标

- Web 前端。
- Claude Code/OpenCode 之外的通用 Agent 托管与复杂编排。
- Trace、OTLP、工具调用和中间步骤评测。
- 自动修改、发布 Agent、Prompt 或 Skill。
- 多租户、RBAC、SSO 和公网 SaaS 运维能力。
- 分布式 Worker、Redis/Kafka 等基础设施。
- 音视频、Office、PDF 的内容解析；MVP 只保证 UTF-8 文本材料。
- Embedding 向量数据库；MVP 允许使用可替换的轻量检索实现。

## 完成定义

- 固定测试数据可从空数据库通过 CLI 完成完整闭环。
- 相同冻结输入重复执行确定性计分得到逐字段一致的结果。
- 每个非 missing 的对齐结果均可追溯到 Gold 与完整 AI 报告中的连续原文区间。
- Run Manifest 足以判断两个 Run 是否可直接比较。
- 单 Case 模型失败不会破坏其他 Case，Run 能给出明确的部分失败状态。
- Claude Code/OpenCode 执行必须发生在独立临时工作区，不能修改原始 Dataset/Case 内容。
- Agent 执行失败不会产生伪 Candidate Report，成功报告可以被冻结并重复用于评分。
- 核心计算、Schema 校验、版本冻结、API 契约和 CLI 闭环都有自动化测试。
