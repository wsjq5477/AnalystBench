# AnalystBench 开发设计文档

状态：Accepted（P0.1 Agent Execution 范围修订，2026-07-21）

本目录把产品概念转成可以实现、测试和验收的工程契约。当前覆盖无前端的 Phase 1 MVP。P0 基线和 P0.1 Agent Execution 策略已经确认，可作为实现依据。

## 文档地图

| 文档 | 说明 |
|---|---|
| mvp-scope.md | 第一阶段范围、非目标和完成定义 |
| architecture.md | 后端组件、依赖方向和部署边界 |
| domain-model.md | 聚合、实体、版本和不可变性规则 |
| eval-spec-schema.md | Eval Spec v1 的结构与校验规则 |
| scoring-spec.md | Claim、因果边、惩罚、门禁和总分算法 |
| scoring-engine-design.md | 评分引擎对齐草稿、语义判定与计分流程 |
| llm-contracts.md | 生成器、抽取器和 Judge 的结构化契约 |
| eval-spec-generator-design.md | 自然语言标准答案转 Eval Spec 功能设计 |
| api-design.md | REST API 与错误模型草案 |
| run-lifecycle.md | Benchmark Run 状态机、缓存和失败语义 |
| agent-runner-design.md | Claude Code/OpenCode 执行、隔离和产物契约 |
| suite-extension.md | Benchmark Suite 扩展点 |
| testing-strategy.md | 分层测试和固定样例策略 |
| case-library-batch-evaluation-design.md | Case 一次审核发布与多报告批量评测契约 |
| case-storage-hierarchy-design.md | 测试集、问题分类、Case 与 Trace 分层存储契约 |
| evaluation-submission-design.md | 从测试集原始日志提交多个报告生成器并自动评分的目录、隔离、状态与 API 契约 |
| frontend-overview.md | 第一阶段前端功能说明 |
| release-checklist.md | MVP 发布检查清单 |
| todolist.md | 阶段任务、依赖和验收门禁 |
| p0-decisions.md | P0 已确认决策记录（历史参考） |
| p0.1-decisions.md | P0.1 Agent Execution Lite 决策记录（历史参考） |

## 阶段门禁

1. P0 文档评审：确认 p0-decisions.md 后，将相关文档改为 Accepted。
2. P0.1 范围修订：确认 p0.1-decisions.md 后，将 Agent Execution 相关文档改为 Accepted。
3. P1 工程骨架：仅实现运行框架、配置、持久化基础和测试框架。
4. P2 及以后：每一阶段开始前确认上一阶段验收结果；遇到改变契约的问题先更新文档并确认。

## 文档状态

- Draft：仍可能因决策改变。
- Accepted：已确认，可作为开发依据。
- Superseded：被新版本替代，保留用于追溯。
