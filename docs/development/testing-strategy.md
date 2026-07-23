# 测试策略

状态：Accepted（P0.1 Agent Execution 范围修订，2026-07-21）

## 测试层次

1. Core 单元测试：Schema 语义校验、引用验证、图约束、评分公式、惩罚和门禁。
2. 属性测试：得分范围、确定性、排序稳定、增加正向命中不降低正向分、惩罚不提高总分。
3. Repository 测试：迁移、事务、不可变性、版本快照、持久化后台任务、幂等写入和 Worker 租约。
4. LLM 契约测试：使用固定 Fake Adapter 覆盖合法输出、Schema 错误、伪造引用、超时、限流和修复失败。
5. Agent Runner 测试：使用不访问网络的 Fake CLI 子进程覆盖 argv、工作区隔离、JSON 事件、取消、超时、输出上限、进程树清理和最终报告提取。
6. API 契约测试：状态码、错误码、ETag、分页、幂等键和 OpenAPI Schema。
7. CLI 测试：参数、JSON 输出、退出码和完整导入/执行/评分/导出流程。
8. 端到端测试：从空 SQLite 数据库到两个 Candidate 的直接 A/B 对比。

## 固定样例

仓库维护不依赖网络和真实模型的 golden fixtures：

- 完全匹配、部分匹配、遗漏和冲突。
- 完整、缺失、反向和冲突因果边。
- Forbidden Claim、critical contradiction、分数封顶和直接失败。
- Candidate 确定程度对分数的影响。
- 缺报告、模型失败、缓存命中和重试。
- Claude Code/OpenCode Fake Runner 的成功、认证错误、非零退出、无最终报告、Worker 中断和取消。
- 可直接比较与非受控比较。

每个 fixture 保存输入、结构化模型产物和预期中间/最终结果。变更预期值必须在评审中说明评分规则变化，禁止无说明批量更新快照。

## 真实模型测试

真实模型测试标记为 integration-llm，默认测试命令不运行，且必须显式配置凭据。它只验证适配器兼容性和输出合规率，不作为确定性评分正确性的唯一证据。

## 阶段质量门禁

- P1：配置、迁移、健康检查和最小 Repository 测试通过。
- P2：领域模型、冻结、快照、内容哈希和导入导出测试通过。
- P3：两个 Fake CLI 的后台执行、隔离、取消、产物和恢复测试通过。
- P4：Eval Spec 全部 Schema/引用/图约束测试通过。
- P5：评分 golden fixtures 和属性测试通过，Core 不触网。
- P6：Worker 重试、幂等、恢复、缓存和部分失败测试通过。
- P7：A/B 可比较性与汇总测试通过。
- P8/P9：Suite 兼容性、CLI E2E 和容器 smoke test 通过。

## 建议基线

核心领域层分支覆盖率不低于 90%，全项目不低于 80%。覆盖率只是门禁之一；评分 golden fixtures、数据库迁移测试和完整 CLI E2E 必须独立通过。
