# AnalystBench Skill 自优化系统方案设计

> 文档类型：工程设计规格 / Codex 开发输入
> 状态：V1 本地代码已实现；私有环境证据待运行
> 版本：v1.2
> 日期：2026-08-12
> 目标版本：Skill Optimization V1
> 目标读者：Codex、后端开发、前端开发、测试、架构评审人员

配套研究与实验口径：[AnalystBench 面向内核日志分析的 Skill 自优化方法研究与实验方案](../skillopt/AnalystBench-Skill自优化研究与实验方案.md)。

---

## 0. Codex 执行约束

Codex 在开始编码前必须：

1. 阅读仓库根目录 `README`、开发说明和已有架构文档；
2. 定位现有 HarnessVersion、ModelVersion、EvaluationTarget、Benchmark Suite、Case Revision、Agent Runner、Candidate Report、Judge、Eval Spec、任务调度器、数据库迁移和前端 API 封装；
3. 复用现有 ORM、任务队列、异常、日志、API 返回格式和权限体系；
4. 不得为了本功能复制一套并行 Evaluation Runner；
5. 不得修改现有历史评测语义；
6. 所有新增表和字段必须有迁移脚本；
7. 所有任务和状态变化必须幂等、可重试；
8. 后端核心逻辑必须有单元测试，状态机、Patch 和发布门禁必须有集成测试；
9. 第一版只实现单 Skill、单 Harness、单 Model，不实现 RL、MetaSkill、跨 Skill 自动组合；
10. 若本文伪路径与仓库结构不一致，以实际结构为准，并先输出映射文档。

本文已按 2026-07-31 的 Linux 工作区完成第一轮仓库映射。后续开发仍须重新核对当前分支和迁移头，不能把本文记录当成永不变化的事实。

### 0.1 已确认产品决策

1. 普通产品流程只读发现冻结 Harness 的 `skill_base_dir/skills/*/SKILL.md`，并由用户选择明确的 Harness × Model × Skill 组合：
   `<skill_base_dir>/skills/<skill-key>`；`name=<skill-key>`、
   `invoke_as=/<skill-key>`，不再要求用户分别手填；
2. 每次目标 Agent 运行前，把冻结 Skill 版本安装到该次隔离工作区的项目级 Skill 目录，例如 `<workspace>/.claude/skills/xxx`；
3. 不复制用户完整 `.claude/` 或全局 HOME，只复制明确登记的 Skill 包和显式依赖；
4. Skill 历史使用 AnalystBench 自己管理的内部 Git 仓库，不向 AnalystBench 源码仓库或用户源仓库写入 commit、branch、tag、配置或工作区文件；
5. 提升只更新 `SkillTargetBinding.active_version_id`，V1 不自动同步回用户 `source_path`；
6. 当前只有 4 个 Case，V1 先采用 `development_regression` 小样本模式；三次重复用于降低随机波动，不等价于独立 Validation 或 Hidden Test；
7. 新增 Case 不会由系统隐式分配 split；用户在创建不可变 Snapshot 时可显式
   放入 `prospective_holdout`，在正式候选冻结前不向优化器公开；
8. 每个 Epoch 必须持久化“改了什么、基线/候选分数、升降 Delta、逐
   Case/Family/Dimension 变化、Gate 和 Active 决策”，并可导出；
9. 真实 claude 验收和研究结果只在用户私有环境运行，不由 Codex 或确定性替身
   测试代跑、代填。

### 0.2 当前仓库映射

| 设计概念 | 当前实现 | V1 改动原则 |
|---|---|---|
| Harness / Model / Target | `src/analystbench/db/models.py` 中 `EvaluationHarness`、`EvaluationModel`、`EvaluationTarget` | 继续复用；Target 仍表示 Harness × Model |
| 可执行 Target | `EvaluationTarget.materialized_method_id` 指向 `EvaluationMethod` | 新增 Variant 后物化独立 Method 或等价冻结执行快照 |
| 批量生成与评分 | `EvaluationSubmissionService`、`EvaluationSubmissionCaseRun`、`EvaluationSubmissionMethodRun` | 复用现有命令执行、正式结果和评分链路，不复制 Runner |
| 后台任务 | `Job`、`JobQueue`、`LocalWorker` | 增加优化 Job 类型和资源限流；保持租约与幂等 |
| Agent Profile | `ExecutionProfile`、`AgentExecutionService` | 优化器模型优先复用冻结 ExecutionProfile，不把它混同为被测 EvaluationModel |
| 内容存储 | `ContentStore` + `content_blobs` | Prompt、证据摘要等复用 ContentStore；Skill 文件历史使用独立内部 Git |
| 数据库迁移 | Alembic，当前头为 `0018_evaluation_submission_idempotency` | 后续改动始终从实际最新头创建后继迁移 |
| API | FastAPI，统一前缀 `/api/v1` | 本文所有新 API 使用 `/api/v1/...` |
| 配置 | `pydantic-settings`，环境变量前缀 `ANALYSTBENCH_` | 不新增独立 YAML 事实源 |
| 前端 | Vue 2，`App.vue` + `app-options.js`，Axios，现有四个主视图 | V1 在“设置/评测结果”内增加二级界面，保持四个主视图；先沿用轮询 |

当前 `EvaluationSubmissionMethodRun` 的唯一约束是 `(case_run_id, method_id)`，同一 Submission 不能直接容纳同一 Case/Method 的三个 repeat。V1 必须通过 Optimization Rollout Group 显式创建多个底层 Submission，或新增包含 `repeat_index` 的专用 Rollout 表；不得假设现有表已经支持重复运行。

---

## 1. 背景与目标

AnalystBench 当前能够冻结 Harness、Model 和 EvaluationTarget，运行 Benchmark、生成 Candidate Report 并调用 Judge 评分。新增能力需要基于历史轨迹和评分自动生成 Skill 候选，并在隔离环境中完成验证、提升和回滚。

```text
Active Skill vN
      │
      ├── baseline runs
      └── evidence + reflection
                 │
                 ▼
           candidate patches
                 │
        static validation
                 │
            screening
                 │
        full paired validation
                 │
          promotion gate
           ┌─────┴─────┐
        promote      reject
           │             │
     active_version   rejected buffer
```

本功能不得直接修改用户原始 Skill 目录。所有候选在 AnalystBench 管理的不可变快照上生成和运行。

### 1.1 V1 目标

- Skill 注册、导入和完整包快照；
- AnalystBench 内部 Git 仓库、不可变 `SkillPackageVersion` 与包级哈希；
- Evaluation Run 绑定具体 Skill 版本；
- 普通 UI 从冻结 Harness + Skill Key 派生源目录、调用名和项目级安装路径；
- 独立临时 HOME / Workspace / Skill Root；HOME/XDG 是进程环境隔离，不冒充通用
  文件系统或网络沙箱；
- Optimization Experiment 和 Epoch 状态机；
- 基线结果缓存和复用；
- 成功/失败轨迹证据收集；
- 结构化 `OptimizationSignal`；
- 受限 Structured Patch；
- 每轮最多两个候选；
- 静态验证和包内测试；
- 单次 Screening；
- 三次完整配对评测，灰区最多七次；
- 发布门禁；
- Rejected Mutation Buffer；
- Decision History；
- Active 原子提升和手动回滚；
- 前端查看实验、Epoch、候选、Diff、分数和退化。
- 每轮修改与得分总账，JSON/Markdown/CSV 导出，版本 ZIP 和带审计的显式回滚；
- `development_regression`、独立 Train/Validation 和后续 Holdout 的严格术语与数据隔离。

### 1.2 非目标

V1 不实现：RL Skill Curator、MetaSkill 自优化、多 Skill 自动组合、跨 Harness 全局 Active、自动覆盖或提交用户源仓库、自动生成标准答案、代理验证器自动演化、完整 NSGA-II 搜索、线上请求实时自修改、容器级恶意代码隔离。

---

## 2. 核心原则

1. 已创建 SkillPackageVersion 不允许原地修改；
2. `SKILL.md`、`scripts/`、`references/`、`tests/` 和 manifest 统一哈希；
3. 每个 Evaluation Run 必须绑定具体版本；
4. 候选不得写入用户 `source_path` 或共享全局 Skill 目录；
5. 所有修改通过结构化 Patch 表达并受编辑预算限制；
6. 基线和候选使用相同 CaseRevision 和运行参数；
7. Active 只能由受信初始 Binding、Promotion Service 或带审计的显式 Rollback 修改；
8. Worker 崩溃后可从持久化状态恢复；
9. 所有诊断、修改、证据和决策可审计；
10. Optimizer Memory 不加载到线上 Agent。
11. 内部 Git 只管理 Skill 制品，不能发现或修改用户源目录及 AnalystBench 仓库的 `.git`；
12. 重复 trial、Train/Validation/Hidden Test 和当前四 Case 开发模式必须使用不同术语和状态，不能把重复次数写成独立样本数。

---

## 3. 术语

| 术语 | 定义 |
|---|---|
| Skill | 逻辑 Skill，如 `kernel-log-analysis` |
| SkillPackageVersion | Skill 完整不可变文件快照 |
| Internal Skill Repository | AnalystBench Managed Root 下每个 Skill 独立的 Git 仓库，与所有用户仓库隔离 |
| Active Version | 某 EvaluationTarget 默认使用的版本 |
| EvaluationTarget | `HarnessVersion × ModelVersion` |
| EvaluationVariant | `EvaluationTarget × SkillPackageVersion` |
| Install Relative Path | Skill 在隔离工作区内的项目级安装位置，如 `.claude/skills/kernel-log-analysis` |
| OptimizationExperiment | 一次完整优化任务 |
| OptimizationEpoch | 一轮证据、候选和评测 |
| OptimizationRunGroup | 一个 arm、split 和 repeat 组合对应的一组底层 Evaluation Submissions |
| CandidateMutation | 从父版本到候选版本的受限修改 |
| OptimizationSignal | 案例评分抽取出的结构化优化信号 |
| DecisionRecord | 诊断、修改、证据和结果记录 |
| Screening | 低成本单次候选筛选 |
| Full Validation | 与基线多次配对的完整验证 |
| Promotion Gate | 是否可成为 Active 的规则集合 |
| OptimizerPolicyVersion | 优化模型、Prompt 和编辑策略快照 |
| VerifierBundleVersion | 静态检查、Judge 和门禁快照 |
| Development Regression | 当前已知小样本上的样本内优化与回归保护，不代表泛化 |
| Independent Validation | Train 仅用于 Evidence/Screening，独立 Validation 仅用于一个 Epoch 的 Gate；同一 Snapshot 只能被一个已启动 Experiment 消费 |
| Prospective Holdout | 在候选冻结前不向优化器公开的后续新增 Case |

---

## 4. 总体架构

```text
┌────────────────────────────────────────────────────────────────┐
│ Frontend                                                       │
│ Skill Registry │ Experiments │ Candidate Compare │ History    │
└───────────────────────────────┬────────────────────────────────┘
                         │ REST / polling
                         │ (SSE 可后续增加)
┌───────────────────────────────▼────────────────────────────────┐
│ API / Control Plane                                            │
│ Skill Service │ Experiment Service │ Promotion Service         │
└───────────────┬───────────────────────┬─────────────────────────┘
                │                       │
        ┌───────▼────────┐      ┌───────▼────────────┐
        │ Optimization   │      │ Evaluation         │
        │ Orchestrator   │      │ Scheduler          │
        └───┬─────────┬──┘      └─────────┬──────────┘
            │         │                   │
┌───────────▼──┐ ┌────▼────────────┐ ┌────▼────────────────────┐
│ Evidence     │ │ Mutation        │ │ Existing Evaluation     │
│ Builder      │ │ Generator       │ │ Submission + Judge      │
└───────────┬──┘ └──────┬──────────┘ └────┬────────────────────┘
            └───────────┼─────────────────┘
                        ▼
              ┌─────────────────────┐
              │ Candidate Sandbox   │
              │ temp HOME/workspace │
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │ Gate / Decision     │
              │ Promote / Reject    │
              └─────────────────────┘
```

### 4.1 组件职责

**Skill Registry**：注册 Skill、导入源目录、创建快照、计算哈希、读取文件树和 Diff、管理 Active、导出版本。

**Optimization Orchestrator**：驱动实验和 Epoch 状态机，调度基线、证据、候选、筛选、验证、门禁和 Early Stop。

**Evidence Builder**：聚合 Candidate Report、Tool Trace 和 Judge 结果，生成 OptimizationSignal，按 failure tag 和 case family 聚类，脱敏并压缩轨迹。

**Mutation Generator**：调用 Optimizer Model，生成 Structured Patch，校验预算，应用 Patch 并创建候选包。

**Static Validator**：路径、结构、Schema、凭据、绝对路径、案例泄漏、引用、脚本语法、包内测试和大小限制。

**Evaluation Scheduler**：复用现有 Runner，运行基线、筛选和完整配对评测，交错调度并汇总中位数。

**Promotion Gate**：校验硬约束、计算 Case Delta 和 Bootstrap，输出 `promote/reject/needs_more_runs`。

---

## 5. 数据模型

以下为逻辑 SQL，必须映射到现有 ORM 和数据库。当前项目 UUID 使用 `String(36)`，JSON 使用规范化 JSON 字符串保存到 `Text`；示例中的 `UUID` 和 `JSON` 只表达逻辑类型，开发时不得引入与 SQLite 不兼容的假设。

### 5.1 skills

```sql
CREATE TABLE skills (
    id UUID PRIMARY KEY,
    skill_key VARCHAR(128) NOT NULL UNIQUE,
    name VARCHAR(256) NOT NULL,
    description TEXT,
    source_path TEXT,
    invoke_as VARCHAR(128) NOT NULL,
    harness_key VARCHAR(128) NOT NULL,
    install_relative_path TEXT NOT NULL,
    publish_mode VARCHAR(32) NOT NULL DEFAULT 'managed',
    editable_paths_json JSON NOT NULL,
    limits_json JSON NOT NULL,
    archived_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

约束：

- `skill_key` 创建后不可修改；
- `source_path` 只用于导入，不作为运行事实源；
- `install_relative_path` 必须是安全相对路径，并匹配 Harness Adapter 允许的项目级 Skill Root；
- claude 兼容 Harness 可使用 `.claude/skills/<skill-dir>`，其他 Harness 由 Adapter 声明允许前缀；
- V1 只支持 `managed`，不写回 `source_path`；
- 默认 editable paths 只有 `SKILL.md`；需允许 `references/**`、
  `scripts/**` 或 `tests/**` 时必须在注册时显式列出。

### 5.2 skill_package_versions

```sql
CREATE TABLE skill_package_versions (
    id UUID PRIMARY KEY,
    skill_id UUID NOT NULL REFERENCES skills(id),
    version_number INTEGER NOT NULL,
    parent_version_id UUID NULL REFERENCES skill_package_versions(id),
    package_hash VARCHAR(71) NOT NULL,
    git_commit VARCHAR(64) NOT NULL,
    git_tree VARCHAR(64) NOT NULL,
    git_object_format VARCHAR(16) NOT NULL,
    manifest_json JSON NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_by VARCHAR(128),
    created_at TIMESTAMP NOT NULL,
    UNIQUE(skill_id, version_number),
    UNIQUE(skill_id, package_hash),
    UNIQUE(skill_id, git_commit)
);
```

`source_type` 是审计字符串；当前主路径写入 `initial`、`import` 和
`optimizer_patch`。回滚直接把 Binding 指回已有 commit，不创建内容相同的
`rollback_copy`。

`SkillPackageVersion.status` 当前表示制品生命周期，主路径使用 `candidate`、
`active` 和 `rejected`。`provisional`/`validated` 不是版本 status，而是
`SkillTargetBinding.active_level`；同一不可变版本在不同 Target 上可有不同验证级别。
Version status 也不是“是否仍是某一 Target 当前 Active”的权威判断；权威来源是
Binding 及其 History。

内部 Git 约束：

- 每个 Skill 使用独立 bare repository，当前路径为
  `<managed_root>/repositories/<skill-id>.git`；
- 仓库由 AnalystBench 创建，不能把 `source_path` 或 AnalystBench 工作树直接 `git init`；
- 导入流程先复制到临时 staging、完成安全检查与规范化哈希，再写入内部仓库；
- commit author 使用固定服务身份，commit message 只包含版本号、来源类型和 Patch hash，不包含日志或标准答案；
- `package_hash` 是领域身份，Git commit/tree 用于 Diff 和物化；相同 `package_hash` 必须复用已有版本；
- 删除数据库记录时不自动执行 Git 历史重写；归档和保留策略单独处理。

### 5.3 skill_target_bindings

```sql
CREATE TABLE skill_target_bindings (
    id UUID PRIMARY KEY,
    skill_id UUID NOT NULL REFERENCES skills(id),
    evaluation_target_id UUID NOT NULL,
    active_version_id UUID NOT NULL REFERENCES skill_package_versions(id),
    active_level VARCHAR(32) NOT NULL,
    lock_version INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL,
    UNIQUE(skill_id, evaluation_target_id)
);
```

除表级 `UNIQUE(skill_id, evaluation_target_id)` 外，V1 Registry Service 还在首次绑定的
原子事务内检查目标 Target 是否已绑定其他 Skill；若已存在，以
`evaluation_target_skill_binding_conflict` 拒绝。因此一个 Target 在 V1 最多有一个
Active Skill，普通评测不存在多 Skill 隐式选择。

Active 更新必须乐观锁：

```sql
UPDATE skill_target_bindings
SET active_version_id = :candidate_id,
    active_level = :candidate_level,
    lock_version = lock_version + 1,
    updated_at = NOW()
WHERE id = :binding_id
  AND active_version_id = :expected_base_id
  AND lock_version = :expected_lock_version;
```

影响行数为 0 时以 `skill_binding_conflict` 拒绝。

### 5.3.1 evaluation_variants

```sql
CREATE TABLE evaluation_variants (
    id UUID PRIMARY KEY,
    evaluation_target_id UUID NOT NULL REFERENCES evaluation_targets(id),
    skill_package_version_id UUID NOT NULL REFERENCES skill_package_versions(id),
    materialized_method_id UUID NOT NULL REFERENCES evaluation_methods(id),
    install_relative_path TEXT NOT NULL,
    invoke_as VARCHAR(128) NOT NULL,
    content_hash VARCHAR(71) NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(evaluation_target_id, skill_package_version_id)
);
```

冻结 Variant 时：

1. 读取 Target 的 Harness/Model/已物化 Method 快照；
2. 加入 Skill version、安装路径、内部 Git commit 和 package hash；
3. 创建或复用只用于该 Variant 的 `EvaluationMethod` 执行快照；
4. Method 命令本身仍是安全 argv 模板，Skill 安装由运行前 Adapter 完成，不通过 Shell `cp` 或命令前缀拼接；
5. 重复冻结相同 Target × SkillVersion 必须幂等复用；SQLite 使用现有 `BEGIN IMMEDIATE` 模式保护物化事务。

无 Skill 的 Target 不创建伪 SkillVersion，继续沿用现有执行路径。

### 5.4 optimizer_policy_versions

```sql
CREATE TABLE optimizer_policy_versions (
    id UUID PRIMARY KEY,
    policy_key VARCHAR(128) NOT NULL,
    version_number INTEGER NOT NULL,
    execution_profile_id UUID NOT NULL REFERENCES execution_profiles(id),
    prompt_bundle_hash VARCHAR(71) NOT NULL,
    config_json JSON NOT NULL,
    content_hash VARCHAR(71) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(policy_key, version_number)
);
```

优化器调用属于“生成 Skill Patch 的 Agent”，不是被测 `EvaluationModel`。V1 复用冻结
`ExecutionProfile` 表达 claude 的可执行文件、模型、Prompt 环境和权限；
`OptimizerPolicyVersion` 再冻结本功能专属 Prompt bundle 和编辑策略。

配置示例：

```json
{
  "prompt_bundle": {
    "instruction": "Use only the supplied Train evidence and propose small general patches."
  }
}
```

当前 pipeline 从 `config_json.prompt_bundle.instruction` 读取冻结基础指令。
`candidate_count`、`max_epochs` 和重复次数进入 Experiment 的
`config_snapshot_json`；Patch 操作和预算属于 Verifier 的
`static_policy_json`，不应混写成 Optimizer Policy 已消费字段。

### 5.5 verifier_bundle_versions

```sql
CREATE TABLE verifier_bundle_versions (
    id UUID PRIMARY KEY,
    bundle_key VARCHAR(128) NOT NULL,
    version_number INTEGER NOT NULL,
    static_policy_json JSON NOT NULL,
    gate_policy_json JSON NOT NULL,
    judge_config_json JSON NOT NULL,
    content_hash VARCHAR(71) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(bundle_key, version_number)
);
```

### 5.6 optimization_experiments

```sql
CREATE TABLE optimization_experiments (
    id UUID PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    skill_id UUID NOT NULL REFERENCES skills(id),
    base_skill_version_id UUID NOT NULL REFERENCES skill_package_versions(id),
    evaluation_target_id UUID NOT NULL,
    data_snapshot_id UUID NOT NULL REFERENCES optimization_data_snapshots(id),
    optimizer_policy_version_id UUID NOT NULL,
    verifier_bundle_version_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL,
    current_epoch_number INTEGER NOT NULL DEFAULT 0,
    max_epochs INTEGER NOT NULL,
    stop_reason VARCHAR(128),
    created_by VARCHAR(128),
    created_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    config_snapshot_json JSON NOT NULL,
    error_json JSON NOT NULL
);
```

Experiment 当前持久化状态是 `created`、`running`、`completed`、`failed` 和
`cancelled`。基线、生成、Screening 与 Validation 的细粒度阶段保存在
Epoch/Candidate/Run Group 状态和事件中，不是 Experiment 的独立状态值。

V1 用一个 `OptimizationDataSnapshot` 同时冻结 Benchmark 内容身份和 split，
持久化字段是 `data_snapshot_id`；早期文稿中分开的 `benchmark_snapshot_id` /
`split_snapshot_id` 不是当前实现字段。不能把可变化的 `dataset_key` 或当前目录扫描
结果直接当实验身份。

### 5.6.1 optimization_data_snapshots

```sql
CREATE TABLE optimization_data_snapshots (
    id UUID PRIMARY KEY,
    dataset_key VARCHAR(255) NOT NULL,
    mode VARCHAR(32) NOT NULL,
    train_cases_json JSON NOT NULL,
    validation_cases_json JSON NOT NULL,
    hidden_test_cases_json JSON NOT NULL,
    prospective_holdout_cases_json JSON NOT NULL,
    case_input_hashes_json JSON NOT NULL,
    eval_spec_hashes_json JSON NOT NULL,
    content_hash VARCHAR(71) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL
);
```

`mode`：

- `development_regression`：当前 4 Case 均可用于证据和门禁，结果必须标记 `provisional`；
- `independent_validation`：Train 与 Validation 独立；达到最少独立 Validation
  Case 且统计 Gate 通过时允许产生 `validated` Active。该模式强制
  `max_epochs=1`，且同一 Snapshot 只能被一个成功启动的 Experiment 原子消费，避免把同一
  Validation 反复变成调参集。早期设计中的
  `validation_gated` 对应当前实现的这个名称，不是第三种运行模式。

当前实现只接受上述两种 `mode`。Snapshot 可以登记 Hidden Test 和
Prospective Holdout 并冻结其哈希，但闭环不会自动调度它们；早期设计中的
`publication` 是私有最终实验阶段，不是当前 API 可选 mode。

同一原始事件的不同裁剪必须通过 `source_group_key` 归入同一集合。Snapshot
创建后不可新增 Case。系统不会将新 Case 自动放入 Prospective；由用户在新
Snapshot 中显式分配为 `prospective_holdout` 或下一次正式切分。创建
`independent_validation` Snapshot 时 Validation 数不足最小值会直接失败，不会
创建一个“仅描述性、但可运行”的独立 Snapshot。

### 5.7 optimization_epochs

```sql
CREATE TABLE optimization_epochs (
    id UUID PRIMARY KEY,
    experiment_id UUID NOT NULL REFERENCES optimization_experiments(id),
    epoch_number INTEGER NOT NULL,
    parent_skill_version_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL,
    evidence_summary_json JSON NOT NULL,
    summary_json JSON NOT NULL,
    best_candidate_version_id UUID,
    decision VARCHAR(32),
    created_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    UNIQUE(experiment_id, epoch_number)
);
```

Epoch 当前状态：`collecting_evidence`、`generating_candidates`、`screening`、
`full_validating` 和 `completed`。早期名称 `generating`/`validating` 仅为恢复
兼容分支，新 Epoch 不写入它们。

### 5.8 candidate_mutations

```sql
CREATE TABLE candidate_mutations (
    id UUID PRIMARY KEY,
    epoch_id UUID NOT NULL REFERENCES optimization_epochs(id),
    parent_skill_version_id UUID NOT NULL,
    candidate_skill_version_id UUID,
    candidate_type VARCHAR(32) NOT NULL,
    structured_patch_json JSON NOT NULL,
    patch_hash VARCHAR(71) NOT NULL,
    rationale TEXT,
    intended_failure_clusters_json JSON NOT NULL,
    intent_json JSON NOT NULL,
    change_stats_json JSON NOT NULL,
    evidence_refs_json JSON NOT NULL,
    status VARCHAR(32) NOT NULL,
    rejection_code VARCHAR(128),
    rejection_detail_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
```

`candidate_type` 当前记录 `structured_patch_<index>`；纠错/证据强化/精简/
工具增强是 `intent_json.change_type`，不是 `candidate_type`。状态主路径为
`validated_static`、`screening`、`screening_passed`、`screening_selected`、
`validating`、`needs_more_runs`、`accepted` 或 `rejected`。

### 5.9 optimization_signals

```sql
CREATE TABLE optimization_signals (
    id UUID PRIMARY KEY,
    experiment_id UUID NOT NULL,
    epoch_id UUID,
    case_path TEXT NOT NULL,
    evaluation_method_run_id UUID NOT NULL,
    run_role VARCHAR(16) NOT NULL,
    case_family VARCHAR(128),
    score DECIMAL(8,3),
    signal_json JSON NOT NULL,
    signal_hash VARCHAR(71) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(evaluation_method_run_id)
);
```

### 5.10 candidate_comparisons

```sql
CREATE TABLE candidate_comparisons (
    id UUID PRIMARY KEY,
    experiment_id UUID NOT NULL,
    epoch_id UUID NOT NULL,
    candidate_mutation_id UUID NOT NULL,
    comparison_type VARCHAR(32) NOT NULL,
    metrics_json JSON NOT NULL,
    gate_result_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
```

### 5.10.1 optimization_run_groups

V1 不修改 `EvaluationSubmissionMethodRun` 的唯一约束来硬塞重复次数，而是让每个 arm × repeat 使用一个底层 `EvaluationSubmission`，由 Run Group 聚合：

```sql
CREATE TABLE optimization_run_groups (
    id UUID PRIMARY KEY,
    experiment_id UUID NOT NULL,
    epoch_id UUID,
    candidate_mutation_id UUID,
    split_role VARCHAR(32) NOT NULL,
    arm VARCHAR(32) NOT NULL,
    skill_package_version_id UUID NOT NULL,
    repeat_index INTEGER NOT NULL,
    evaluation_submission_id UUID NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL,
    run_config_hash VARCHAR(71) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(experiment_id, epoch_id, candidate_mutation_id, split_role, arm, repeat_index),
    UNIQUE(experiment_id, run_config_hash)
);
```

`0018_evaluation_submission_idempotency` 还为底层 `evaluation_submissions` 增加可空、唯一的
`idempotency_key`。优化编排使用 `skillopt:<run_config_hash>`：Worker 在 Run Group 写入前
崩溃时，重试底层 Submission 创建会复用同一批次，不再因只有 Run Group
唯一约束而重复调用模型。

底层 Submission 增加可空的 `purpose` 和 `optimization_context_json`：

```text
purpose = normal | skill_optimization
optimization_context = experiment/epoch/candidate/arm/repeat/split
```

`skill_optimization` Submission：

- 复用现有 Case 复制、Method Run、stdout 报告、评分和 Worker；
- 评分 `result.json` 强制 `included_in_statistics=false` 与
  `result_purpose=skill_optimization`，不进入普通总览、正式排行榜或普通结果列表；
- 底层 Submission 默认不出现在普通 Submission 列表；普通取消/删除 API 会拒绝它，
  只能通过 Optimization Experiment 状态机取消和管理；
- 每次运行冻结 `EvaluationVariant` 和 Skill package hash；
- 基线与候选使用不同 Submission，不能共享可写运行目录。

### 5.11 decision_records

```sql
CREATE TABLE decision_records (
    id UUID PRIMARY KEY,
    experiment_id UUID NOT NULL,
    epoch_id UUID,
    candidate_mutation_id UUID,
    diagnosis_json JSON NOT NULL,
    revision_json JSON NOT NULL,
    evidence_json JSON NOT NULL,
    outcome_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
```

---

## 6. Skill 包存储与哈希

推荐管理目录：

```text
<managed_root>/
├── repositories/
│   └── <skill-id>.git/
└── tmp/
```

设置层在未显式配置时仍有
`<workspace_root_path>/skill-optimization` 兼容派生值，但启用自优化时的严格预检
要求 `ANALYSTBENCH_SKILL_OPTIMIZATION_MANAGED_ROOT` 显式配置为已存在、
可写的绝对路径。该目录不得指向用户源 Skill、AnalystBench 源码仓库、
`results` 或 Worker workspace。大文本和结构化摘要继续进入现有
`ContentStore`。

### 6.1 包哈希

V1 新导入使用 `analystbench.skill-package.v2`。步骤为：

1. 遍历纳入版本的普通文件；
2. 相对路径统一为 `/`；
3. 按路径排序；
4. 不跟随符号链接；
5. 拒绝设备文件、FIFO、Socket 以及任何 setuid/setgid 位；
6. 每个文件计算 SHA-256；
7. 将源权限归一为非可执行 `0644` 或保留可执行语义的 `0755`，并把
   `path + normalized mode + size + file_hash` 序列化为 canonical JSON；
8. manifest 同时冻结 `ignored_paths` 规则：忽略目录名、文件名和文件
   后缀的排序清单；
9. 对完整 canonical manifest JSON 再计算 SHA-256。

哈希基于原始 bytes，不自动修改换行。
这使执行位成为制品身份的一部分：同样 bytes 但脚本从不可执行变为可执行，
`package_hash` 会变化。物化后包整体只读，但保留执行语义：非执行文件为 `0444`，
可执行文件为 `0555`，目录为 `0555`。旧 v1 manifest 物化时按其不含 mode 的旧哈希
规则复核，以保持已存版本兼容；新导入不再生成 v1。

内部 Git 提交前计算上述哈希；checkout/导出/物化后必须按版本 manifest
的 format 重新计算并与数据库 `package_hash` 一致。Git commit hash 不能替代包哈希，
因为 commit 还包含父提交、作者和时间。

### 6.2 文件安全

必须拒绝：`../`、绝对路径、指向包外的符号链接、超限文件、设备节点、FIFO、Socket、setuid/setgid。导入时忽略或拒绝 `.git/`、`.svn/`、缓存目录、编辑器临时文件和运行产物，具体规则进入冻结 Manifest。

---

## 7. Skill 配置

```json
{
  "key": "kernel-log-analysis",
  "name": "Kernel Log Analysis",
  "source_path": "<frozen-harness.skill_base_dir>/skills/kernel-log-analysis",
  "invoke_as": "/kernel-log-analysis",
  "harness_key": "claude-skill",
  "install_relative_path": ".claude/skills/kernel-log-analysis",
  "editable_paths": [
    "SKILL.md",
    "references/**",
    "scripts/**",
    "tests/**"
  ],
  "publish_mode": "managed",
  "limits": {
    "max_files": 200,
    "max_total_bytes": 2097152,
    "max_single_file_bytes": 262144,
    "max_skill_tokens": 12000
  }
}
```

V1 不自动同步 `source_path`。后续可增加 `git` 或 `explicit_sync`。

普通 UI 注册时不直接收集上述三个路径字段，而是使用冻结 Harness 和 Skill Key
确定以下映射；底层 API 仍保存解析后的绝对源路径作为导入审计事实：

```text
source_path           = <harness.skill_base_dir>/skills/<skill-key>
invoke_as             = /<skill-key>
install_relative_path = .claude/skills/<skill-key>
```

`invoke_as` 是 Prompt 中的调用名，`install_relative_path` 是文件系统位置，两者
语义不同；V1 产品契约只是让它们共同由同一个 Skill Key 派生，不能在运行时把
一个字段当作另一个字段使用。`skill_base_dir` 必须来自与 Target 对应的冻结
Harness，且解析后的源目录必须通过包与路径安全检查。

---

## 8. 运行隔离

每次 Evaluation Run：

```text
<run_root>/
├── home/
├── workspace/
│   ├── .claude/
│   │   └── skills/
│   │       └── kernel-log-analysis/
│   └── logs/
└── home/tmp/
```

`run_root` 是临时执行目录，命令结束后会清理；正式报告、评分和审计制品按
Evaluation Submission 结果路径持久化，不依赖临时 `run_root/artifacts`。

环境变量：

```bash
HOME=<run_root>/home
XDG_CONFIG_HOME=<run_root>/home/.config
XDG_CACHE_HOME=<run_root>/home/.cache
CLAUDE_CONFIG_DIR=<run_root>/home/.config/claude
ANALYSTBENCH_SKILL_VERSION_ID=<uuid>
```

Harness Adapter 从内部 Git 的指定 commit 导出 Skill 包，验证 `package_hash`，再复制到 `<workspace>/<install_relative_path>`。目标 Agent 的 cwd 是 `<workspace>`，因此类似 `claude -p "/kernel-log-analysis 分析 logs/..."` 的命令可通过项目级 Skill 发现机制加载该版本。

禁止通过覆盖共享 `~/.claude/skills` 或其他全局目录实现候选切换。也禁止把用户完整 `.claude/` 复制进工作区，因为其中可能包含 settings、hooks、plugins、MCP、认证引用和与实验无关的 Skill。若 Harness 只能发现全局 Skill，必须先扩展 Adapter 或使用隔离 HOME；不得暂时覆盖后再恢复。

运行期间已安装 Skill 包按文件权限只读；缓存写入独立临时目录；候选
生成器与目标 Agent 不共用 HOME。当前的 HOME/XDG 重定向是“进程环境与用户
状态隔离”，不是 mount namespace：它不阻止按 Worker 操作系统权限通过绝对路径
访问其他文件，也不自动断网。因此不应在文档或验收中把它称为完整沙箱；
只有第 13.2 节声明式包内测试使用 bubblewrap 无网络 namespace。

隔离 HOME 不会复制服务用户真实 HOME 中的 CLI 登录。凭据应由 Worker 显式继承的
环境变量或受控 wrapper 提供；不复制完整 `.claude/`、hooks、plugins 或未声明
Skill。`--version` 探测只证明 CLI 可执行，不证明认证可用。

---

## 9. OptimizationSignal

```json
{
  "schema_version": "1.0",
  "case_id": "case-xxx",
  "case_revision_id": "uuid",
  "case_family": "hungtask-lock-contention",
  "run_id": "uuid",
  "score": 82.5,
  "dimension_scores": {
    "error_type": 10,
    "root_cause": 15,
    "evidence": 20,
    "timeline": 8,
    "recommendation": 7
  },
  "failure_tags": ["MISSING_ROOT_CAUSE", "EVIDENCE_NOT_BOUND"],
  "missing_claims": [],
  "wrong_claims": [],
  "unsupported_claims": [],
  "evidence_errors": [],
  "preserve_behaviors": [],
  "tool_failures": [],
  "format_failures": [],
  "judge_confidence": 0.86,
  "source_refs": {
    "evaluation_submission_id": "uuid",
    "evaluation_method_run_id": "uuid",
    "result_path": "relative/path/result.json",
    "trace_uri": null
  }
}
```

V1 Failure Tags：

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
OTHER
```

信号生成优先级：程序化结果 > Eval Spec 字段结果 > Judge 结构化输出 > 对 Judge 自然语言结果的二次抽取。不得只用总分生成信号。

当前 Evaluation Submission 链路的稳定事实源是 `run.json`、`result.json`、生成报告和 `EvaluationSubmissionMethodRun.artifact_json`，并没有保证存在 `candidate_report_id`、`judge_result_id` 或结构化 Tool Trace。V1 必须先适配这些实际产物；`trace_uri` 仅在具体 Harness 明确导出可解析 Trace 时填写，否则为 `null`，不能从 stderr 或自然语言日志伪造工具轨迹。

---

## 10. 优化器输入

```text
optimizer-input/
├── current-skill/
├── skill-summary.json
├── failure-clusters.json
├── success-patterns.json
├── rejected-history.json
├── editable-paths.json
├── edit-budget.json
└── optimizer-instruction.md
```

`failure-clusters.json`：

```json
[
  {
    "cluster_id": "cluster-1",
    "tags": ["EVIDENCE_NOT_BOUND"],
    "case_family_distribution": {
      "hungtask-lock-contention": 4,
      "scheduler-latency": 2
    },
    "support_count": 6,
    "representative_signals": ["signal-id-1", "signal-id-2"],
    "summary": "结论正确但未将根因 Claim 绑定到明确日志证据"
  }
]
```

`success-patterns.json`：

```json
[
  {
    "pattern_id": "preserve-1",
    "support_count": 8,
    "summary": "能够区分最终重启触发者和原始故障根因",
    "source_refs": ["signal-id-5", "signal-id-7"]
  }
]
```

Rejected History 根据当前 failure tags 和 case family 检索最多 10 条，不把全部历史塞进上下文。

---

## 11. Structured Patch

### 11.1 Schema

当前输出协议身份是 `structured_skill_patch.v1`。Role envelope 只允许
`role`、`prompt_version`、`findings` 和 `patches`；其中每个 patch 的顶层
只允许 `rationale`、`intent` 和 `operations`。一个合法 patch 例如：

```json
{
  "rationale": "强化锁等待者与锁持有者的区分",
  "intent": {
    "change_type": "corrective",
    "target_failure_families": ["hungtask-lock-contention"],
    "target_dimensions": ["root_cause", "evidence"],
    "target_failure_tags": ["EVIDENCE_NOT_BOUND"],
    "protected_behaviors": ["保留已正确的重启触发者判定"]
  },
  "operations": [
    {
      "op": "insert_after",
      "path": "SKILL.md",
      "anchor": "## 锁与阻塞分析",
      "content": "\n### 等待者与持有者判定\n..."
    },
    {
      "op": "replace",
      "path": "SKILL.md",
      "old": "block LOCK 表示线程持有锁",
      "new": "block LOCK 只表示线程因锁阻塞，需结合 owner 或调用栈判断持锁者"
    }
  ]
}
```

### 11.2 操作

V1 仅支持 `append`、`insert_after`、`replace`、`delete`。
`create`、`old_text`、unified diff、shell 命令和 schema 外字段都会被拒绝。

规则：path 必须匹配 editable paths；`anchor` 或 `old` 必须在目标
文件中精确且唯一命中；`append` 和其他文本操作只能作用于已存文件；
操作按顺序在临时副本中执行，任一步失败则不创建候选版本；完成后
重新计算包哈希，相同哈希复用已有版本，并保留 before/after diff 和
变更统计。

### 11.3 编辑预算

```yaml
max_operations: 4
max_changed_files: 2
max_added_tokens: 600
max_deleted_tokens: 300
max_single_file_change_ratio: 0.25
```

超限错误码：`edit_budget_exceeded`。

---

## 12. 证据分析与候选策略

V1 已实现四个固定角色，由同一个冻结 claude Optimizer Profile
使用不同的显式版本 Prompt 依次运行：

```text
failure_analyst
success_analyst
generalization_analyst
simplification_analyst
```

每个角色只接收有界的 Train/Development 聚合证据和同实验的
Rejected History，不接收 Validation、Hidden、Holdout、原始日志、标准答案或
报告全文。输出必须匹配严格 JSON envelope 和
`structured_skill_patch.v1`；非法 JSON 只允许使用同一 Runner 做一次格式修复，
修复后仍无效则记录该角色错误。`AgentRunnerError` 每次调用最多三次尝试，
失败间隔为 1/2 秒指数退避。

合并器按固定角色顺序对各角色的第 1 个提案、第 2 个提案……
做 round-robin，按 canonical patch hash 去重，取到 `candidate_count`
为止。当前实现不做语义相似度合并，不应将其写成已有能力。

默认每轮取两个去重后的候选。候选的 `intent.change_type` 可为
`corrective`、`simplification`、`evidence_strengthening` 或
`tool_enhancement`，具体顺序由固定角色 round-robin 和 canonical hash
去重结果决定，不强制“第一个纠错、第二个精简”。

---

## 13. 静态验证

固定顺序：

```text
patch_schema
editable_path
patch_apply
package_structure
content_security_scan
case_leak_scan
token_size
referenced_file_check
script_syntax
package_tests
```

### 13.1 内容扫描

至少检查：API Key、Private Key、绝对用户路径、Windows 用户目录、标准答案片段、大量 Case ID 硬编码、要求读取隐藏测试、网络外传、越权修改系统 Prompt。

### 13.2 包内测试

包内测试只在 `manifest.json` 显式声明 `package_tests.argv` 时执行；仅存在
`tests/` 不触发隐式测试。声明时 `tests/` 必须存在，argv 必须匹配受限
Python/pytest 前缀、不经 shell，且 timeout 不能超过 Verifier 上限。平台把临时 Skill
副本放入 bubblewrap namespace，清空环境，只读绑定运行时和守卫代码，禁止网络与
子进程。测试失败、`bwrap` 缺失或当前 WSL/容器不允许 namespace 时，候选
在进入 Screening 前静态拒绝。若未声明测试，允许通过但记录
`not_configured`。需要真实 namespace 的 E2E 默认跳过，私有宿主使用
`ANALYSTBENCH_RUN_BWRAP_E2E=1` 显式启用。

---

## 14. Evaluation Submission 扩展

当前真实执行单位是 `EvaluationSubmission → CaseRun → MethodRun`。V1 增加
Submission `purpose`，Run Group 不使用单一 `run_role` 字段，而是保存
`split_role × arm`：

```text
split_role = screening | validation
arm        = baseline | candidate
```

每个 Run Group 绑定 `skill_package_version_id`、`experiment_id`、
`epoch_id`、可空 `candidate_mutation_id`、`repeat_index` 和
`run_config_hash`。Validation 每个 repeat 根据
`experiment_id + epoch_id + candidate_mutation_id + repeat_index` 生成稳定
`pair_seed`，Seed 决定 baseline→candidate 或 candidate→baseline 的创建顺序；
`pair_seed` 和 `pair_position` 进入冻结 run config hash 与 Submission context。
当前数据库没有 `pair_key` 列。底层 Submission Manifest 必须包含同一
快照，Method Run artifact 再保存实际安装路径、package hash 和内部
Git commit。Hidden/Prospective 不在当前优化编排中生成 Run Group。

历史 Run 可保持 SkillVersion 为 NULL，并显示 `legacy/unfrozen`。

---

## 15. 基线缓存

缓存键必须包含：

```text
parent_skill_version_id
evaluation_target_id
data_snapshot_id
verifier_bundle_version_id
run_config_hash
repeat_count
```

任一 Skill、Model、Harness、Benchmark、Case Revision、Eval Spec、Judge、运行参数或环境镜像变化均不得复用。

当前实现按 `experiment_id + epoch_id + candidate_mutation_id + repeat_index`
计算稳定 `pair_seed`，每个 repeat 由 Seed 决定创建顺序为 baseline→candidate 或
candidate→baseline，并把 `pair_seed`/`pair_position` 写进冻结 run config hash 与
Optimization Context。这提供可复现的交错调度；外部 CLI/模型服务仍可有时间漂移，
因此私有实验仍应记录运行时间和并发配置。

V1 的“缓存”只用于同一 Experiment、同一 Epoch 和同一目标 repeat 的中断恢复，不跨 Experiment 复用历史分数。原因是当前本地 Harness、外部 CLI、模型服务和工作目录内容无法形成足够强的环境指纹。每个 Epoch 的基线必须是该 Epoch 的 `parent_skill_version_id`：

```text
Epoch 1: baseline v1 vs candidate v2
v2 promoted
Epoch 2: baseline v2 vs candidate v3
```

不得让后续候选始终与实验初始 v1 比较。若某个已完成 Run Group 的 Manifest、输出哈希和状态完整，Worker 重启后可以幂等复用该组，而不是重新调用模型。

---

## 16. Screening 与 Full Validation

`development_regression` 可以逐 Epoch 更新 Active 并在下一轮使用新基线。
`independent_validation` 不执行这种多轮适应：前端锁定且后端强制
`max_epochs == 1`，同一独立 Snapshot 只能被一个已启动 Experiment 消费。

### 16.1 Screening

- `independent_validation` 使用 Train 子集，不使用 Validation；`development_regression` 在当前四 Case
  阶段让全部开发 Case 参与 Evidence 和固定 Screening，Case 增长后再由冻结快照
  显式指定开发 Screening 子集；
- 每 Case 一次；
- 不做 Bootstrap；
- 只检查硬约束和粗粒度 Delta；
- 最多保留一个候选进入完整验证。

拒绝条件：新增执行失败；平均 Delta < -1；关键维度显著下降；Unsupported Claim 上升超过阈值；中位耗时增长超过 50%。

### 16.2 Full Validation

- `independent_validation` 模式使用独立 Validation 全集；
- `development_regression` 模式使用当前四个开发 Case，但结果标记为样本内和 `provisional`；
- 基线和候选每 Case 三次；
- 按 Case 取中位数；
- 执行完整 Gate；
- 灰区只在同一 Validation/Development Case 上增加到五或七次。

### 16.3 灰区

以下任一成立：Overall Delta 在 `[0, min_gain)`；Bootstrap 下界不大于 0 但胜率大于 0.55；关键维度接近阈值；运行方差超过配置值。达到最大次数仍不确定则拒绝：`INCONCLUSIVE_AFTER_MAX_REPEATS`。

### 16.4 Hidden Test

- 不参与 Screening、Full Validation、灰区增采样或 Epoch 选择；
- 优化器、Evidence Builder 和 Rejected Buffer 均不能读取 Hidden Test 日志、答案、逐 Case 分数或失败标签；
- 当前系统只冻结和隔离 Hidden Test，不自动运行；应在最终版本、统计方案和固定重复次数预注册后，由用户在私有环境另行运行；
- 一旦用于决定继续修改 Skill，该集合即失去 Hidden Test 身份，必须登记为已暴露并更换新快照。

### 16.5 当前四 Case 模式

当前只有 4 个 Case，不能把 `2/1/1` 机械切分后声称得到可靠 Train、Validation 和 Test。默认配置：

```json
{
  "split_mode": "development_regression",
  "train_case_count": 0,
  "validation_case_count": 4,
  "optimizer_visible_case_count": 4,
  "hidden_test_case_count": 0,
  "repeats": 3,
  "promotion_label": "provisional"
}
```

四个 Case 在持久化结构中登记为 Validation，但 `development_regression` 会明确
把它们同时作为优化器可见的开发 Case；Gate 的职责是防止已知 Case 回归并判断
样本内是否改善。后续新增 Case 不会自动进入任何 split；用户应在新 Snapshot
中显式先放入 `prospective_holdout`。积累到足以
覆盖多个独立故障家族后，再由用户冻结正式切分并启用
`independent_validation`。

---

## 17. Promotion Gate

### 17.1 配置

```json
{
  "min_overall_delta": 1.0,
  "max_latency_growth": 0.20,
  "max_token_growth": 0.20,
  "critical_dimension_min_delta": 0.0,
  "critical_family_max_regression": -2.0,
  "minimum_independent_validation_cases": 8,
  "bootstrap_samples": 2000,
  "bootstrap_confidence": 0.95,
  "min_candidate_win_probability": 0.0,
  "require_bootstrap_lower_bound_positive": true
}
```

这些字段和完整 `judge_config` 在 `VerifierBundleVersion` 中不可变冻结。Judge runner
及 configuration 进入 Run Group config hash；Bootstrap 样本数/置信度进入实际比较，
根据 Experiment/Epoch/Candidate 身份导出稳定 Seed，并把最终整数 Seed 保存在
Comparison 与 Gate metrics 中。

### 17.2 判定顺序

1. 数据完整性；
2. 执行成功硬约束；
3. 关键质量硬约束；
4. 性能和成本硬约束；
5. Overall Delta；
6. 统计置信度；
7. 输出 `promote/reject/needs_more_runs`。

硬约束失败不得被总分抵消。

当前 Method Run artifact 对最终 stdout 持久化确定性估算：
`token_count=ceil(output_character_count/4)`，同时记录
`token_count_source=approximate_output_characters`。该量度只代表输出报告规模，不冒充
provider 输入+输出 usage 或账单 Token。Full Gate 以每个 Case 重复运行中位数构成配对，
然后比较基线/候选跨 Case 平均值的增长；任一 pair 缺 usage 以
`token_usage_missing` 硬拒绝，超阈值以 `token_growth_exceeded` 硬拒绝。

`forbidden_hit_count` 和 `missing_chain_count` 也会在每个 Case 上对重复运行取中位数。
候选的任意一项高于基线即以 `candidate_guardrail_metric_increased` 硬拒绝；
该判定不只依赖布尔 Failure Tag，也不允许被 Overall Delta 抵消。

门禁模式：

- `development_regression`：执行质量与稳定性硬约束、最小 Delta 和逐 Case 回归检查；Bootstrap 仅展示，不以 4 个 Case 声称统计显著；通过后只产生 `provisional` Active；
- `independent_validation`：达到 `minimum_independent_validation_cases` 后，才启用 Bootstrap 下界/胜率作为自动发布必要条件；
- 私有 publication 阶段：只报告冻结 Final Version 的 Hidden Test 结果，不执行 Promotion；当前闭环不自动调度该阶段。

### 17.3 配对计算

```python
baseline_case_score = median(baseline_repeats)
candidate_case_score = median(candidate_repeats)
case_delta = candidate_case_score - baseline_case_score
overall_delta = mean(case_deltas)
```

Bootstrap 以 Case 为重采样单元，不能以单次 Run 为单元。

### 17.4 原子 Promotion

单事务执行：校验 Active 未变化；写 DecisionRecord；更新 Binding 的
`active_version_id` 和 `active_level`；标记 Candidate accepted、版本制品 status 为
`active`；写 Binding History 和持久化事件；提交。`provisional`/`validated`
保存在 Binding 上，不写进 Version status。V1 前端通过轮询读取事件记录；失败可幂等
重试。

### 17.5 Epoch 总账与 Active Path Score

每个终态 Epoch 必须冻结选中候选的意图、实际 Patch/Diff 统计、静态结果、
Baseline/Candidate/配对 Delta、逐 Case/Family/Dimension 变化、Gate 原因和
Active 决策。所有候选保留在 JSON 总账；Markdown/CSV 提供一轮一行的主路径摘要，
不代替候选详情。

`ACTIVE PATH SCORE` 的精确定义是：

```text
initial baseline score + Σ(仅 decision=promote 的 Epoch paired delta)
```

Retained/拒绝候选不进入累计。该值是跨 Epoch 的 Active 路径审计量，不是对最终
Active 重新执行的独立绝对分；因每个 Epoch 的基线/候选采样独立且可有模型波动，
它不保证等于最后一轮 Candidate Score。研究报告必须保留各轮配对分数，并把最终
Hidden Test 分数单独报告。

若候选未进入 Full Validation，可保留带阶段标记的 Screening 比较用于诊断；它不能
触发 Promotion 或进入 Active Path 累计。完全缺少合法比较时字段为 `null`，不伪造
`0`。

---

## 18. 状态机

### 18.1 Experiment

```text
created
  ↓
running
  ├── epoch 1
  ├── epoch 2 ... (development_regression only)
  └── atomic promotion/retain decisions
  ↓
completed
```

任意运行态可进入 `failed` 或 `cancelled`。

### 18.2 Epoch

```text
collecting_evidence
  ↓
generating_candidates
  ↓
screening
  ↓
full_validating
  ↓
completed(decision=promote|retain|no_screening_survivor)
```

`created`、`reflecting`、`static_validating` 和 `deciding` 不是当前 Epoch
持久化状态。Candidate 另行记录 `validated_static`、`screening`、
`screening_passed`、`screening_selected`、`validating`、`needs_more_runs`、
`accepted` 或 `rejected`。

### 18.3 Early Stop

- 达到最大 Epoch；
- 连续两个 Epoch 无候选通过 Screening；
- 连续两个 Epoch 完整验证无提升；
- 达到目标分；
- 预算耗尽；
- 人工取消；
- 不可恢复基础设施错误。

当前自动终止使用 `MAX_EPOCHS`、`NO_SCREENING_SURVIVOR` 和
`NO_VALIDATION_IMPROVEMENT`；用户取消记录 `user_cancelled`，Optimizer
无法产生可用提案时记录 `optimizer_error`。目标分、总预算和通用基础设施
错误不是当前 Early Stop 的已实现原因码。

---

## 19. 任务队列与幂等

Job 类型：

```text
skill.import
experiment.freeze
experiment.run_baseline
experiment.build_evidence
experiment.reflect
experiment.generate_candidate
candidate.static_validate
candidate.screen
candidate.full_validate
candidate.gate
candidate.promote
experiment.finalize
```

幂等键示例：

```text
skill.import:<skill-id>:<package-hash>
baseline:<experiment-id>:<run-config-hash>
reflect:<epoch-id>:<evidence-hash>:<policy-version-id>
candidate:<epoch-id>:<candidate-index>:<patch-hash>
static:<candidate-id>:<verifier-version-id>
gate:<candidate-id>:<comparison-hash>:<gate-policy-hash>
promote:<binding-id>:<candidate-version-id>
```

重试：Optimizer 调用只在 `AgentRunnerError` 时最多执行三次，两次
重试前分别等待 1 秒和 2 秒；JSON 语法错误只允许一次同 Runner 格式修复。
Patch schema/预算非法、Gate Reject 不重试；Active 冲突终止实验，
需基于新 Active 重新决策。

---

## 20. API

### 20.1 Skill

```http
POST   /api/v1/skills
GET    /api/v1/skills
GET    /api/v1/skills/{skill_id}
POST   /api/v1/skills/{skill_id}/versions
GET    /api/v1/skills/{skill_id}/versions
GET    /api/v1/skills/{skill_id}/versions/{version_id}/export
GET    /api/v1/skills/{skill_id}/diff?from_version_id={id}&to_version_id={id}
GET    /api/v1/skills/{skill_id}/bindings
GET    /api/v1/skills/{skill_id}/binding-history
PUT    /api/v1/skills/{skill_id}/bindings
POST   /api/v1/skills/{skill_id}/bindings/{evaluation_target_id}/rollback
POST   /api/v1/evaluation-variants
```

创建请求：

```json
{
  "key": "kernel-log-analysis",
  "name": "Kernel Log Analysis",
  "source_path": "<frozen-harness.skill_base_dir>/skills/kernel-log-analysis",
  "invoke_as": "/kernel-log-analysis",
  "harness_key": "claude-skill",
  "install_relative_path": ".claude/skills/kernel-log-analysis",
  "editable_paths": ["SKILL.md", "references/**", "scripts/**", "tests/**"]
}
```

普通 UI 会按第 7 节派生 `name`、`source_path`、`invoke_as` 和
`install_relative_path`；这里保留完整底层 API 形态，供审计和脚本化调用。

### 20.2 Experiment

```http
POST   /api/v1/skill-optimization/policies
GET    /api/v1/skill-optimization/policies
POST   /api/v1/skill-optimization/verifiers
GET    /api/v1/skill-optimization/verifiers
POST   /api/v1/skill-optimization/data-snapshots
GET    /api/v1/skill-optimization/data-snapshots
POST   /api/v1/skill-optimization/experiments
GET    /api/v1/skill-optimization/experiments
GET    /api/v1/skill-optimization/experiments/{id}
POST   /api/v1/skill-optimization/experiments/{id}:start
POST   /api/v1/skill-optimization/experiments/{id}:resume
POST   /api/v1/skill-optimization/experiments/{id}:cancel
GET    /api/v1/skill-optimization/experiments/{id}/events
GET    /api/v1/skill-optimization/experiments/{id}/detail?epoch_offset=0&epoch_limit=20
GET    /api/v1/skill-optimization/experiments/{id}/ledger
GET    /api/v1/skill-optimization/experiments/{id}/export?format=json|markdown|csv
POST   /api/v1/skill-optimization/preflight
```

创建请求：

```json
{
  "name": "kernel-log-analysis-opt-001",
  "skill_id": "uuid",
  "base_skill_version_id": "uuid",
  "evaluation_target_id": "uuid",
  "data_snapshot_id": "uuid",
  "optimizer_policy_version_id": "uuid",
  "verifier_bundle_version_id": "uuid",
  "max_epochs": 5
}
```

### 20.3 Candidate

```http
GET  /api/v1/skill-optimization/candidates/{id}
```

候选详情响应统一包含 intent、change stats、Patch、Diff、静态验证、Screening
和完整比较。V1 不提供绕过 Gate 的手工 promote/reject API；Active 只能由
Promotion Service 的门禁晋升或显式 rollback 改变。

### 20.4 事件与前端更新

当前持久化事件名使用 snake_case：`evidence_built`、`candidate_generated`、
`candidate_static_rejected`、`candidate_screening_completed`、`candidate_gate_decided`、
`epoch_started`、`epoch_completed`、`epoch_summary_ready`、`skill_version_promoted`、
`experiment_resumed`、`experiment_cancelled`、`experiment_completed` 和
`experiment_failed`。前端还会读取实验/Epoch 当前 status，不依赖一个虚构的
`*.status_changed` 事件。

```json
{
  "event_id": "uuid",
  "event_type": "candidate_gate_decided",
  "experiment_id": "uuid",
  "epoch_id": "uuid",
  "candidate_id": "uuid",
  "timestamp": "2026-07-31T00:00:00Z",
  "payload": {}
}
```

V1 先把事件持久化，并沿用当前前端轮询模式读取实验和增量事件；不能把尚不存在的 SSE 基础设施写成前置依赖。后续可在不改变事件 Schema 的情况下增加 SSE。

---

## 21. 后端模块建议

```text
src/analystbench/
├── skill_optimization/
│   ├── __init__.py
│   ├── registry.py
│   ├── git_store.py
│   ├── package.py
│   ├── sandbox.py
│   ├── patch.py
│   ├── evidence.py
│   ├── statistics.py
│   ├── gate.py
│   ├── experiment.py
│   └── promotion.py
├── db/models.py
└── api/routes/
    ├── skills.py
    └── skill_optimization.py

tests/
├── test_skill_registry.py
├── test_skill_patch.py
├── test_skill_gate.py
├── test_skill_optimization.py
└── test_skill_optimization_api.py
```

Skill 自优化核心必须隔离在 `src/analystbench/skill_optimization/` 包中。旧模块只暴露小型 Protocol/Hook，不导入该包；API、应用工厂和 Worker 作为 composition root 注入 Adapter。`experiment.py` 只编排现有 `EvaluationSubmissionService`；`evidence.py` 转换现有 `result.json/run.json/artifact_json`；`gate.py` 和 `statistics.py` 必须尽量为纯函数；`patch.py` 不调用 LLM；`registry.py/git_store.py/package.py` 负责内部 Git 和路径安全。

---

## 22. 前端

### 22.1 页面

V1 不新增第五个主导航。Skill Registry 作为“设置”内的二级区域；Optimization Experiments 和详情作为“评测结果”内的二级区域，保留总览、测试集、评测结果、设置四个主视图。

**Skill Registry**：名称、key、源目录、安装相对路径、Active、绑定 Target、版本数、最近导入、最近优化、回滚。

**Optimization Experiments**：实验名、Skill、Base/Current、Target、Benchmark、Epoch、状态、最佳 Delta、成本、创建人和时间。

**Experiment Detail**：

```text
Header: 状态、Skill、Target、Benchmark、Base、Current Active
Progress: Freeze → Baseline → Epochs → Validation → Promotion
Epoch Timeline: 证据、候选、筛选、门禁
Candidate Compare: Patch、Overall、Dimension、Family、Runtime、Token
Decision History: diagnosis → revision → evidence → outcome
```

### 22.2 图表

- Overall 基线/候选；
- 各维度 Delta；
- 故障家族热力表；
- Epoch 分数趋势；
- 候选状态时间线；
- Token/Latency 对比。

### 22.3 目录建议

```text
src/frontend/src/
├── App.vue
├── app-options.js
├── api/
│   ├── skills.js
│   └── skill-optimization.js
└── components/skill-optimization/
    ├── SkillRegistryPanel.vue
    ├── ExperimentListPanel.vue
    ├── ExperimentProgress.vue
    ├── EpochTimeline.vue
    ├── CandidateDiff.vue
    ├── MetricDelta.vue
    ├── FamilyRegressionTable.vue
    └── DecisionHistory.vue
```

当前项目是 Vue 2 + Vuex 3，必须保持 Options API，不混入 Vue 3 Composition API。API 使用现有 Axios request 封装；实验状态沿用有界轮询并在终态停止。是否进一步拆分 `App.vue/app-options.js` 应作为独立重构，不与本功能强绑定。

---

## 23. 配置

配置进入现有 `analystbench.config.Settings`，通过 `ANALYSTBENCH_` 环境变量覆盖，不新增 YAML 配置事实源。建议字段：

```text
skill_optimization_enabled = false
skill_optimization_managed_root = <explicit-existing-writable-absolute-path>
skill_optimization_max_files = 200
skill_optimization_max_total_bytes = 2097152
skill_optimization_max_single_file_bytes = 262144
skill_optimization_max_skill_tokens = 12000
skill_optimization_max_epochs = 5
skill_optimization_candidate_count = 2
skill_optimization_validation_repeats = 3
skill_optimization_max_repeats = 7
skill_optimization_min_overall_delta = 1.0
skill_optimization_minimum_independent_validation_cases = 8
skill_optimization_max_latency_growth = 0.20
skill_optimization_max_token_growth = 0.20
skill_optimization_test_timeout_seconds = 120
```

编辑预算、Failure Tags、门禁和 Prompt 不应全部成为进程级 Settings；它们属于冻结的 `OptimizerPolicyVersion` 和 `VerifierBundleVersion`。Settings 只提供系统上限和默认值。非法配置必须在 API/Worker 启动时给出稳定错误，不能运行到候选阶段才失败。

---

## 24. Optimizer Prompt 契约

当前 V1 冻结 `OptimizerPolicyVersion.prompt_bundle/config`，并在运行时为四个
角色构造带明确版本的 Prompt：

```text
failure_analyst       = skill_optimizer.failure_analyst.v1
success_analyst       = skill_optimizer.success_analyst.v1
generalization_analyst = skill_optimizer.generalization_analyst.v1
simplification_analyst = skill_optimizer.simplification_analyst.v1
output schema         = structured_skill_patch.v1
pipeline              = four_role_optimizer.v1
```

每个 Prompt 定义 Train-only 证据边界、角色任务、严格 JSON
Schema 和禁止事项。模型返回非法 JSON 时只允许一次格式修复；仍失败
则记录该角色错误，不允许正则拼凑不可验证 JSON。四角色的输出概要、
错误和最终入选 patch hash 保存在 `optimizer_pipeline_completed` 事件中。

---

## 25. 统计实现

输入：

```python
case_results = [
    {
        "case_revision_id": "...",
        "family": "...",
        "baseline_scores": [80, 82, 81],
        "candidate_scores": [84, 83, 85]
    }
]
```

输出：

```json
{
  "pairs": [],
  "case_outcomes": [],
  "overall_delta": 2.4,
  "candidate_win_probability": 0.97,
  "bootstrap_confidence": 0.95,
  "bootstrap_interval": [0.6, 4.1],
  "bootstrap_seed": 123456789,
  "family_deltas": {},
  "dimension_deltas": {},
  "guardrail_metric_deltas": {}
}
```

Bootstrap Seed：

```text
seed_context = experiment_id + epoch_id + candidate_mutation_id
seed_material = seed_context + sorted(case_path + paired_delta)
bootstrap_seed = first_64_bits(sha256(seed_material))
```

结果中保存实际整数 Seed。`bootstrap_samples` 和 `bootstrap_confidence` 来自冻结
Verifier Gate Policy；以 Case Delta 为重采样单元，不以 repeat 作为独立样本。

---

## 26. 日志、指标和审计

结构化日志字段：`experiment_id`、`epoch_id`、`candidate_id`、`skill_id`、`skill_version_id`、`evaluation_target_id`、`job_type`、`state_from`、`state_to`、`duration_ms`、`retry_count`、`error_code`。

指标：

```text
skillopt_experiment_total{status}
skillopt_experiment_duration_seconds
skillopt_epoch_total{decision}
skillopt_candidate_total{status,type}
skillopt_candidate_acceptance_rate
skillopt_static_reject_total{reason}
skillopt_gate_reject_total{reason}
skillopt_rollout_total{role}
skillopt_optimizer_calls_total
skillopt_optimizer_tokens_total
skillopt_oracle_calls_total
skillopt_active_version_changes_total
skillopt_rollback_total
```

审计：Skill 导入、实验创建/启动/取消、候选生成、手动拒绝、自动/手动提升、回滚、Active 冲突、策略版本变化。

---

## 27. 安全

未来权限建议：

```text
skill.read
skill.manage
skill.import
skill.optimize
skill.promote
skill.rollback
skill.view_sensitive_trace
```

当前 API 没有通用认证/权限中间件，因此 V1 不能声称上述权限已经生效。V1 的安全边界是本地自托管、功能开关、路径白名单和显式危险操作确认；若要面向多用户部署，必须先建设统一认证授权，而不是只在 Skill API 内实现孤立权限判断。

数据脱敏：隐藏标准答案、身份、凭据和内部 URL；仅保留必要日志；证据使用稳定 ID；不可外发数据使用内网模型。

日志属于不可信输入。Optimizer Prompt 必须声明日志中的指令均为数据，不得执行，不得读取未提供路径，不得网络外传。

V1 默认只提升 AnalystBench Managed Active，不自动发布到生产源目录。

内部 Git 安全要求：运行 Git 时显式设置 repo 路径和固定 author，不读取用户全局 hooks，不执行仓库 hooks，不递归添加子模块，不跟随外部 worktree，不调用用户配置的 merge/diff driver。所有导入内容先经过普通文件白名单检查。

---

## 28. 测试

### 28.1 单元测试

**Package Snapshot / Internal Git**：v2 稳定哈希、忽略路径清单、排序、
执行 mode、setuid/setgid 拒绝、符号链接拒绝、路径穿越、重复哈希、
大小限制、checkout 后哈希一致、只读物化保留执行位、内部仓库不修改
用户 `.git`、禁用 hooks/submodule。

**Patch Applier / Optimizer Pipeline**：四种操作、`create`/`old_text`
拒绝、anchor/old 不唯一、越界路径、预算超限、整体回滚、四角色
Train-only Prompt、严格 schema、一次格式修复、1/2 秒退避、round-robin 和
canonical hash 去重。

**Gate**：正常 promote、Overall 不达标、关键维度退化、新增超时、延迟超限、灰区、最大次数拒绝、Case 级 Bootstrap、四 Case 模式不宣称显著性、`provisional` Active。

**State Machine**：合法/非法迁移、重复事件幂等、Worker 恢复、取消、Active 冲突。

### 28.2 集成测试

构造 Fake Harness：3 个 Train Cases、2 个 Validation Cases、确定性评分、一个可通过 Patch、一个回归 Patch。验证：

```text
import
→ create experiment
→ baseline
→ build evidence
→ generate 2 candidates
→ static reject 1
→ validate 1
→ promote
→ active changed
→ rollback
```

### 28.3 E2E

使用真实 claude Skill Harness 小数据集验证：把冻结版本复制到
`<workspace>/.claude/skills/<skill>`、cwd 正确、`/skill-name` 可被发现、每次运行
使用独立临时 HOME/XDG 环境、并发不污染、Candidate Report 正常评分、
轮询状态更新、Diff 展示、Active 切换后普通评测使用新版本、历史评测
仍读取旧版本。HOME/XDG 重定向只是进程环境与用户状态隔离，不是
文件系统/网络沙箱证据。

### 28.4 回归

现有无 Skill 或固定 Skill 评测行为不变。新增字段必须可空或有兼容默认值。

---

## 29. 数据迁移和兼容

步骤：

1. 从当前 Alembic 最新头创建后继迁移；
2. 新建 Skill、Version、Binding、Variant、Experiment、Epoch、Candidate、Snapshot、Run Group、Comparison、Decision 和 Event 表；
3. 为 `evaluation_submissions` 增加可空 `purpose`、`optimization_context_json`；
4. Variant 通过 `materialized_method_id` 复用现有 Method Run，不强制给所有历史 Method Run 增加 Skill FK；
5. 导入当前 Skill 为 v1，可选设为某个 Target Binding 的 Active；
6. 历史 Run 不强制回填，显示 `legacy/unfrozen`。

运行版本优先级：

```text
request.target / target_selection
  ├── target binding exists  -> freeze current Active EvaluationVariant
  └── no binding             -> frozen Target materialized method

request.method_id             -> explicitly selected frozen legacy Method
```

当前普通 Submission API 不接收一个单独的 `skill_version`
参数。按 Target 评测时在创建 Submission 的事务中解析 Active，并将
Variant/Version/Binding 身份写进冻结 Target snapshot。Active 变化不得改变
历史 Run 展示。

---

## 30. 实施拆分

### 30.0 当前开发基线（2026-08-12）

第一条后端纵切已落在独立的 `src/analystbench/skill_optimization/` 包：

- 已实现 v2 包检查与稳定哈希（包含归一化 mode 和
  `ignored_paths`）、setuid/setgid/符号链接/容量限制、AnalystBench
  内部 bare Git、不可变只读版本、Diff 和 checkout 后哈希复核；
- 已实现 Skill、Version、Binding、Binding History、EvaluationVariant、Experiment、Epoch、Candidate、Snapshot、Run Group、Signal、Comparison、Decision、Event 的 ORM 与 `0014`—`0018` 迁移；`0018` 增加底层 Submission 幂等键；
- 现有 `EvaluationSubmissionService` 只增加可选 `EvaluationWorkspacePreparer` Protocol；应用工厂和 Worker 注入 Skill Adapter，功能关闭时保持旧行为；
- 已实现冻结版本安装到 `<workspace>/.claude/skills/<skill>`，且只复制声明的 Skill 包；目标命令收到具体 `ANALYSTBENCH_SKILL_VERSION_ID`；
- Optimizer、Target 和 Judge 使用各自临时 HOME/XDG 目录；这是进程环境隔离，不是通用文件系统/网络沙箱，交互式 HOME 登录不会自动复制；
- 已实现四角色 Train-only Optimizer pipeline、严格
  `structured_skill_patch.v1`、一次 JSON 格式修复、Runner 1/2 秒退避、
  canonical-hash round-robin 去重、逐 Epoch 收紧的操作预算、文件数/增删
  Token/单文件比例预算、静态失败候选隔离、每轮默认最多两个
  候选、单次 Screening、三次完整配对验证、灰区自动追加到 5/7 次、
  逐 Case 中位数和确定性 Bootstrap；
- 静态验证固定检查凭据与私有路径、Case 泄漏、引用文件、脚本语法和声明式包内测试；只有 manifest 声明 `package_tests.argv` 才会使用 bubblewrap 无网络 namespace 执行，失败发生在不可变版本导入前；
- 已实现 Failure Family、Dimension、Failure Tag Evidence，优化器会读取当前 Evidence 和同实验 Rejected History，但不会读取 hidden/prospective holdout；
- 已实现硬回归 Gate、`provisional`/`validated` 判定、原子 Binding 晋升、下一 Epoch 新基线、连续两轮无 Screening survivor/无验证提升 Early Stop、取消、恢复与事件记录；
- Full Gate 强制完整输出字符近似 Token usage，缺失/超阈值硬拒绝；逐 Case 的 `forbidden_hit_count`/`missing_chain_count` 重复中位数任一上升硬拒绝；
- Run Group 使用冻结配置哈希幂等复用；Verifier 冻结 Judge 与
  Bootstrap 策略，Validation repeat 以稳定 `pair_seed` 交错 arm 顺序；
  底层 Submission 使用同一 run hash 的唯一 idempotency key，覆盖
  “Submission 已创建、Run Group 尚未写入”的崩溃窗口；配置漂移则拒绝复用；
- Snapshot 冻结 Case 输入、日志、Eval Spec 和 `source_group_key` 哈希；启动、恢复和推进时拒绝内容漂移，并支持严格独立 Train/Validation；`independent_validation` 强制单 Epoch，启动时原子消费 Snapshot；
- 每个已终结 Epoch 持久化候选意图、实际修改统计、基线/候选分数、本轮与累计 Delta、逐 Case/Family/Dimension 变化和 Gate/Active 决策；支持 JSON、Markdown、CSV 确定性总账导出；
- 已实现版本 ZIP 导出、同一 Target 历史 Active 限定的显式回滚、乐观锁和 Binding 审计历史；不会把版本自动写回用户源目录；
- 按 Target 发起的普通评测会优先解析当前 `SkillTargetBinding` Active 对应的冻结 EvaluationVariant，并把具体版本冻结进 Submission；历史 Run 不随晋升/回滚变化；
- V1 每个 Evaluation Target 只允许一个 Active Skill；优化 Submission/结果默认不进入普通列表和统计，普通取消/删除 API 不能绕过 Experiment 破坏它们；
- 已实现基础与上下文环境预检，覆盖功能开关、绝对 Managed Root、Git/Runner、迁移、磁盘、Skill/Harness/Target/Profile/Policy/Snapshot 和 Case 日志；
- 已提供 `/api/v1/skills`、`/api/v1/evaluation-variants`、`/api/v1/skill-optimization/*` 后端接口，以及实验向导、分页 Epoch 流、Evidence、候选、比较、Diff、总账、导出、版本和回滚前端；
- 设置页只读发现宿主机 Skill，普通测评、定时测评和自优化统一选择明确的 Harness × Model × Skill 组合；选择后后端自动建立内部版本。前端支持 `development_regression` 或 `independent_validation`，并在页面为 Case 编辑 Train/Validation/Hidden/Prospective split；独立模式的 Epoch 输入被锁定为 1；
- 已通过确定性 `/skill` 命令契约并发 E2E：两个冻结版本分别安装到独立 `<workspace>/.claude/skills/<skill>`，并发执行且互不污染。真实 claude E2E 自动发现 PATH 中的 `claude`，也可由 `ANALYSTBENCH_REAL_CLAUDE` 显式指定。

本地代码闭环已覆盖 V1 开发范围。真实 claude 二进制、用户私有 Case 先导实验、
独立 Holdout 和研究结果仍是用户私有环境的运行项，不能用确定性替身测试
代替，也不能在没有数据时预填结论。完整操作见
[Skill 自优化私有环境运行手册](../skill-optimization-runbook.md)。

### Phase 0：仓库勘察

复核本文 0.2 的仓库映射，补齐 claude 对项目级 `.claude/skills/<name>` 的实际发现机制和真实命令烟雾测试，并把差异直接更新回本文。此阶段不改变业务行为，不额外制造内容重复的映射文档。

### Phase 1：Skill Registry 与版本冻结

实现表、迁移、CRUD、导入、内部 Git、哈希、文件树、Diff、Binding、Variant 物化、回滚和项目级 Skill 安装。

验收：两个并发 Run 分别在自己的 `<workspace>/.claude/skills/<name>` 加载不同版本且互不污染；内部 Git 不修改 AnalystBench 或用户源仓库；修改源目录不影响快照；Active 切换不影响历史。

### Phase 2：Experiment 骨架和基线

实现 Experiment/Epoch 状态机、Optimization Data Snapshot、Run Group、子 Evaluation Submission、同 Experiment 中断恢复缓存、Run Role、API 和基础前端。

验收：可创建、启动、取消、恢复实验；已完整终结的同实验 Run Group 可复用；跨实验不复用旧基线；Epoch 2 以 Epoch 1 已提升版本为新基线。

### Phase 3：OptimizationSignal 与 Evidence

实现 Judge 适配、Failure Tags、成功/失败信号、聚类、优化器输入和 Decision History。

验收：每个评分 Run 从实际 `run.json/result.json/artifact_json` 生成合法信号；可按 Tag/Family 聚合；优化器输入不含 prospective holdout 或隐藏答案；无 Trace 时保持 `trace_uri=null`。

### Phase 4：候选和静态验证

实现四角色 Train-only Optimizer pipeline、版本化 Prompt、严格
`structured_skill_patch.v1`、一次 JSON 格式修复、Runner 退避、canonical-hash
round-robin 合并、Patch Applier、预算、静态检查、包内测试和 Diff UI。

验收：默认可选出最多两个去重候选；非法 schema、`create`、
`old_text` 和越界 Patch 拒绝；合法 Patch 创建不可变包；所有拒绝有错误码。

### Phase 5：筛选、完整验证和 Gate

实现 Screening、配对调度、中位数、Bootstrap、Gate、灰区增采样和比较页面。

验收：关键回归不能被总分抵消；Gate 可复现；灰区可追加到 5/7 次；展示 Case/Family/Dimension Delta；四 Case 模式只产生 `provisional` 判定，不显示统计显著。

### Phase 6：Promotion 与闭环

实现原子 Active、冲突处理、下一 Epoch、Early Stop、回滚、审计、指标和 E2E。

验收：`development_regression` 最多五轮闭环，接受候选成为下一轮父版本；
`independent_validation` 固定一轮；拒绝不影响 Active；同一 Target Binding
上曾经 Active 的版本可带乐观锁回滚；Worker 重启可恢复。

### Phase 7：文档和实验

操作、API、配置、导出、回滚和私有验收手册已补充。真实内核日志实验和研究
文档结果由用户在私有环境运行后据实填写，不属于本地替身开发的完成声明。

---

## 31. V1 验收清单

以下 `[x]` 只表示当前代码、迁移或确定性测试已实现对应契约，不代表真实
claude 或私有数据已经跑出提升。真实环境证据单列在末尾。

### 功能

- [x] 注册和导入 Skill；
- [x] 设置页只读发现冻结 Harness 下的宿主机 Skill，三个执行入口统一使用 Harness × Model × Skill 组合并自动建立内部版本；
- [x] 每个 Skill 使用与用户仓库隔离的内部 Git；
- [x] 完整包不可变版本；
- [x] EvaluationVariant 绑定 Target 与 SkillVersion，并校验 Harness Key；
- [x] 运行前安装到隔离工作区的项目级 Skill 目录；
- [x] 候选运行隔离；
- [x] Development Regression 最多五 Epoch；Independent Validation 强制一 Epoch；
- [x] 四角色只使用有界 Train/Development 证据，并按固定顺序
  round-robin/canonical hash 去重，默认每轮取最多两个候选；
- [x] 所有候选使用严格 `structured_skill_patch.v1`，只允许
  `append`/`insert_after`/`replace`/`delete`；
- [x] 编辑预算生效；
- [x] 静态违规候选单独拒绝，不中止其他候选；
- [x] 基线/候选配对运行三次；
- [x] 配对运行以稳定 Seed 交错 arm 顺序；Verifier 冻结 Judge 和
  Bootstrap policy/seed 身份；
- [x] 灰区自动追加到 5/7 次；
- [x] Gate 支持硬约束和 Bootstrap；
- [x] 四 Case 开发模式标记 `provisional` 且不宣称显著性；
- [x] 独立模式严格分离 Train/Validation，校验最少 Validation Case 和跨 split `source_group_key`；
- [x] 独立 Snapshot 只能被一个已启动 Experiment 原子消费，避免重复选择导致 Validation 过拟合；
- [x] Snapshot 冻结输入/日志/Eval Spec 哈希，运行前拒绝 drift；
- [x] prospective holdout 不进入优化器输入；
- [x] Development Regression 中 Rejected Buffer 下一轮可检索；独立验证不会运行第二 Epoch；
- [x] 通过候选原子提升；
- [x] 仅可显式回滚到同一 Target 曾 Active 的版本，带乐观锁和审计历史；
- [x] Run Group 恢复复用和 Early Stop；
- [x] Submission 幂等键与 Run Group 唯一哈希覆盖“Submission 已创建、
  Run Group 尚未落库”的崩溃窗口；
- [x] 每个 Epoch 持久化“改了什么”和本轮/累计分数升降；
- [x] 前端查看 Diff、逐 Case/Family/Dimension 变化、Gate 原因和历史；
- [x] 实验总账可导出 JSON/Markdown/CSV，版本可导出确定性 ZIP；
- [x] CLI/API/UI 提供环境预检、总账、版本导出和显式回滚入口。
- [x] 按 Target 创建的普通评测冻结当时 Active Variant/Version，历史评测不随 Binding 变化；
- [x] 优化结果排除普通统计/列表，且普通取消/删除不能破坏实验子任务；

### 质量

- [x] 当前工作树已生成核心风险代码覆盖率报告；
- [x] Gate 95%、Patch 98%，状态机可执行区间 90.3%。整个
  `experiment.py`（含 CRUD、分页、详情和兼容读取面）为 80.0%，不拿整文件比例
  冒充状态机比例；
- [ ] 通用认证/授权中间件及全部 API 权限检查；这是当前本地自托管产品缺少的
  平台级基础设施，不属于 Skill 自优化 V1，不能在本模块中虚构已完成；
- [x] 无路径穿越和符号链接逃逸；
- [x] v2 package hash 包含归一化 mode 和 `ignored_paths`，拒绝
  setuid/setgid，只读物化保留执行位；
- [x] Patch 预算、内容安全、Case 泄漏、引用、语法和声明式包内测试检查；
- [x] 包内测试必须由 manifest 声明，且使用 bubblewrap 无网络 namespace；真实 namespace E2E 由私有宿主 opt-in；
- [x] 不修改 AnalystBench 仓库或用户源仓库的 Git 状态；
- [x] 并发候选不污染（确定性命令契约 E2E）；
- [x] 历史评测可复现；
- [x] 当前工作树全量后端回归收集 234 个用例：231 通过，3 个按私有环境
  约定跳过（真实 claude 1 个、bubblewrap namespace 2 个），退出码 0；

### 性能

- [x] 快照、预检和哈希 API 使用同步路由线程池，推进阶段由 Worker 执行，不阻塞异步事件循环；
- [x] 大文件和文件数有上限；
- [x] 前端详情按 Epoch 分页加载；
- [x] 事件轮询更新；
- [x] 同实验 Run Group 缓存命中不重复执行。

### 用户私有环境与研究证据（不由本地替身代替）

- [ ] 真实 claude 二进制 `/skill` 发现与并发 E2E；
- [ ] 真实 Optimizer × Target × Judge 的完整单 Epoch 验收；
- [ ] 用户私有 Case 的 `development_regression` 先导实验；
- [ ] 独立 Train/Validation 与最终未暴露 Holdout 实验；
- [ ] 根据真实导出总账填写研究结果、成本、失败样本和环境版本。

---

## 32. 扩展接口

```python
class MutationStrategy(Protocol):
    def propose(self, context) -> list[StructuredPatch]: ...

class VerificationStage(Protocol):
    def verify(self, candidate, context) -> VerificationResult: ...

class GateStrategy(Protocol):
    def decide(self, comparison, policy) -> GateDecision: ...

class EvidenceRetriever(Protocol):
    def retrieve(self, query, limit) -> list[DecisionRecord]: ...
```

后续可接入 SkillOpt Adapter、Trace2Skill 合并器、CoEvoSkills Surrogate Verifier、SkillMOO Pareto Search、MUSE Memory、SkillOS Curator 和 MetaSkill-Evolve，但不得改变不可变版本、隔离运行和权威门禁原则。

---

## 33. 完成定义

“本地代码开发完成”和“私有实验研究完成”是两个不同边界。

### 33.1 V1 本地代码开发完成

以下事项由当前仓库实现和本地确定性验证证明：

1. Skill 可从 Harness 派生的用户配置目录导入内部 Git 并形成不可变包；
2. 两个版本可分别安装到独立工作区的项目级 Skill 目录，并发执行且互不污染；
3. Evaluation 结果可生成 OptimizationSignal；
4. 优化器输出只能通过受限 Structured Patch 修改 Managed 副本；
5. 候选经过预算、静态验证、Screening 和重复配对验证；
6. Gate 可阻止关键类别、执行、时延与 Token 回归；
7. 通过候选原子更新 Active，失败候选不改变 Active；
8. 每轮修改、分数升降、逐 Case/Family/Dimension 和决策可在前端复盘并导出；
9. Development Regression 的新 Active 可进入下一 Epoch；Independent Validation
   固定一 Epoch 且不重用 Snapshot；中断可恢复；
10. 同一 Target 曾 Active 的版本可带乐观锁显式回滚，历史评测身份不变；
11. Snapshot 内容冻结、独立切分、防泄漏、版本 ZIP、Binding 审计和环境预检可用；
12. 聚焦测试、前端构建和全量回归以当前提交的实际命令结果为验收证据。

### 33.2 用户私有环境与研究完成

以下事项必须由用户在私有环境完成，不能作为 Codex 本地开发的虚构结果：

1. 真实 claude Skill 可从用户配置目录导入内部 Git 并形成不可变包；
2. 两个版本可分别安装到独立工作区的项目级 Skill 目录，并发评测且互不污染；
3. 真实 Benchmark 可生成 OptimizationSignal；
4. 优化器可生成受限 Patch；
5. 候选经过静态、筛选和三次配对验证；
6. Gate 可阻止关键类别回归；
7. 通过候选原子更新 Active；
8. 失败候选和原因可在前端复盘；
9. Development Regression 的新 Active 可进入下一 Epoch；Independent Validation
   只运行一 Epoch；
10. 同一 Target Binding 上任一曾 Active 的历史版本可带乐观锁回滚，不要
    把范围误写成“任意已验证版本”；
11. E2E 和现有系统回归测试通过；
12. 当前四 Case 能完成带 `provisional` 标记的先导实验；
13. 在新增独立 Holdout Case 前，不把先导结果写成论文主实验或泛化结论；
14. 研究文档第一组正式主实验结果在数据条件满足后补充。

---

## 附录 A：错误码

以下是当前 V1 主路径使用的稳定小写错误/原因码，而非早期设计中的
大写占位名：

```text
skill_source_invalid
skill_package_too_large
skill_path_invalid
skill_symlink_forbidden
skill_file_mode_forbidden
skill_install_path_invalid
skill_git_failed
skill_package_integrity_failed

optimizer_output_invalid
optimizer_policy_invalid
optimizer_policy_unsafe_tools
optimizer_policy_unsafe_arguments
skill_patch_invalid
skill_patch_operation_invalid
skill_patch_operation_forbidden
skill_patch_path_forbidden
skill_patch_target_missing
skill_patch_anchor_invalid
edit_budget_exceeded
skill_token_budget_exceeded

skill_content_security_violation
skill_case_leak_detected
skill_reference_check_failed
skill_script_syntax_invalid
skill_package_test_config_invalid
skill_package_test_sandbox_unavailable
skill_package_tests_timeout
skill_package_tests_failed

screening_results_missing
screening_delta_below_minimum
no_paired_results
minimum_delta_not_met
candidate_case_failures_increased
candidate_new_failure_type
candidate_guardrail_metric_increased
critical_dimension_regressed
failure_family_regressed
latency_growth_exceeded
token_usage_missing
token_growth_exceeded
gray_zone
inconclusive_after_max_repeats
independent_validation_cases_insufficient
independent_validation_not_confident

optimization_independent_validation_epoch_limit
optimization_independent_snapshot_consumed
optimization_case_input_drift
optimization_eval_spec_drift
optimization_submission_managed_by_experiment
optimization_result_managed_by_experiment
evaluation_target_skill_binding_conflict
skill_binding_conflict
```

---

## 附录 B：最小完整流程伪代码

```python
def run_experiment(experiment_id: str) -> None:
    exp = freeze_experiment(experiment_id)
    current_version = exp.base_skill_version

    for epoch_no in range(1, exp.max_epochs + 1):
        epoch = create_or_get_epoch(exp, epoch_no, current_version)
        baseline = ensure_epoch_baseline(
            experiment=exp,
            epoch=epoch,
            parent_version=current_version,
        )

        signals = build_optimization_signals(
            experiment=exp,
            epoch=epoch,
            baseline=baseline,
            current_version=current_version,
        )

        evidence = build_evidence_bundle(
            signals=signals,
            rejected_history=retrieve_rejected_history(signals),
        )

        patches = propose_candidate_patches(
            policy=exp.optimizer_policy,
            current_version=current_version,
            evidence=evidence,
        )

        candidates = []
        for patch in patches:
            candidate = create_candidate(epoch, patch)

            static_result = static_validate(candidate)
            if not static_result.passed:
                reject_candidate(candidate, static_result)
                continue

            screening = run_screening(exp, candidate, baseline)
            if not screening.passed:
                reject_candidate(candidate, screening)
                continue

            candidates.append((candidate, screening))

        survivor = select_screening_survivor(candidates)
        if survivor is None:
            finalize_epoch_rejected(epoch, "no_screening_survivor")
            if should_early_stop(exp):
                break
            continue

        while True:
            comparison = run_full_paired_validation(
                experiment=exp,
                candidate=survivor,
                repeats=current_repeat_target(exp),
            )

            decision = promotion_gate(comparison, exp.gate_policy)

            if decision.kind == "needs_more_runs":
                if comparison.repeat_count >= exp.gate_policy.max_repeats:
                    decision = reject_inconclusive(comparison)
                    break
                increase_repeat_target(exp)
                continue

            break

        record_decision(epoch, survivor, comparison, decision)

        if decision.kind == "promote":
            promote_atomically(
                binding=exp.skill_target_binding,
                expected_active=current_version,
                candidate=survivor.skill_version,
                provisional=exp.split_mode == "development_regression",
            )
            current_version = survivor.skill_version
            finalize_epoch_accepted(epoch, survivor)
        else:
            add_to_rejected_buffer(survivor, comparison, decision)
            finalize_epoch_rejected(epoch, decision.reason)

        if should_early_stop(exp):
            break

    finalize_experiment(exp, current_version)
```

---

## 附录 C：V1 默认值

| 配置 | 默认值 |
|---|---:|
| 当前数据模式 | development_regression |
| Skill 版本存储 | AnalystBench 内部 Git |
| 发布模式 | managed / provisional |
| 最大 Epoch | Development Regression 默认/上限 5；Independent Validation 固定 1 |
| 每轮候选数 | 2 |
| Screening 重复数 | 1 |
| Full Validation 初始重复数 | 3 |
| 最大重复数 | 7 |
| 最大 Patch 操作 | 4，逐轮衰减 |
| 最大修改文件 | 2 |
| 最大新增 Token | 600 |
| 最大删除 Token | 300 |
| 最小 Overall Delta | +1.0 |
| Bootstrap 样本数 | 2000（Verifier 可冻结配置） |
| 置信度 | 95% |
| 启用统计自动门禁的最少独立 Validation Case | 8 |
| 最大关键家族退化 | -2.0 |
| 最大延迟增长 | 20% |
| 最大 Token 增长 | 20% |
| 连续无提升 Early Stop | 2 Epoch |
