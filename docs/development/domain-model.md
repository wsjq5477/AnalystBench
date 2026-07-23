# 领域模型与版本规则

状态：Accepted（P0.1 Agent Execution 范围修订，2026-07-21）

## 核心对象

| 对象 | 作用 | 可变性 |
|---|---|---|
| Dataset | Case 的逻辑集合 | 名称、描述可修改 |
| Case | 问题描述、领域、标签的逻辑身份 | 元数据可修改 |
| Case Revision | 问题材料与标准答案的内容快照 | 创建后不可变 |
| Dataset Version | Case Revision 有序集合的快照 | 冻结后不可变 |
| Candidate | 被评测 Agent/模型/Prompt/Skill 的逻辑身份 | 元数据可修改 |
| Candidate Version | Candidate 的具体版本与配置 | 创建后不可变 |
| Candidate Report | 某 Candidate Version 对某 Case Revision 的输出 | 创建后不可变 |
| Agent Execution Profile | Runner 类型、Prompt、权限、超时与非敏感配置 | 版本化且不可变 |
| Candidate Generation Run | 对一个 Dataset Version 批量生成报告的 Manifest | 启动后输入不可变 |
| Agent Case Run | 单个 Agent/Case 子进程执行和原始产物 | 状态推进，完成后不可变 |
| Eval Spec | 某 Case 的评分标准逻辑身份 | 草稿容器 |
| Eval Spec Version | 已校验的具体评分标准 | 冻结后不可变 |
| Scoring Policy Version | 系数、门禁和阈值 | 冻结后不可变 |
| Model Profile | 模型端点的非敏感配置 | 可创建新版本 |
| Prompt Version | 生成、抽取或 Judge Prompt | 不可变 |
| Benchmark Run | 一次批量评测及其 Manifest | 启动后输入不可变 |
| Case Run | Run 中一个 Case 的执行与结果 | 状态推进，完成后结果不可变 |
| Experiment Comparison | 两个 Run 的派生比较 | 可重算，不是事实源 |

## 身份与版本

- 所有实体使用 UUID；面向人的版本号使用单调递增整数。
- 不可变对象同时保存 canonical JSON 的 SHA-256 content_hash。
- Dataset Version 保存 Case Revision ID 集合，而不是动态查询当前 Case。
- Candidate Version 与 Candidate Report 分离，便于同一版本逐步补齐报告。
- Candidate Report 记录来源为 imported 或 agent_run；agent_run 来源必须引用成功的 Agent Case Run。
- Candidate Generation Run 与 Benchmark Run 分离：先冻结 Candidate Report，再独立评分，避免 Agent 波动与 Judge 波动混在一次 Run 中。
- Eval Spec Version 必须精确绑定 Case Revision；标准答案变化后不能继续沿用旧版本。
- Benchmark Run 启动时冻结 Manifest，后续对象即使产生新版本也不影响该 Run。

## Candidate 覆盖规则

MVP 允许 Candidate Version 缺少部分 Case Report，但正式 Run 默认使用 strict 模式。创建 Run 时预检覆盖率：

- strict 模式：缺任一报告则拒绝启动。
- partial 模式：为缺失报告创建 skipped Case Run，汇总同时展示总 Case 数、已评测数和覆盖率。

partial 模式必须由调用方显式指定，Run Manifest 记录实际模式。

## 删除与引用

- 被冻结版本或 Run 引用的对象不可物理删除，只能归档。
- 未被引用的草稿可删除。
- Content Store 对象按引用计数清理；MVP 不自动执行物理清理命令。

## Run Manifest

至少记录：

- Dataset Version 及每个 Case Revision 的 ID/hash。
- Candidate Version 及每个 Candidate Report 的 ID/hash。
- 若报告由 Agent 生成，记录 Candidate Generation Run、Execution Profile、Agent Case Run、CLI/Adapter 版本和原始事件哈希。
- 每个 Case 对应的 Eval Spec Version ID/hash。
- Scoring Policy Version ID/hash。
- Generator、Extractor、Judge 的 Model Profile、Prompt Version 和参数摘要。
- Suite ID/version、Core 版本、Schema 版本和随机种子（如使用）。
- 创建时间、启动者和运行选项。

## 可比较性

两个 Run 只有在 Dataset Version、Eval Spec Versions、Scoring Policy、Extractor、Judge、Prompt、Suite 和 Core 版本一致时才属于直接 A/B。Candidate Version 可以不同。否则仍可展示结果，但必须标记为非受控比较并列出差异。
