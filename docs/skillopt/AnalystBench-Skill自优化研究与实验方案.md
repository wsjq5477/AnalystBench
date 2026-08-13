# AnalystBench 面向内核日志分析的 Skill 自优化方法研究与实验方案

> 文档类型：研究论文 / 技术白皮书
> 文档状态：V1 本地代码已实现，真实私有实验结果待补充
> 版本：v1.2
> 日期：2026-08-12
> 适用系统：AnalystBench、内核日志分析 Agent、claude Skill Harness
> 文献口径：截至 2026-07-31，以引用论文的最新公开版本为准

配套工程规格：[AnalystBench Skill 自优化系统方案设计](../development/AnalystBench-Skill自优化系统方案设计-Codex.md)。

---

## 摘要

Agent Skill 通过可移植的指令、脚本、参考资料和输出约束，为冻结的大语言模型提供面向特定任务的过程性知识。与模型微调相比，Skill 具有成本低、可审计、易版本化和跨模型迁移等优势；但现有 Skill 多依赖人工编写或一次性生成，缺少基于真实执行结果的持续优化机制。SkillsBench v4 在 87 个任务、8 个领域和 18 个 Model–Harness 配置上的配对实验表明，人工整理的 Skill 将平均通过率从 33.9% 提高到 50.5%，即提升 16.6 个百分点；同时仍有 13 个任务出现负向变化。在三个专用 Harness 配置上，一次性自生成 Skill 相对无 Skill 基线分别下降 8.1、11.3 和 11.5 个百分点。这说明“存在 Skill”不等价于“Skill 有效”，Skill 的构建、验证、裁剪和演进必须进入受控评估闭环。

本文调研 SkillOpt、Trace2Skill、CoEvoSkills（其 arXiv 条目也使用 EvoSkills 名称）、SkillMOO、MUSE-Autoskill、SkillOS、SkillHone、MetaSkill-Evolve 等 2026 年 Skill 自优化工作，并结合 AnalystBench 当前已具备的 Harness、Model、EvaluationTarget、Benchmark、Eval Spec 和独立评分能力，提出一种面向内核日志分析的 Skill Training Engine。该方案将完整 Skill 包视为不可变、可训练的外部状态，通过冻结评测环境、批量结果归纳、结构化失败信号、受限文本修改、多候选搜索、分级验证、配对门禁、失败编辑记忆和原子发布，实现 Skill 的自动优化、回归控制和持续维护。

本文同时给出实验问题、基线、数据切分、评价指标、消融实验和结果表格模板。
当前仓库已具备本地代码闭环，但真实 claude、用户私有 Case、成本和 Holdout 结果必须
由用户在私有环境据实运行后再填入；确定性替身测试不能代替这部分证据。

**关键词：** Agent Skill；SkillOpt；自优化 Agent；内核日志分析；文本空间优化；LLM-as-a-Judge；Benchmark；持续学习

### 研究贡献与结论边界

本文的预期贡献不是提出一种未经验证的新通用优化算法，而是：

1. 将受控文本优化、完整 Skill 包版本、开放式报告评分和工程发布门禁组合成可复现系统；
2. 为内核日志分析建立 Claim、Evidence、因果链和执行稳定性的结构化优化信号；
3. 给出适用于非确定性 Agent 输出的配对评测、回归保护和小样本治理方法；
4. 通过真实 AnalystBench 实验检验该组合是否优于人工 Skill、一次性重写和单候选优化。

在实验完成前，文中的“预期提升”“研究假设”和默认阈值均是待验证设计，不应写成已证实结论。本文引用的 2026 年工作多数仍是预印本，后续正式成文时必须记录所用论文版本和访问日期。

---

## 1. 引言

大语言模型 Agent 已逐步从单轮问答扩展到具备文件操作、命令执行、工具调用和多步骤推理能力的任务执行系统。在内核日志分析场景中，通用模型虽然具备一定的 Linux 和操作系统知识，但通常无法稳定遵循企业内部的故障分类体系、证据引用规范、根因判定边界和标准报告格式。因此，实际系统往往通过领域 Prompt、知识库、规则和 Skill 将专家经验注入 Agent。

Skill 与普通 Prompt 的主要区别在于：Skill 不是单条临时指令，而是面向一类任务的可复用过程性资产，可以包含 `SKILL.md`、脚本、参考资料、模板和测试。SkillsBench 将 Skill 定义为具有过程性内容、适用于任务类别、包含结构化组件且可在文件系统中移植的 Agent 资产。该定义非常适合 AnalystBench 当前的内核日志分析 Skill：它既包含分析流程，又可能包含日志解析脚本、错误码表、定位手册和输出 Schema。

然而，现有 Skill 研发通常采用以下方式：领域专家手工编写；发现单个案例失败后追加规则；让模型根据少量失败样例一次性重写；修改后仅抽查少量案例；直接覆盖当前生效目录。这些方式容易造成案例过拟合、规则膨胀、成功能力被破坏、跨模型退化和历史结果不可复现。

本文研究目标是回答以下问题：

1. 如何利用 AnalystBench 已有执行轨迹和评分结果自动改进 Skill？
2. 如何防止优化 Agent 针对具体日志或标准答案进行硬编码？
3. 如何在模型输出具有随机性的情况下可靠判定候选 Skill 是否提升？
4. 如何同时优化质量、稳定性、耗时和成本，而不是只追求总平均分？
5. 如何让优化过程可复现、可审计、可回滚，并可被后续 Agent 持续继承？

---

## 2. 背景与问题定义

### 2.1 Agent Skill

本文将 Skill 定义为面向一类 Agent 任务的可移植过程性资产包：

```text
skill-package/
├── SKILL.md
├── references/
├── scripts/
├── tests/
└── manifest.json
```

其中 `SKILL.md` 描述流程、规则和输出约束；`references/` 保存定位手册和领域知识；`scripts/` 提供解析、预处理和校验；`tests/` 验证包内脚本；`manifest.json` 保存入口、权限、限制和内容哈希。

Skill 不等同于系统 Prompt，也不等同于知识库。知识库主要提供事实性内容，Skill 主要规定 Agent 应如何执行任务、如何使用证据以及如何形成结论。

### 2.2 Skill 优化问题

给定冻结目标模型 \(M\)、执行 Harness \(H\)、当前 Skill 包 \(S_t\)、训练集 \(D_{train}\)、验证集 \(D_{validation}\)、隐藏测试集 \(D_{test}\) 和评估函数 \(E\)，Agent 在案例 \(x\) 上执行后得到轨迹和多维评分：

\[
(\tau, \mathbf{r}) = H(M, x, S_t)
\]

优化器根据训练轨迹生成候选：

\[
\{S_{t+1}^{(1)}, \ldots, S_{t+1}^{(k)}\}
= O(S_t, \{\tau, \mathbf{r}\}, \mathcal{H}_t)
\]

其中 \(\mathcal{H}_t\) 是历史接受、拒绝和回滚记录。目标是在硬约束内提高隐藏分布上的质量，而不是只提高一次评测总分。约束至少包括：关键故障类型不得退化、无依据结论率不得上升、不得新增超时和空报告、时延和 Token 增长受限、Skill 不得包含案例专属答案和敏感信息。

### 2.3 内核日志分析的特殊性

内核日志分析具有以下特征：

- 同一故障机制存在大量设备、版本和日志裁剪变体；
- 错误类型、根因、责任模块、证据链、时间线和建议需要分别评分；
- 根因、触发者和恢复动作必须严格区分；
- 模型容易将“等待锁”过度归因为“持有全局锁”；
- 部分指标可程序化验证，部分依赖 LLM Judge；
- 相同 Skill 的重复执行存在方差；
- 案例数量有限，数据泄漏代价高。

因此，AnalystBench 不能采用简单的“失败后整体重写 Prompt”循环。

---

## 3. 业界现状与相关工作

### 3.1 SkillsBench：Skill 有效性并非天然成立

SkillsBench v4 对 87 个任务、8 个领域和 18 个 Model–Harness 配置进行了配对评估。主要结论包括：

- 人工整理 Skill 将平均通过率从 33.9% 提高到 50.5%，提升 **16.6 个百分点**；
- 软件工程领域平均提升 **11.6 个百分点**，领域差异显著；
- 87 个任务中有 **13 个任务**使用 Skill 后出现负向变化；
- 在 claude、Codex 和 Gemini CLI 三个专用配置中，一次性自生成 Skill 相对无 Skill 分别下降 **8.1、11.3 和 11.5 个百分点**；
- 1～3 个聚焦 Skill 的平均增益高于包含 4 个及以上 Skill 的组合，紧凑或标准长度的 Skill 也优于全面文档式内容；
- 主实验对每个任务和条件使用三个 trial，并以任务级配对方式聚合。

这说明 Skill 是高价值适配层，但必须被系统评测；自动生成结果不能直接发布；删除、裁剪和替换与新增同样重要。

### 3.2 SkillOpt：受控文本空间优化

SkillOpt 将单个自然语言 Skill 文档视为冻结模型的可训练外部状态。其循环包括：目标模型执行批量任务；优化器分别分析成功与失败轨迹；提出 `append`、`insert_after`、`replace`、`delete` 编辑；合并重复或冲突建议；通过“文本学习率”限制每步修改数量；在独立验证集上测试；严格提升才接受；拒绝编辑进入负反馈缓冲；Epoch 边界进行慢速 Meta 更新。

SkillOpt 默认采用 4 个 Epoch、rollout batch 40、reflection minibatch 8、文本学习率 4，并使用严格验证门禁、Rejected Buffer 和优化器侧 Meta Skill。论文报告其在 6 个 Benchmark、7 个目标模型和 3 种 Harness 的 52 个组合上均达到最佳或并列最佳；对 GPT-5.5，相对无 Skill 基线在直接对话、Codex 和 claude Harness 中分别平均提升 23.5、24.8 和 19.1 个百分点。

对 AnalystBench 的启示：Skill 更新必须小步、结构化；成功轨迹用于保护已有能力；候选必须经过独立门禁；失败修改也应成为后续证据；优化只增加离线成本，不增加线上推理调用。

### 3.3 Trace2Skill：从批量轨迹归纳通用经验

Trace2Skill 认为逐条轨迹顺序修改容易将局部失败固化为案例专属规则。它先并行分析大量成功和失败轨迹，提取轨迹局部经验，再通过层次化合并生成统一、无冲突的 Skill 目录。

该机制适合内核日志。例如多条案例分别表现为：漏掉 watchdog 最终动作；把等待锁线程误判为持锁线程；没有分析锁传播；时间线正确但证据未绑定。逐条修补可能形成重复规则；批量归纳则可得到通用的“阻塞—持锁—调度—传播—恢复”分析流程。

对 AnalystBench 的启示：优化证据应按批次和故障家族归纳；并行分析后需要层次化去重和冲突消解；目标是通用程序性知识，而不是关键词记忆。

### 3.4 CoEvoSkills / EvoSkills：独立代理验证器

该工作在论文正文中使用 CoEvoSkills 名称，arXiv 摘要条目当前也出现 EvoSkills 名称。其核心是将 Skill Generator 与 Surrogate Verifier 分离：代理验证器读取任务要求和候选产物，不依赖生成器的内部推理，从而降低确认偏差。Surrogate Verifier 提供细粒度断言、失败原因和修复建议，Ground-truth Oracle 保持最终权威判定。

论文消融中，移除代理验证器后通过率从 71.1% 降至 41.1%，说明仅依赖不透明的通过/失败信号难以定向修复。但代理测试全部通过时，权威 Oracle 仍可能失败，所以代理验证不能替代发布门禁。

对 AnalystBench 的启示：格式检查、证据检查、诊断型 Judge 和权威评分应分层；优化器不能看到隐藏标准答案；代理验证负责反馈，权威 Judge 负责发布决策。

### 3.5 SkillMOO：多目标优化与裁剪

SkillMOO 将 Skill 优化建模为同时考虑通过率、成本和耗时的多目标搜索，通过 LLM 变异和 NSGA-II 保留 Pareto 候选。其软件工程实验显示，Skill bundle 的裁剪与替换比盲目扩展更有效；新增指导在部分实验中没有提高通过率，而裁剪和替换能够降低成本。

AnalystBench 优化器必须支持删除、合并、替换和识别无效章节，并将质量、时延、Token 和成功率共同纳入门禁。

### 3.6 MUSE-Autoskill：生命周期管理

MUSE-Autoskill 将 Skill 视为长期资产，生命周期包括创建、记忆、管理、评估和精炼。其 Skill 包配置 `tests/`，创建后在沙箱执行单元测试，只有全部通过才注册到 Skill Bank；失败时读取错误轨迹并修复。系统还维护每个 Skill 的专属经验记忆。

对 AnalystBench 的启示：完整包而非单个 `SKILL.md` 需要版本化；包内脚本应有测试；运行时 Skill、优化器记忆和审计历史必须分离；结果应进入统一 Registry。

### 3.7 SkillOS、SkillHone 与 MetaSkill-Evolve

SkillOS 面向持续任务流，由训练得到的 Skill Curator 更新 SkillRepo，适合未来管理多个内核故障域 Skill，但第一版直接引入 RL 成本过高。

SkillHone 强调持久化决策历史：为什么改、改了什么、依据是什么、候选被接受还是拒绝。长期维护不能只保留最终文件。

MetaSkill-Evolve 将优化流程拆成 Analyzer、Retriever、Allocator、Proposer 和 Evolver 五类 Meta Skill，并在慢速循环中优化“优化器本身”。该方向适合后续研究，不应进入第一版，否则会同时引入任务 Skill、优化器和 Judge 多层漂移。

### 3.8 对比

| 系统 | 核心机制 | 验证 | 长期记忆 | 多目标 | 建议阶段 |
|---|---|---|---|---|---|
| SkillsBench | Skill 三条件配对评测 | 确定性验证 | 否 | 否 | 评测基线 |
| SkillOpt | 受限 Patch、验证门禁、拒绝缓冲 | Held-out | Meta 记忆 | 单目标为主 | V1 核心 |
| Trace2Skill | 并行轨迹归纳、层次合并 | 结果评测 | 有限 | 否 | V1 核心 |
| CoEvoSkills | Generator/Verifier 协同 | 代理 + Oracle | 迭代上下文 | 否 | V2 |
| SkillMOO | LLM 变异 + Pareto | 多次执行 | 搜索 Archive | 是 | V2 |
| MUSE-Autoskill | 创建、测试、记忆、管理 | 单测 + 反馈 | 每 Skill 记忆 | 可扩展 | V2 |
| SkillOS | RL Curator | 下游任务奖励 | SkillRepo | 复合奖励 | V3 |
| SkillHone | 持续决策历史 | 角色隔离 | 完整历史 | 可扩展 | V1 应吸收 |
| MetaSkill-Evolve | 双时间尺度递归演进 | 分支评估 | Meta Skill | 可扩展 | 后期 |

---

## 4. 从概念闭环到可验证系统的工程缺口

`SkillVersion + EvaluationVariant + 三次基线/候选评测 + 自动提升` 构成了最小闭环，但从演示性流程进入可复现研究和生产系统仍需补充：

1. **文本学习率。** 不能只限制最多五轮，还要限制每轮操作数、修改文件数、Token 和单文件变化比例。
2. **成功能力保护。** 只分析失败会导致规则持续膨胀，必须同时提取需要保留的成功行为。
3. **多候选搜索。** 单候选贪心容易陷入局部最优，应生成纠错、证据强化、裁剪和工具增强等不同方向。
4. **多维门禁。** 总平均分可能掩盖关键故障家族退化。
5. **结构化优化信号。** 自然语言评语需要统一映射为可统计 Failure Tags。
6. **失败编辑记忆。** 候选拒绝后必须记录目标、Patch、退化案例和原因，防止重复试错。
7. **完整包版本。** `scripts/`、`references/` 和 `tests/` 必须与 `SKILL.md` 一起冻结。
8. **优化器/验证器版本。** Prompt、模型、Judge、门禁和数据切分均需版本化。
9. **隔离安装机制。** Skill 的源目录、运行时安装目录和 Harness 发现规则必须显式配置；候选不能通过覆盖用户全局目录切换。
10. **小样本结论边界。** 重复运行只能估计同一 Case 的随机波动，不能替代独立 Validation 或 Hidden Test，也不能证明跨故障家族泛化。
11. **制品版本与项目版本分离。** Skill 历史可以由 AnalystBench 内部 Git 仓库管理，但不得向 AnalystBench 仓库或用户源仓库写入 commit、branch、tag 或 Git 配置。

建议 Failure Tags：

```text
WRONG_ERROR_TYPE
WRONG_ROOT_CAUSE
MISSING_ROOT_CAUSE
WRONG_RESPONSIBLE_COMPONENT
UNSUPPORTED_CLAIM
MISSING_EVIDENCE
EVIDENCE_NOT_BOUND
TIMELINE_INCONSISTENT
CAUSAL_CHAIN_BROKEN
OVERCONFIDENT_CONCLUSION
TOOL_MISUSE
FORMAT_SCHEMA_ERROR
TIMEOUT
EMPTY_REPORT
EXECUTION_FAILURE
```

---

## 5. 本文方案：AnalystBench Skill Training Engine

### 5.1 设计原则

- 冻结目标模型、Harness、Benchmark、Judge 和运行环境；
- 完整 Skill 包不可变版本化；
- 修改必须由可追溯轨迹支持；
- 使用结构化 Patch 和文本学习率；
- 每轮生成多个不同方向的候选；
- 静态检查、代理验证和权威评测分层；
- 基线与候选按相同 Case 配对；
- 拒绝修改和回滚记录同样持久化；
- 优化器记忆不加载到线上 Agent；
- 只有通过门禁的版本才能原子切换 Active。
- 用户源目录只作为导入源，不作为候选运行目录；
- Skill 版本保存在 AnalystBench 自己管理的内部 Git 仓库，和用户项目、AnalystBench 项目的 Git 历史完全隔离；
- 每次运行将冻结版本物化到该次 Harness 工作区的项目级 Skill 目录，例如 `.claude/skills/<skill-name>`。
- 晋升或回滚只改变 Skill × Target Binding；后续按 Target 发起的普通评测冻结
  当时 Active Variant/Version，历史 Run 不随 Binding 变化；
- V1 一个 Evaluation Target 最多绑定一个 Active Skill，不对多 Skill 做隐式选择；
- 可回滚目标仅限同一 Binding 上曾经 Active 的版本，并需要乐观锁与审计原因；任意
  不可变版本可导出 ZIP 供人工审核，但导出不会改变 Active 或写回源 Skill。

### 5.2 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│                  AnalystBench Control Plane                 │
├─────────────────────────────────────────────────────────────┤
│ Skill Registry │ Experiment Manager │ Promotion / Rollback  │
└──────────┬──────────────────┬──────────────────────┬─────────┘
           │                  │                      │
           ▼                  ▼                      ▼
┌────────────────┐  ┌────────────────────┐  ┌─────────────────┐
│ Evidence       │  │ Optimization       │  │ Evaluation      │
│ Collector      │  │ Engine             │  │ Scheduler       │
│ trajectories   │  │ reflect/cluster    │  │ baseline        │
│ scores/claims  │  │ mutate/consolidate │  │ screen/validate │
└───────┬────────┘  └──────────┬─────────┘  └────────┬────────┘
        └──────────────────────┼─────────────────────┘
                               ▼
                    ┌─────────────────────┐
                    │ Candidate Sandbox   │
                    │ temp HOME/workspace │
                    └──────────┬──────────┘
                               ▼
                    ┌─────────────────────┐
                    │ Verifier Pipeline   │
                    │ static→diagnostic   │
                    │ →authoritative      │
                    └─────────────────────┘
```

### 5.3 核心领域模型

```text
Skill
  └── SkillPackageVersion
        ├── parent_version_id
        ├── package_hash
        ├── internal_git_commit
        ├── manifest
        ├── source_snapshot
        └── status

EvaluationTarget
  = HarnessVersion × ModelVersion

EvaluationVariant
  = EvaluationTarget × SkillPackageVersion

OptimizationExperiment
  ├── base_skill_version_id
  ├── optimizer_policy_version_id
  ├── verifier_bundle_version_id
  └── data_snapshot_id

OptimizationEpoch
  ├── evidence_batch
  ├── candidate_mutations[]
  ├── screening_results[]
  └── gate_decision

DecisionRecord
  ├── diagnosis
  ├── candidate_revision
  ├── redacted_evidence
  └── outcome
```

`data_snapshot_id` 指向同时冻结 Benchmark 内容身份和
Train/Validation/Hidden/Prospective 切分的 `OptimizationDataSnapshot`；早期分开的
`benchmark_snapshot_id`/`split_snapshot_id` 不是当前实现字段。

`package_hash` 是跨存储实现的制品身份，内部 Git commit 用于 Diff、父子关系、导出和回滚。两者都必须保存，但不能只用 commit hash 代替规范化包哈希。内部 Git 仓库位于 AnalystBench Managed Root 下，不嵌套在用户 Skill 源目录或 AnalystBench 源码仓库中。
新导入的 v2 package manifest 还把每个文件归一为 `0644`/`0755` 的执行语义
以及固定 `ignored_paths` 规则纳入哈希，拒绝 setuid/setgid。运行物化后
整包只读，但保留非执行 `0444`与可执行
`0555` 的区别。旧 v1 manifest 仅为已存制品保留兼容复核，新导入不再生成
v1。

### 5.4 Skill 发现与隔离物化

一个 Skill 注册项至少保存：

```json
{
  "source_path": "<frozen-harness.skill_base_dir>/skills/kernel-log-analysis",
  "invoke_as": "/kernel-log-analysis",
  "install_relative_path": ".claude/skills/kernel-log-analysis",
  "harness_key": "claude-skill"
}
```

导入时，系统只读取 `source_path` 指向的 Skill 包，过滤 `.git`、缓存、设备文件和越界符号链接，再提交到该 Skill 自己的内部 Git 仓库。运行时不从 `source_path` 直接执行，而是把指定 commit 对应的包物化到本次隔离工作区：

```text
<run-workspace>/
├── .claude/
│   └── skills/
│       └── kernel-log-analysis/
│           ├── SKILL.md
│           ├── references/
│           └── scripts/
└── logs/
```

`install_relative_path` 必须是受限相对路径，并由 Harness Adapter 校验是否属于该 Harness 允许的项目级 Skill 根。系统只复制目标 Skill；不得复制用户完整的 `.claude/`、全局 HOME、settings、hooks、plugins、认证文件或其他未声明 Skill。若一个 Skill 依赖其他 Skill，依赖项必须显式登记并分别冻结。
普通产品流程不让用户自由手填上述路径：`source_path` 由与 Target 兼容的
冻结 Harness `skill_base_dir` 和 Skill Key 派生，`invoke_as=/<skill-key>`，安装路径
派生为 `.claude/skills/<skill-key>`。运行目标命令还会收到实际冻结的
`ANALYSTBENCH_SKILL_VERSION_ID`；权威运行身份同时持久化在 Submission/Variant 中。

### 5.5 OptimizationSignal

```json
{
  "case_id": "case-xxx",
  "case_family": "hungtask-lock-contention",
  "score": 82.5,
  "failure_tags": ["MISSING_ROOT_CAUSE", "EVIDENCE_NOT_BOUND"],
  "missing_claims": ["未说明 watchdog 是最终恢复触发者"],
  "unsupported_claims": ["声称 chmod 持有全局锁，但日志只证明其等待锁"],
  "evidence_errors": [
    {
      "claim": "chmod 持锁",
      "problem": "block LOCK 只能证明阻塞，不能证明锁 owner"
    }
  ],
  "preserve_behaviors": ["正确识别 hungtask 超过 120 秒"]
}
```

### 5.6 批量结果与轨迹归纳

当前 V1 每个 Epoch 已使用同一个冻结 claude Optimizer Profile
依次执行四类显式版本分析：Failure Analyst 提取重复失败；Success
Analyst 保护有效规则；Generalization Analyst 识别案例专属修补；Simplification
Analyst 识别重复、冲突和无效内容。

当前 V1 实现中，持久化评测可保留 Candidate Report、评分 artifact、生成状态和耗时，
但优化器 Prompt 实际只接收经过去敏与聚合的 Train/Development 信号：Case path、
Family、Overall/Dimension score 和 Failure Tags，以及同实验 Rejected History。它不直接
获得原始日志全文、完整 Candidate Report、stdout/stderr 或逐 Claim 评分明细。因此
“从完整轨迹归纳”仍是后续研究扩展，不应写成当前已实现能力。只有 Harness 能稳定
导出结构化 Tool Trace 时，Trace 才可作为未来附加证据。
每个角色的输出使用严格 JSON envelope 和 `structured_skill_patch.v1`。
非法 JSON 仅允许同 Runner 格式修复一次；`AgentRunnerError` 最多尝试
三次，两次退避为 1/2 秒。各角色提案按固定角色顺序 round-robin，
只按 canonical patch hash 去重；当前不做语义相似度合并。

### 5.7 受限候选

V1 默认每轮从四角色提案中取两个 canonical-hash 去重候选。
候选意图可为纠错、证据强化、精简或工具增强，不强制候选序号与
意图类型一一对应。`structured_skill_patch.v1` 只支持
`append`、`insert_after`、`replace`、`delete`；`create`、
`old_text`、schema 外字段、unified diff 和 shell 命令会被拒绝。

```yaml
edit_budget:
  max_operations: 4
  max_changed_files: 2
  max_added_tokens: 600
  max_deleted_tokens: 300
  max_single_file_change_ratio: 0.25
  allowed_operations: [append, insert_after, replace, delete]
```

推荐逐轮衰减为 `4, 4, 3, 2, 1` 个操作。

### 5.8 分级验证

**第一级：确定性静态检查。** 检查目录、Schema、editable paths、凭据、绝对路径、案例泄漏、引用文件、包内测试、文件与 Token 限制。包内测试不根据
`tests/` 目录隐式运行；必须由 `manifest.json` 声明 `package_tests.argv`，并在
bubblewrap 只读、无网络 namespace 中执行。未声明时记录 `not_configured`；已声明
但 `bwrap`/namespace 不可用时静态拒绝候选。

**第二级：诊断验证。** V1 复用确定性规则和现有结构化评分信号，诊断格式、Claim—Evidence 绑定、时间线、根因与触发者混淆、无依据结论和明显回归。V2 可增加与优化器隔离的独立 Surrogate Verifier；它只提供诊断，不决定发布。

**第三级：权威评测。** 使用冻结 Eval Spec、Judge 和标准答案运行完整 Benchmark，只有权威门禁通过才能发布。

### 5.9 配对评测与门禁

每个案例取多次运行中位数：

\[
\Delta_i = median(candidate_i) - median(baseline_i)
\]

整体为所有 Case Delta 的平均值。建议默认门禁如下；其中统计置信度条件只在独立 Validation Case 达到预注册最小数量时启用，当前四 Case 开发模式只执行效应门槛与硬回归约束：

- 平均配对质量分提升不少于 `+1.0`；
- 配对 Bootstrap 单侧 95% 下界大于 0，或候选优于基线概率不低于 0.95；
- 错误类型和根因准确率不得下降；
- 每个 Case 的 `forbidden_hit_count` 与 `missing_chain_count` 重复中位数均不得上升；
- 关键故障家族最大退化不超过 2 分；
- 不得新增超时、空报告或执行失败；
- 成功生成率不得下降；
- 中位耗时和输出报告规模增长不超过 20%。当前 `token_count` 是
  `ceil(最终 stdout 字符数/4)` 的确定性估算，不是 provider 总 Token；Full Gate
  要求基线/候选每个配对都有值，缺失或超阈值都硬拒绝。

运行次数采用自适应策略：筛选一次；正常验证三次；灰区在 Validation 上增加到五至七次。Hidden Test 不参与灰区增采样，也不参与任何 Epoch 的接受/拒绝；最终版本和实验方案冻结后，才按预注册的固定次数运行一次正式测试。

### 5.10 数据切分与当前四 Case 过渡方案

“拆分”是把不同 Case 按用途隔离，而不是把同一个 Case 的三次运行拆开：

- **Train**：当前优化器可读取去敏聚合的 Case path、Family、Overall/Dimension
  score 和 Failure Tags，用于提出 Patch；原始日志、完整报告和逐 Claim 明细
  未直接进入 V1 Optimizer Prompt；
- **Validation**：优化器不能读取标准答案和逐 Case 修复细节，只由系统用于选择候选和决定是否提升；
- **Hidden Test**：优化过程中完全不运行，只在最终版本冻结后衡量泛化。

三个重复 trial 解决的是随机性问题；三个集合解决的是信息泄漏和过拟合问题，两者不能互相替代。正式数据不使用纯随机切分，应按故障家族、设备、版本、同源事件或表现形式成组隔离。例如：Train 为设备 A 的 hungtask+mutex；Validation 为设备 B 的 hungtask+rwsem；Hidden Test 为 vCPU 被 Host 抢占导致 Guest 全局锁传播。同一原始事件的不同日志裁剪必须位于同一集合。

当前只有 4 个 Case，不足以同时形成有代表性的 Train、Validation 和 Hidden Test。V1 采用以下过渡策略：

1. 将现有 4 个 Case 标记为 `development_regression` 集，允许优化器读取并用于生成候选；
2. 基线和候选各运行三次，门禁只表示“在当前已知四题上的样本内提升与回归保护”，不得写成泛化结论；
3. 自动提升只更新 Managed Active，并标记 `provisional=true`，可立即回滚；
4. 后续新增的 Case 不会由系统自动分配；用户在新 Snapshot 中可显式将其放入
   `prospective_holdout`，在最终候选冻结前不向优化器暴露；
5. Case 数和故障家族足够后，冻结正式 Train/Validation/Hidden Test Snapshot，并停止用开发集结果替代论文主实验。

可配置的统计门禁保存 `minimum_independent_validation_cases`。当前 API 在创建
`independent_validation` Snapshot 时就强制 Validation Case 数达到该值；数量不足
会拒绝创建，不会先运行一个只具描述性的独立实验。

此外，`independent_validation` 在前端锁定、后端强制仅一个 Epoch，且同一
Snapshot 只能被一个已启动 Experiment 原子消费。如果根据该 Validation 结果继续修改 Skill，
它已经成为调参信号；下一次“独立验证”必须使用新的、未参与先前决策的数据
冻结新 Snapshot。Hidden/Prospective 只冻结和隔离，当前优化闭环不自动运行。

### 5.11 持续决策历史

当前实现不只保存“最后成功版本”。每个终态 Epoch 都冻结：父版本、所有
候选及拒绝原因、选中候选的 rationale/目标失败簇、实际修改文件与操作/增删
Token/Patch hash、静态检查、Baseline/Candidate/配对 Delta、逐
Case/Family/Dimension 变化、Gate 与 Active 决策。JSON 总账保留候选全量结构，
Markdown/CSV 提供一轮一行的主路径摘要。

总账中 `ACTIVE PATH SCORE` 定义为“初始基线分 + 仅已晋升 Epoch 的配对 Delta
之和”；拒绝/保留轮不进入累计。它是 Active 版本链的审计量，不是对最终版本
新做的 Holdout 绝对分，也不保证等于最后一轮 Candidate Score。论文应展示每轮
配对分数与独立 Hidden Test，不用该链式量代替泛化证据。

```json
{
  "diagnosis": {
    "target_failure_clusters": ["EVIDENCE_NOT_BOUND"],
    "evidence_refs": ["run-123", "run-131"]
  },
  "revision": {
    "candidate_version_id": "skill-v5-c",
    "patch_hash": "sha256:..."
  },
  "evaluation": {
    "overall_delta": -1.7,
    "regressed_families": ["suspend-timeout"]
  },
  "outcome": {
    "decision": "rejected",
    "reason": "提高证据召回，但引入过度归因"
  }
}
```

### 5.12 多模型发布

第一版 Active 绑定 `Harness × Model × Skill`。后续全局 Active 必须在指定模型集合上全部通过门禁。

---

## 6. 预期收益与研究假设

### 6.1 预期收益

- 提升错误类型、根因和证据链质量；
- 将专家工作从逐条改 Skill 转为审核失败聚类和候选；
- 通过门禁减少回归；
- 通过完整版本冻结提高复现性；
- 通过 Decision History 提高可审计性；
- 离线优化、线上零额外模型调用；
- 形成“Benchmark 发现问题—Skill 优化—验证发布—新数据继续训练”的资产飞轮。

### 6.2 研究假设

- H1：在独立样本量足够时，完整方案在隐藏测试集上优于人工 Skill，且置信区间支持正向效应；
- H2：受限 Patch + 验证门禁优于整体重写；
- H3：批量成功/失败归纳优于逐 Case 修改；
- H4：Rejected Buffer 减少重复试错；
- H5：多候选提高每轮有效改进概率；
- H6：代理验证降低权威 Judge 调用，但不能替代权威 Judge；
- H7：裁剪和替换可在不降质的情况下降低 Token 和耗时；
- H8：故障家族隔离切分更能反映泛化；
- H9：单模型优化 Skill 对相邻模型和 Harness 有部分迁移收益。

---

## 7. 实验设计

> 本节为实现后的实验模板。所有 `[待补]` 应以真实运行结果替换。

### 7.1 研究问题

- RQ1：自优化是否提升隐藏测试质量？
- RQ2：是否降低重复运行波动？
- RQ3：是否泛化到未见设备、版本和表现？
- RQ4：离线优化成本与线上成本如何变化？
- RQ5：文本学习率、门禁、成功保护、拒绝缓冲和代理验证各自贡献多少？
- RQ6：优化 Skill 能否跨模型和 Harness 迁移？
- RQ7：Development Regression 的连续多 Epoch 是否出现膨胀或遗忘？跨不同、未重用的
  Independent Validation Snapshot 时如何监测验证过拟合？

### 7.2 数据集

| 拆分 | 案例数 | 故障家族数 | 优化器可见信息 | 用途 |
|---|---:|---:|---|---|
| Train | [待补] | [待补] | V1：去敏的 path/Family/分数/Failure Tags 聚合 | 候选生成 |
| Validation | [待补] | [待补] | 维度汇总与门禁 | 接受/拒绝 |
| Hidden Test | [待补] | [待补] | 完全不可见 | 最终报告 |
| Transfer Test | [待补] | [待补] | 完全不可见 | 迁移实验 |

同一原始事件的不同裁剪不得跨集合；相同设备和故障签名尽量放同一集合；所有
切分与 Case/日志/Eval Spec 哈希一起冻结为不可变 `OptimizationDataSnapshot`，
Experiment 保存 `data_snapshot_id`。独立 Snapshot 不得被第二个 Experiment 成功启动。

在当前 4 Case 阶段，论文主结果表保持 `[待补]`，只允许增加“开发集先导实验”小节。先导实验应明确四个 Case 均参与了候选生成，因此只能用于验证系统闭环、观察样本内变化和估算运行方差，不能回答 RQ1、RQ3 或声称隐藏集泛化。后续新增 Case 应由用户在新 Snapshot 中显式保留为前瞻性 Hidden/Prospective Holdout，而不是立即加入优化器上下文。系统当前不自动为新 Case 分配 split。

### 7.3 对照基线

| 编号 | 方法 | 说明 |
|---|---|---|
| B0 | No Skill | 不加载领域 Skill |
| B1 | Human Skill | 当前人工维护版本 |
| B2 | One-shot Rewrite | 读取训练反馈后整体重写一次 |
| B3 | Sequential Edit | 每个失败案例后顺序修改 |
| B4 | Trace2Skill-style | 批量归纳，无严格验证门禁 |
| B5 | SkillOpt-style | 单候选、受限 Patch、验证门禁 |
| P1 | Proposed Full | 多候选、结构化信号、分级验证和决策历史 |
| P2 | Proposed + MOO | 加入质量/成本 Pareto 搜索 |

### 7.4 评价指标

质量：Overall Quality、Error Type Accuracy、Root Cause Accuracy、Responsible Component Accuracy、Evidence Precision/Recall、Claim-Evidence Binding、Timeline Consistency、Unsupported Claim Rate、Critical Omission Rate。

运行：Generation Success、Timeout、Empty Report、Schema Valid、Median/P95 Latency、输出报告
字符近似 Token、单案例成本。若私有 CLI/provider 另外提供可审计 Input/Output usage，
在研究数据中另列字段，不与当前 Gate 的 `approximate_output_characters` 混为一个指标。

优化过程：Candidate Acceptance、Accepted Gain per Epoch、Repeated Rejected Mutation、Skill Token Size、Optimization Cost、Oracle Invocation、Surrogate Catch Rate、Rollback Count。

### 7.5 主实验结果表

| 方法 | Overall ↑ | Error Type ↑ | Root Cause ↑ | Evidence Binding ↑ | Unsupported Claim ↓ | Success ↑ | Latency ↓ | Tokens ↓ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| No Skill | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] |
| Human Skill | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] |
| One-shot Rewrite | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] |
| SkillOpt-style | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] | [待补] |
| Proposed Full | **[待补]** | **[待补]** | **[待补]** | **[待补]** | **[待补]** | **[待补]** | [待补] | [待补] |

### 7.6 故障家族结果

| 故障家族 | 案例数 | Human Skill | Proposed | Delta | 门禁 |
|---|---:|---:|---:|---:|---|
| Hungtask / Lock | [待补] | [待补] | [待补] | [待补] | [待补] |
| Deadlock | [待补] | [待补] | [待补] | [待补] | [待补] |
| Suspend / Resume | [待补] | [待补] | [待补] | [待补] | [待补] |
| Memory Corruption | [待补] | [待补] | [待补] | [待补] | [待补] |
| Scheduler Latency | [待补] | [待补] | [待补] | [待补] | [待补] |
| Virtualization | [待补] | [待补] | [待补] | [待补] | [待补] |

### 7.7 消融实验

| 实验 | 配置 | Overall | Delta vs Full | 目的 |
|---|---|---:|---:|---|
| A0 | Full | [待补] | — | 完整方案 |
| A1 | 无文本学习率 | [待补] | [待补] | 过度修改影响 |
| A2 | 无成功轨迹分析 | [待补] | [待补] | 灾难性遗忘 |
| A3 | 无 Rejected Buffer | [待补] | [待补] | 重复试错 |
| A4 | 无代理验证器 | [待补] | [待补] | 诊断反馈价值 |
| A5 | 单候选 | [待补] | [待补] | 多候选价值 |
| A6 | 无裁剪操作 | [待补] | [待补] | Skill 膨胀 |
| A7 | 随机切分 | [待补] | [待补] | 数据泄漏影响 |
| A8 | 仅总平均分门禁 | [待补] | [待补] | 多维门禁价值 |

### 7.8 统计方法

报告案例级配对 Delta、均值、中位数、标准差、配对 Bootstrap 95% 区间、候选胜率和故障家族最大退化。Bootstrap 以独立 Case 为重采样单元，不以三次 trial 为独立样本。当前实现默认 2000 次，样本数/置信度冻结在 Verifier，根据 Experiment/Epoch/Candidate 和实际 Case Delta 导出稳定 Seed 并持久化。仅在独立非零配对数足够时报告 Wilcoxon signed-rank test；多候选、多 Epoch 或多维重复检验使用 Holm 校正。当前 4 Case 先导实验只报告每个 Case 的原始三次结果、配对 Delta 和描述性区间，不报告“统计显著”。

### 7.9 成本收益

| 项目 | Human Skill | Proposed Full |
|---|---:|---:|
| 初始人工编写工时 | [待补] | [待补] |
| 每轮人工维护工时 | [待补] | [待补] |
| 优化器调用成本 | 0 | [待补] |
| Rollout 成本 | [待补] | [待补] |
| 线上额外模型成本 | 0 | 0 |
| 每提升 1 分成本 | [待补] | [待补] |

### 7.10 迁移实验

| 训练配置 | 测试配置 | Baseline | Optimized | Transfer Gain |
|---|---|---:|---:|---:|
| Model A + Harness A | Model B + Harness A | [待补] | [待补] | [待补] |
| Model A + Harness A | Model A + Harness B | [待补] | [待补] | [待补] |
| Kernel v1 | Kernel v2 | [待补] | [待补] | [待补] |

---

## 8. 风险、局限与治理

### 8.1 Judge 偏差

优先使用确定性检查；冻结 Judge 版本；采用多维 Rubric；抽样人工复核；关键发布可使用双 Judge 或裁决 Judge；定期评估 Judge 与专家一致性。

### 8.2 Benchmark 过拟合

按故障家族隔离；Validation 不返回答案；Hidden Test 不参与优化；扫描 Skill 中案例专属字符串；定期补充新案例；监控 Train、Validation 和 Test 差距。

### 8.3 Skill 膨胀和冲突

使用文本学习率、Token 上限、delete/replace、Simplification Analyst、冲突检查，并把成本和延迟设为硬约束。

### 8.4 越权和数据安全

采用 `editable_paths` 白名单、临时 HOME/XDG、凭据扫描、最小权限、Patch 审计和可选人工
审批。需要明确实现边界：Optimizer、Target 和 Judge 的 HOME 重定向只是进程环境/用户状态
隔离，不是 mount namespace，不自动阻断绝对路径访问或网络。当前只有 manifest
声明的 Skill 包内测试使用 bubblewrap 只读、无网络 namespace；私有宿主必须证明
`bwrap` 可创建 namespace，不得用“独立 HOME”代替沙箱验收。

### 8.5 环境漂移

记录模型标识与参数、Harness commit、Skill package hash、内部 Git commit、Judge、Benchmark snapshot、运行时间和资源信息。V1 若没有容器镜像，不得伪造该字段，应记录为 `not_containerized` 并保存可获得的 CLI、Python、操作系统和 Harness 版本。

### 8.6 实验成本

复用基线；采用一次筛选、三次验证、灰区增采样；代理验证减少权威 Judge 调用；按失败家族分层抽样；Early Stop；只对高复用 Skill 开启自动优化。

---

## 9. 分阶段路线

### V1：SkillOpt-Lite 原生闭环

普通 UI 由冻结 Harness `skill_base_dir` + Skill Key 派生源目录、调用名和项目级
安装路径。当前 V1 已实现内部 Git 不可变包、受限 Patch、四角色
Train-only Optimizer pipeline、每轮默认两个去重候选、
Screening、三次完整配对验证、灰区 5/7 次增采样、聚合 Failure
Family/Dimension/Tag Evidence、Rejected History、Run Group 恢复复用、Early
Stop、原子 Active、回滚、逐 Epoch 总账与 JSON/Markdown/CSV/ZIP 导出。

前端已支持 `development_regression` 和带 Train/Validation/Hidden/Prospective 切分编辑器的
`independent_validation`。独立模式强制单 Epoch，且单 Snapshot 只能被一个已启动 Experiment 消费；
Hidden/Prospective 只冻结与隔离，不自动运行。普通 Target 评测会冻结当时 Active
Variant/Version，历史 Run 不随晋升/回滚改变。

当前 Full Gate 强制完整的输出字符近似 Token usage，缺失或超阈值硬拒绝；逐 Case
的 `forbidden_hit_count`/`missing_chain_count` 重复中位数任一上升也硬拒绝。
Verifier 冻结 Judge 与 Bootstrap 策略，稳定 Seed 用于 Bootstrap 和基线/候选交错调度。
具体地，Bootstrap Seed 随实际 Case Delta 进入比较结果；每个 Validation
repeat 另以 Experiment/Epoch/Candidate/Repeat 生成 `pair_seed`，决定
baseline→candidate 或 candidate→baseline 顺序，并将 Seed/位置纳入冻结
run config hash 和 Submission context。
优化结果强制排除普通统计/列表，底层 Submission 取消与删除由 Experiment 状态机
保护；Submission idempotency key 与 Run Group hash 共同覆盖崩溃恢复。

仓库已通过两个冻结 Skill 版本的确定性 `/skill` 并发隔离 E2E。真实 claude
二进制 E2E 和 bubblewrap namespace E2E 都需在用户私有宿主显式运行；临时 HOME
不自动继承交互式 CLI 登录。四 Case 先导结果和独立 Holdout 主实验仍按本文件第 6
节保持 `[待补]`，不得把确定性契约测试写成真实效果。

### V2：生产级生命周期

代理验证器、更大规模多候选、跨实验 Skill 记忆、Hidden/Prospective 的独立发布工作流、
暴露登记与数据更换、跨模型联合门禁、质量/成本 Pareto 搜索和可选的显式 Git 源仓库
同步。任何源仓库同步都必须是单独授权的发布动作。

### V3：多 Skill 与数据扩充

Skill 检索和组合、SkillRepo、从内核文档和代码生成训练锚点、自动构造案例变体、跨模型联合发布。

### V4：Meta 优化

Analyzer、Proposer、Retriever 和候选分配策略的慢速自优化。

---

## 10. 结论

Agent Skill 是连接冻结大模型和企业领域流程的重要适配层，但其价值取决于真实任务验证，而不是是否由专家或大模型编写。现有研究揭示了稳定规律：一次性自生成和无约束重写不可靠；批量轨迹归纳优于对单个失败做局部反应；小步修改、验证门禁和失败记忆是稳定优化关键；删除与替换往往比持续新增更有效；代理验证提供诊断但不能取代权威门禁；长期 Skill 必须保留版本和决策历史。

AnalystBench 已具备 Benchmark、执行 Harness、版本冻结和独立评分基础，
并已完成 Skill Training Engine 的 V1 本地代码闭环。该能力不是增加一个
“自动重写”按钮，而是覆盖 Skill 训练、评估、优化、发布和回滚的控制面。
用户在私有环境完成真实 claude 与私有数据实验后，才能用本文定义的
主实验、消融实验和迁移实验量化其相对人工 Skill、一次性生成和
SkillOpt-style 基线的真实收益。

---

## 参考文献

[1] Li, X. et al. **SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks.** arXiv:2602.12670v4, 2026. https://arxiv.org/abs/2602.12670v4

[2] Yang, Y. et al. **SkillOpt: Executive Strategy for Self-Evolving Agent Skills.** arXiv:2605.23904, 2026. https://arxiv.org/abs/2605.23904

[3] Microsoft. **SkillOpt Official Repository.** 2026. https://github.com/microsoft/SkillOpt

[4] Ni, J. et al. **Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills.** arXiv:2603.25158, 2026. https://arxiv.org/abs/2603.25158

[5] Qwen Applications. **Trace2Skill Official Repository.** 2026. https://github.com/Qwen-Applications/Trace2Skill

[6] Zhang, H. et al. **CoEvoSkills / EvoSkills: Self-Evolving Agent Skills via Co-Evolutionary Verification.** arXiv:2604.01687, 2026. https://arxiv.org/abs/2604.01687

[7] Lin, H. et al. **MUSE-Autoskill: Self-Evolving Agents via Skill Creation, Memory, Management, and Evaluation.** arXiv:2605.27366, 2026. https://arxiv.org/abs/2605.27366

[8] Ouyang, S. et al. **SkillOS: Learning Skill Curation for Self-Evolving Agents.** arXiv:2605.06614, 2026. https://arxiv.org/abs/2605.06614

[9] **SkillMOO: Multi-Objective Optimization of Agent Skills for Software Engineering.** arXiv:2604.09297, 2026. https://arxiv.org/abs/2604.09297

[10] Li, Z., Hu, Y. **SkillHone: A Harness for Continual Agent Skill Evolution Through Persistent Decision History.** arXiv:2606.08671, 2026. https://arxiv.org/abs/2606.08671

[11] Wang, Z. et al. **MetaSkill-Evolve: Recursive Self-Improvement of LLM Agents via Two-Timescale Meta-Skill Evolution.** arXiv:2607.05297, 2026. https://arxiv.org/abs/2607.05297

[12] Agrawal, L. A. et al. **GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning.** arXiv:2507.19457 / ICLR 2026. https://arxiv.org/abs/2507.19457
