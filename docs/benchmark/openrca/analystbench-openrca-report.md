# OpenRCA 调研与 AnalystBench 可复用点

> 结论修订版，基于本地 OpenRCA 代码与 AnalystBench 当前设计。更新时间：2026-08-10。

## 1. 结论先行

OpenRCA 更适合作为公开 RCA 数据与对照实现，而不是 AnalystBench 的架构来源。

当前没有必要引入 Controller/Executor、OpenRCA 轨迹格式、难度分层或 Best-of-N。OpenRCA 的 Agent 循环、轨迹保存和字段评分，在 AnalystBench 当前体系中要么已经具备，要么不适合复用。

唯一值得推进的启发是：让 Harness 可选接入项目的问题分类与责任组件目录，并把目录访问条件作为正式评测元数据，通过冻结快照保证比较口径清晰。

| 决策 | 内容 |
| --- | --- |
| 不复用 | Controller/Executor、内嵌 IPython 执行、精确字符串评测、难度规则、Best-of-N |
| 可作为外部材料 | OpenRCA 数据集、任务样例和现有基线，可用于未来外部验证或研究对照 |
| 唯一建议落地 | 问题分类与责任组件目录的可选接入、版本冻结及可比较性标记 |

## 2. OpenRCA 当前做法

### 2.1 Agent 运行：分析者下指令，执行者生成并运行 Python

OpenRCA 的循环是：

1. Controller 读取问题和上一步 Observation，输出 `analysis`、`completed`、`instruction`。
2. Executor 把 instruction 转成 Python，在同一个有状态 IPython Kernel 中执行并返回结果。
3. Controller 继续分析结果，循环直至完成或达到最大步数，再输出最终根因 JSON。

这里的“Executor”不是执行修复动作，而是执行数据分析代码，本质上是 OpenRCA 自带的工具调用层。AnalystBench 已有自己的 Harness、工具调用和循环测试，因此没有必要再引入这一层职责拆分。

### 2.2 轨迹：采集并保存，但没有自动分析

OpenRCA 把每一步生成的代码和结果写入 `.ipynb`，同时保存完整 Prompt JSON 和运行日志。

代码中没有发现把这些轨迹进一步转换成“错误发生在哪一步、工具选择是否合理、证据链是否充分”等诊断结论的评估器。因此 OpenRCA 提供的是 trace archive，不是 trace evaluation。

既然 AnalystBench 已经采集轨迹，照搬这里只会多一种保存格式，不会增加分析能力。

### 2.3 单故障评分：三个字段逐项命中

对单故障任务，评测器从最终答案中解析最多三个字段，再逐项计算：

| 字段 | 判定方式 | 含义 |
| --- | --- | --- |
| 根因组件 | 预测字符串 `==` 标准字符串 | 不做别名、层级或语义映射 |
| 根因原因 | 预测字符串 `==` 标准字符串 | “OOM”和“内存泄漏”不会因为语义接近而自动匹配 |
| 发生时间 | 时间差不超过 60 秒 | 只有时间字段有显式容差 |
| 总分 | 命中字段数 ÷ 被要求字段数 | 例如要求 3 项，命中 2 项得约 0.67 |

组件和原因能采用精确匹配，是因为 Prompt 强制模型从预先给定的候选列表中选择。它把开放语义问题改造成了闭集标签选择。这种方式适合固定数据集，不适合直接替代 AnalystBench 的开放报告语义评测。

### 2.4 其他机制

- 多个故障：评测器遍历预测根因的排列，寻找与标准答案顺序最匹配的组合。当前不考虑多故障答案，因此无需借鉴。
- 难度：按任务编号硬编码 easy / middle / hard。当前不需要难度分层。
- 重复采样：同一任务可运行多次并保留最高分。当前不需要 Best-of-N。

## 3. 对 AnalystBench 的复用判断

| OpenRCA 能力 | 当前判断 | 原因 |
| --- | --- | --- |
| Controller / Executor | 不接入 | 当前 Harness 已承担工具编排、代码或命令执行与循环测试 |
| 运行轨迹保存 | 不接入 | 轨迹采集已经具备；OpenRCA 没有后续轨迹分析器 |
| 精确字段评分 | 不接入 | 依赖闭集候选与标准字符串，不覆盖开放报告中的语义等价 |
| 多故障排列匹配 | 不接入 | 当前范围只评一个故障 |
| 难度分层 | 不接入 | 按任务编号划分，且当前不需要难度维度 |
| Best-of-N | 不接入 | 会改变评测成本和统计解释，当前不需要 |
| 领域候选目录 | 保留启发 | 可降低术语漂移，但必须显式记录 Harness 获得了什么信息 |
| 公开数据与任务 | 以后可用 | 可做外部验证或横向研究，不要求迁移其 Agent 实现 |

## 4. 唯一建议落地的优化

### 4.1 把分类与组件目录定义为 Harness 能力

Harness 可以选择访问项目的问题分类、责任组件及必要的别名映射，用于约束调查方向和规范最终术语。

目录不直接替模型作答，也不把“未接入目录”的结果强行映射成同一信息条件。它是 Harness 获得的一项能力，而不是评分器里的补丁。

### 4.2 给目录访问条件建模

| 模式 | 定义 | 适用场景 |
| --- | --- | --- |
| `none` | Harness 不获得分类或责任组件目录 | 测量开放环境下的自主分析能力 |
| `frozen_snapshot` | 评测绑定不可变目录快照，并记录版本与内容哈希 | 正式受控比较，推荐默认使用 |
| `harness_managed` | 目录由 Harness 自带或在其内部维护 | 比较整体 Harness 方案，但无法单独归因到推理能力 |
| `live` | 运行时读取会变化的线上目录 | 贴近生产验证；不适合严格复现的排行榜比较 |

### 4.3 明确可比较性

- 同一冻结快照：信息条件一致，适合比较 Harness 或 Model 的表现。
- 目录访问条件不同：仍可比较整体系统效果，但不能把差异解释为纯模型推理能力差异。
- 目录内容不同：必须展示版本、哈希与来源，避免“看起来同名，实际知识不同”。

### 4.4 最小验证实验

固定同一 Case、Harness、Model、Prompt 和运行预算，只改变领域目录条件：

1. 无目录；
2. 仅问题分类目录；
3. 问题分类 + 责任组件目录。

比较最终报告得分、失败类型、耗时与工具调用成本。若第 3 组稳定提升，且没有通过答案泄漏直接暴露标准结论，再决定是否产品化。

## 5. 最终决策

当前不规划 OpenRCA 核心能力迁移。

OpenRCA 保留为外部基准与数据来源。AnalystBench 的实际优化项独立表述为“领域目录能力与可比较性控制”，不再包装成 Controller/Executor、轨迹分析或 OpenRCA 评分复用。

## 6. 代码与设计依据

- `/home/jiqi/LLM/OpenRCA/rca/baseline/rca_agent/controller.py`
- `/home/jiqi/LLM/OpenRCA/rca/baseline/rca_agent/executor.py`
- `/home/jiqi/LLM/OpenRCA/rca/run_agent_standard.py`
- `/home/jiqi/LLM/OpenRCA/main/evaluate.py`
- `/home/jiqi/LLM/OpenRCA/main/task_specification.json`
- `/home/jiqi/LLM/AnalystBench/docs/development/harness-model-evaluation-design.md`
- `/home/jiqi/LLM/AnalystBench/docs/development/architecture.md`
