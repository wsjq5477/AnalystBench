# AnalystBench Skill 自优化系统方案设计

> 文档类型：工程设计规格 / Codex 开发输入  
> 状态：Revised Draft for Implementation  
> 版本：v1.1  
> 日期：2026-07-31  
> 目标版本：Skill Optimization V1  
> 目标读者：Codex、后端开发、前端开发、测试、架构评审人员

配套研究与实验口径：[AnalystBench 面向内核日志分析的 Skill 自优化方法研究与实验方案](./AnalystBench-Skill自优化研究与实验方案.md)。

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

1. Skill 源路径由用户配置，例如 `/user/project/.claude/skills/xxx`；
2. 每次目标 Agent 运行前，把冻结 Skill 版本安装到该次隔离工作区的项目级 Skill 目录，例如 `<workspace>/.claude/skills/xxx`；
3. 不复制用户完整 `.claude/` 或全局 HOME，只复制明确登记的 Skill 包和显式依赖；
4. Skill 历史使用 AnalystBench 自己管理的内部 Git 仓库，不向 AnalystBench 源码仓库或用户源仓库写入 commit、branch、tag、配置或工作区文件；
5. 提升只更新 `SkillTargetBinding.active_version_id`，V1 不自动同步回用户 `source_path`；
6. 当前只有 4 个 Case，V1 先采用 `development_regression` 小样本模式；三次重复用于降低随机波动，不等价于独立 Validation 或 Hidden Test；
7. 新增 Case 默认可以进入 `prospective_holdout`，在正式候选冻结前不向优化器公开。

### 0.2 当前仓库映射

| 设计概念 | 当前实现 | V1 改动原则 |
|---|---|---|
| Harness / Model / Target | `src/analystbench/db/models.py` 中 `EvaluationHarness`、`EvaluationModel`、`EvaluationTarget` | 继续复用；Target 仍表示 Harness × Model |
| 可执行 Target | `EvaluationTarget.materialized_method_id` 指向 `EvaluationMethod` | 新增 Variant 后物化独立 Method 或等价冻结执行快照 |
| 批量生成与评分 | `EvaluationSubmissionService`、`EvaluationSubmissionCaseRun`、`EvaluationSubmissionMethodRun` | 复用现有命令执行、正式结果和评分链路，不复制 Runner |
| 后台任务 | `Job`、`JobQueue`、`LocalWorker` | 增加优化 Job 类型和资源限流；保持租约与幂等 |
| Agent Profile | `ExecutionProfile`、`AgentExecutionService` | 优化器模型优先复用冻结 ExecutionProfile，不把它混同为被测 EvaluationModel |
| 内容存储 | `ContentStore` + `content_blobs` | Prompt、证据摘要等复用 ContentStore；Skill 文件历史使用独立内部 Git |
| 数据库迁移 | Alembic，当前头为 `0013_p19_harness_model_targets` | 新功能从实际最新头创建后继迁移 |
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
- 用户可配置源目录、调用名和项目级安装相对路径；
- 独立 HOME / Workspace / Skill Root；
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
- 当前四 Case 的 `development_regression` 模式和后续 `prospective_holdout` 升级路径。

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
7. 只有 Promotion Service 可以修改 Active；
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
              │ isolated HOME/root  │
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
    key VARCHAR(128) NOT NULL UNIQUE,
    name VARCHAR(256) NOT NULL,
    description TEXT,
    source_path TEXT,
    invoke_as VARCHAR(128) NOT NULL,
    harness_key VARCHAR(128) NOT NULL,
    install_relative_path TEXT NOT NULL,
    publish_mode VARCHAR(32) NOT NULL DEFAULT 'managed',
    editable_paths JSON NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

约束：

- `key` 创建后不可修改；
- `source_path` 只用于导入，不作为运行事实源；
- `install_relative_path` 必须是安全相对路径，并匹配 Harness Adapter 允许的项目级 Skill Root；
- claude 兼容 Harness 可使用 `.claude/skills/<skill-dir>`，其他 Harness 由 Adapter 声明允许前缀；
- V1 只支持 `managed`，不写回 `source_path`；
- 默认 editable paths 为 `SKILL.md`、`references/**`、`scripts/**`、`tests/**`。

### 5.2 skill_package_versions

```sql
CREATE TABLE skill_package_versions (
    id UUID PRIMARY KEY,
    skill_id UUID NOT NULL REFERENCES skills(id),
    version_no INTEGER NOT NULL,
    parent_version_id UUID NULL REFERENCES skill_package_versions(id),
    package_hash CHAR(64) NOT NULL,
    internal_git_commit VARCHAR(64) NOT NULL,
    internal_git_tree VARCHAR(64) NOT NULL,
    git_object_format VARCHAR(16) NOT NULL,
    manifest JSON NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_by VARCHAR(128),
    created_at TIMESTAMP NOT NULL,
    UNIQUE(skill_id, version_no),
    UNIQUE(skill_id, package_hash)
);
```

`source_type`：`imported`、`mutation`、`manual`。回滚直接把 Binding 指回已有 commit，不创建内容相同的 `rollback_copy`。  
`status`：`draft`、`candidate`、`provisional`、`validated`、`rejected`、`archived`。Active 由独立 Binding 表表示；四 Case 开发模式通过的版本使用 `provisional`，不能冒充独立验证完成。

内部 Git 约束：

- 每个 Skill 使用独立 bare repository，例如 `<managed_root>/skills/<skill-id>/repo.git`；
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

影响行数为 0 时标记 `ACTIVE_CHANGED_CONFLICT`。

### 5.3.1 evaluation_variants

```sql
CREATE TABLE evaluation_variants (
    id UUID PRIMARY KEY,
    evaluation_target_id UUID NOT NULL REFERENCES evaluation_targets(id),
    skill_package_version_id UUID NOT NULL REFERENCES skill_package_versions(id),
    materialized_method_id UUID NOT NULL REFERENCES evaluation_methods(id),
    install_relative_path TEXT NOT NULL,
    invoke_as VARCHAR(128) NOT NULL,
    content_hash CHAR(64) NOT NULL UNIQUE,
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
    key VARCHAR(128) NOT NULL,
    version_no INTEGER NOT NULL,
    optimizer_execution_profile_id UUID NOT NULL REFERENCES execution_profiles(id),
    prompt_bundle_uri TEXT NOT NULL,
    prompt_hash CHAR(64) NOT NULL,
    config JSON NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(key, version_no)
);
```

优化器调用属于“生成 Skill Patch 的 Agent”，不是被测 `EvaluationModel`。V1 复用冻结 `ExecutionProfile` 表达 claude 或 OpenCode 的可执行文件、模型、Prompt 环境和权限；`OptimizerPolicyVersion` 再冻结本功能专属 Prompt bundle 和编辑策略。

配置示例：

```json
{
  "candidate_count": 2,
  "max_epochs": 5,
  "reflection_batch_size": 8,
  "edit_budget_schedule": [4, 4, 3, 2, 1],
  "allowed_operations": ["append", "insert_after", "replace", "delete"],
  "max_changed_files": 2,
  "max_added_tokens": 600,
  "max_deleted_tokens": 300
}
```

### 5.5 verifier_bundle_versions

```sql
CREATE TABLE verifier_bundle_versions (
    id UUID PRIMARY KEY,
    key VARCHAR(128) NOT NULL,
    version_no INTEGER NOT NULL,
    static_policy JSON NOT NULL,
    gate_policy JSON NOT NULL,
    judge_config_snapshot JSON NOT NULL,
    content_hash CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(key, version_no)
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
    benchmark_snapshot_id UUID NOT NULL,
    split_snapshot_id UUID NOT NULL,
    optimizer_policy_version_id UUID NOT NULL,
    verifier_bundle_version_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL,
    current_epoch_no INTEGER NOT NULL DEFAULT 0,
    max_epochs INTEGER NOT NULL,
    stop_reason VARCHAR(128),
    created_by VARCHAR(128),
    created_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    config_snapshot JSON NOT NULL
);
```

Experiment 状态：`created`、`freezing`、`baseline_running`、`optimizing`、`screening`、`validating`、`promoting`、`completed`、`failed`、`cancelled`。

`benchmark_snapshot_id` 和 `split_snapshot_id` 不是当前仓库已有对象。V1 必须新增不可变优化数据快照，不能把可变化的 `dataset_key` 或当前目录扫描结果直接当实验身份。

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
    content_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL
);
```

`mode`：

- `development_regression`：当前 4 Case 均可用于证据和门禁，结果必须标记 `provisional`；
- `validation_gated`：Train 与 Validation 独立，允许自动提升为非临时 Active；
- `publication`：在前两者基础上冻结 Hidden Test，只用于最终报告。

同一原始事件的不同裁剪必须通过 `source_group_key` 归入同一集合。Snapshot 创建后不可新增 Case；后续 Case 通过新 Snapshot 进入 `prospective_holdout` 或下一轮正式切分。

### 5.7 optimization_epochs

```sql
CREATE TABLE optimization_epochs (
    id UUID PRIMARY KEY,
    experiment_id UUID NOT NULL REFERENCES optimization_experiments(id),
    epoch_no INTEGER NOT NULL,
    parent_skill_version_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL,
    evidence_summary JSON,
    best_candidate_version_id UUID,
    decision VARCHAR(32),
    created_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    UNIQUE(experiment_id, epoch_no)
);
```

Epoch 状态：`created`、`collecting_evidence`、`reflecting`、`generating_candidates`、`static_validating`、`screening`、`full_validating`、`deciding`、`accepted`、`rejected`、`failed`。

### 5.8 candidate_mutations

```sql
CREATE TABLE candidate_mutations (
    id UUID PRIMARY KEY,
    epoch_id UUID NOT NULL REFERENCES optimization_epochs(id),
    parent_skill_version_id UUID NOT NULL,
    candidate_skill_version_id UUID,
    candidate_type VARCHAR(32) NOT NULL,
    structured_patch JSON NOT NULL,
    patch_hash CHAR(64) NOT NULL,
    rationale TEXT,
    intended_failure_clusters JSON NOT NULL,
    evidence_refs JSON NOT NULL,
    status VARCHAR(32) NOT NULL,
    rejection_code VARCHAR(128),
    rejection_detail JSON,
    created_at TIMESTAMP NOT NULL
);
```

候选类型：`corrective`、`evidence_strengthening`、`simplification`、`tool_enhancement`。  
状态：`proposed`、`patch_validated`、`package_created`、`static_rejected`、`screening`、`screening_rejected`、`full_validation`、`gate_rejected`、`accepted`、`failed`。

### 5.9 optimization_signals

```sql
CREATE TABLE optimization_signals (
    id UUID PRIMARY KEY,
    experiment_id UUID NOT NULL,
    epoch_id UUID,
    case_revision_id UUID NOT NULL,
    evaluation_run_id UUID NOT NULL,
    run_role VARCHAR(16) NOT NULL,
    case_family VARCHAR(128),
    score DECIMAL(8,3),
    signal JSON NOT NULL,
    signal_hash CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(evaluation_run_id)
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
    baseline_run_group_id UUID NOT NULL,
    candidate_run_group_id UUID NOT NULL,
    metrics JSON NOT NULL,
    gate_result JSON,
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
    evaluation_submission_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL,
    run_config_hash CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(experiment_id, epoch_id, candidate_mutation_id, split_role, arm, repeat_index)
);
```

底层 Submission 增加可空的 `purpose` 和 `optimization_context_json`：

```text
purpose = normal | skill_optimization
optimization_context = experiment/epoch/candidate/arm/repeat/split
```

`skill_optimization` Submission：

- 复用现有 Case 复制、Method Run、stdout 报告、评分和 Worker；
- 默认 `included_in_statistics=false`，不进入普通总览和正式排行榜；
- 由 Optimization Experiment 管理保留和删除，普通“正式结果”页面默认折叠；
- 每次运行冻结 `EvaluationVariant` 和 Skill package hash；
- 基线与候选使用不同 Submission，不能共享可写运行目录。

### 5.11 decision_records

```sql
CREATE TABLE decision_records (
    id UUID PRIMARY KEY,
    experiment_id UUID NOT NULL,
    epoch_id UUID,
    candidate_mutation_id UUID,
    diagnosis JSON NOT NULL,
    revision JSON,
    evidence JSON NOT NULL,
    outcome JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
```

---

## 6. Skill 包存储与哈希

推荐管理目录：

```text
<managed_root>/
├── skills/<skill-id>/
│   ├── repo.git/
│   └── metadata/
│       ├── <version-id>.json
│       └── <package-hash>.file-index.json
├── optimizer-policies/
├── verifier-bundles/
└── experiments/
```

`managed_root` 默认从现有 `workspace_root_path` 派生为 `<workspace_root_path>/skill-optimization`；大文本和结构化摘要继续进入现有 `ContentStore`。不得在项目源码目录下创建内部仓库。

### 6.1 包哈希

1. 遍历纳入版本的文件；
2. 相对路径统一为 `/`；
3. 按路径排序；
4. 不跟随符号链接；
5. 拒绝设备文件、FIFO 和 Socket；
6. 每个文件计算 SHA-256；
7. 将 `path + mode + size + file_hash` 序列化为 canonical JSON；
8. 对 canonical JSON 再计算 SHA-256。

哈希基于原始 bytes，不自动修改换行。

内部 Git 提交前后都计算上述哈希；checkout 后必须重新计算并与数据库 `package_hash` 一致。Git commit hash 不能替代包哈希，因为 commit 还包含父提交、作者和时间。

### 6.2 文件安全

必须拒绝：`../`、绝对路径、指向包外的符号链接、超限文件、设备节点、FIFO、Socket、setuid/setgid。导入时忽略或拒绝 `.git/`、`.svn/`、缓存目录、编辑器临时文件和运行产物，具体规则进入冻结 Manifest。

---

## 7. Skill 配置

```json
{
  "key": "kernel-log-analysis",
  "name": "Kernel Log Analysis",
  "source_path": "/home/jiqi/claude/skills/kernel-log-analysis",
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

推荐注册时由用户选择或由 Harness Adapter 提供默认安装位置：

```text
claude-compatible → .claude/skills/<skill-dir>
custom            → 用户配置，但必须匹配 Adapter allowlist
```

`invoke_as` 是 Prompt 中的调用名，`install_relative_path` 是文件系统位置，两者不能相互推导。

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
└── artifacts/
```

环境变量：

```bash
HOME=<run_root>/home
ANALYSTBENCH_SKILL_VERSION_ID=<uuid>
```

Harness Adapter 从内部 Git 的指定 commit 导出 Skill 包，验证 `package_hash`，再复制到 `<workspace>/<install_relative_path>`。目标 Agent 的 cwd 是 `<workspace>`，因此类似 `claude -p "/kernel-log-analysis 分析 logs/..."` 的命令可通过项目级 Skill 发现机制加载该版本。

禁止通过覆盖共享 `~/.claude/skills` 或其他全局目录实现候选切换。也禁止把用户完整 `.claude/` 复制进工作区，因为其中可能包含 settings、hooks、plugins、MCP、认证引用和与实验无关的 Skill。若 Harness 只能发现全局 Skill，必须先扩展 Adapter 或使用隔离 HOME；不得暂时覆盖后再恢复。

运行期间 Skill 包只读；Benchmark 和答案只读且优化 Agent 不可见；缓存写入独立临时目录；候选生成器与目标 Agent 不共用 HOME。

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

```json
{
  "schema_version": "1.0",
  "candidate_type": "corrective",
  "summary": "强化锁等待者与锁持有者的区分",
  "target_failure_clusters": ["cluster-1"],
  "preserve_rules": ["preserve-1"],
  "operations": [
    {
      "op": "insert_after",
      "path": "SKILL.md",
      "anchor": "## 锁与阻塞分析",
      "anchor_occurrence": 1,
      "content": "\n### 等待者与持有者判定\n..."
    },
    {
      "op": "replace",
      "path": "SKILL.md",
      "old_text": "block LOCK 表示线程持有锁",
      "new_text": "block LOCK 只表示线程因锁阻塞，需结合 owner 或调用栈判断持锁者"
    }
  ],
  "expected_effect": {
    "improve_tags": ["EVIDENCE_NOT_BOUND", "UNSUPPORTED_CLAIM"],
    "risk_tags": ["MISSING_ROOT_CAUSE"]
  }
}
```

### 11.2 操作

V1 仅支持 `append`、`insert_after`、`replace`、`delete`。

规则：path 必须匹配 editable paths；anchor 或 old_text 必须唯一命中；操作按顺序执行；任一步失败整体回滚；在临时副本应用；完成后重新计算包哈希；相同哈希不创建新版本；保存 before/after diff。

### 11.3 编辑预算

```yaml
max_operations: 4
max_changed_files: 2
max_added_tokens: 600
max_deleted_tokens: 300
max_single_file_change_ratio: 0.25
```

超限错误码：`EDIT_BUDGET_EXCEEDED`。

---

## 12. 证据分析与候选策略

V1 实现四个逻辑角色，可以由同一模型使用不同 Prompt：

```text
failure_analyst
success_analyst
generalization_analyst
simplification_analyst
```

每个角色必须输出 JSON。合并器执行：按 tag 和语义相似度去重；合并 support count；删除案例专属关键词；检测 preserve 与 corrective 冲突；检测多个操作的区域冲突；生成候选方向。

默认每轮两个候选：

```text
Candidate 1: corrective
Candidate 2: simplification 或 evidence_strengthening
```

失败主要来自脚本时可使用 `tool_enhancement`。V1 UI 可配置 1～2，后端预留最大 4。

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

若存在 `tests/`，在无网络沙箱执行，设置超时并收集 stdout/stderr。命令由 manifest 声明。失败时候选不得进入 Screening。若未配置测试，允许通过但记录 `not_configured`。

---

## 14. Evaluation Submission 扩展

当前真实执行单位是 `EvaluationSubmission → CaseRun → MethodRun`。V1 增加 Submission `purpose`，并在 `optimization_run_groups` 中保存以下 `run_role`：

```text
baseline
screening_candidate
validation_baseline
validation_candidate
hidden_test
```

每个 Run Group 绑定：`skill_package_version_id`、`optimization_experiment_id`、`optimization_epoch_id`、可空 `candidate_mutation_id`、`repeat_index`、`pair_key`。底层 Submission Manifest 必须包含同一快照，Method Run artifact 再保存实际安装路径、package hash 和内部 Git commit。

`pair_key`：

```text
<experiment-id>:<epoch>:<case-revision-id>:<repeat-index>
```

历史 Run 可保持 SkillVersion 为 NULL，并显示 `legacy/unfrozen`。

---

## 15. 基线缓存

缓存键必须包含：

```text
parent_skill_version_id
evaluation_target_id
benchmark_snapshot_id
split_snapshot_id
verifier_bundle_version_id
run_config_hash
repeat_count
```

任一 Skill、Model、Harness、Benchmark、Case Revision、Eval Spec、Judge、运行参数或环境镜像变化均不得复用。

完整验证中基线与候选交错执行，顺序由稳定 Seed 生成，降低服务时间漂移。

V1 的“缓存”只用于同一 Experiment、同一 Epoch 和同一目标 repeat 的中断恢复，不跨 Experiment 复用历史分数。原因是当前本地 Harness、外部 CLI、模型服务和工作目录内容无法形成足够强的环境指纹。每个 Epoch 的基线必须是该 Epoch 的 `parent_skill_version_id`：

```text
Epoch 1: baseline v1 vs candidate v2
v2 promoted
Epoch 2: baseline v2 vs candidate v3
```

不得让后续候选始终与实验初始 v1 比较。若某个已完成 Run Group 的 Manifest、输出哈希和状态完整，Worker 重启后可以幂等复用该组，而不是重新调用模型。

---

## 16. Screening 与 Full Validation

### 16.1 Screening

- `validation_gated` 使用 Validation 子集；`development_regression` 在当前四 Case
  阶段让全部开发 Case 参与 Evidence 和固定 Screening，Case 增长后再由冻结快照
  显式指定开发 Screening 子集；
- 每 Case 一次；
- 不做 Bootstrap；
- 只检查硬约束和粗粒度 Delta；
- 最多保留一个候选进入完整验证。

拒绝条件：新增执行失败；平均 Delta < -1；关键维度显著下降；Unsupported Claim 上升超过阈值；中位耗时增长超过 50%。

### 16.2 Full Validation

- `validation_gated` 模式使用独立 Validation 全集；
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
- 只在最终版本、统计方案和固定重复次数预注册后运行；
- 一旦用于决定继续修改 Skill，该集合即失去 Hidden Test 身份，必须登记为已暴露并更换新快照。

### 16.5 当前四 Case 模式

当前只有 4 个 Case，不能把 `2/1/1` 机械切分后声称得到可靠 Train、Validation 和 Test。默认配置：

```json
{
  "split_mode": "development_regression",
  "train_case_count": 4,
  "validation_case_count": 0,
  "hidden_test_case_count": 0,
  "repeats": 3,
  "promotion_label": "provisional"
}
```

四个 Case 都可以向优化器提供反馈；Gate 的职责是防止已知 Case 回归并判断样本内是否改善。后续新增 Case 默认先进入 `prospective_holdout`。积累到足以覆盖多个独立故障家族后，再由用户冻结正式切分并启用 `validation_gated`。

---

## 17. Promotion Gate

### 17.1 配置

```json
{
  "quality": {
    "min_overall_delta": 1.0,
    "min_candidate_win_probability": 0.95,
    "require_bootstrap_lower_bound_gt_zero": true
  },
  "guardrails": {
    "error_type_delta_min": 0.0,
    "root_cause_delta_min": 0.0,
    "unsupported_claim_rate_delta_max": 0.0,
    "critical_family_max_regression": -2.0,
    "generation_success_rate_delta_min": 0.0,
    "new_timeout_count_max": 0,
    "new_empty_report_count_max": 0,
    "new_execution_failure_count_max": 0,
    "median_latency_growth_max": 0.20,
    "median_token_growth_max": 0.20
  },
  "statistics": {
    "bootstrap_samples": 10000,
    "confidence": 0.95,
    "minimum_independent_validation_cases": 8,
    "initial_repeats": 3,
    "max_repeats": 7
  }
}
```

### 17.2 判定顺序

1. 数据完整性；
2. 执行成功硬约束；
3. 关键质量硬约束；
4. 性能和成本硬约束；
5. Overall Delta；
6. 统计置信度；
7. 输出 `promote/reject/needs_more_runs`。

硬约束失败不得被总分抵消。

门禁模式：

- `development_regression`：执行质量与稳定性硬约束、最小 Delta 和逐 Case 回归检查；Bootstrap 仅展示，不以 4 个 Case 声称统计显著；通过后只产生 `provisional` Active；
- `validation_gated`：达到 `minimum_independent_validation_cases` 后，才启用 Bootstrap 下界/胜率作为自动发布必要条件；
- `publication`：只报告冻结 Final Version 的 Hidden Test 结果，不执行 Promotion。

### 17.3 配对计算

```python
baseline_case_score = median(baseline_repeats)
candidate_case_score = median(candidate_repeats)
case_delta = candidate_case_score - baseline_case_score
overall_delta = mean(case_deltas)
```

Bootstrap 以 Case 为重采样单元，不能以单次 Run 为单元。

### 17.4 原子 Promotion

单事务执行：校验 Active 未变化；写 DecisionRecord；更新 Binding；标记 Candidate accepted；按切分模式把版本标记为 `provisional` 或 `validated`；写持久化事件记录；提交。V1 前端通过轮询读取事件记录；后续 SSE 可以发送 `skill.active_changed`。失败可幂等重试。

---

## 18. 状态机

### 18.1 Experiment

```text
created
  ↓
freezing
  ↓
baseline_running
  ↓
optimizing
  ├── screening
  ├── validating
  ├── promoting
  └── optimizing(next epoch)
  ↓
completed
```

任意运行态可进入 `failed` 或 `cancelled`。

### 18.2 Epoch

```text
created
  ↓
collecting_evidence
  ↓
reflecting
  ↓
generating_candidates
  ↓
static_validating
  ↓
screening
  ↓
full_validating
  ↓
deciding
  ├── accepted
  └── rejected
```

### 18.3 Early Stop

- 达到最大 Epoch；
- 连续两个 Epoch 无候选通过 Screening；
- 连续两个 Epoch 完整验证无提升；
- 达到目标分；
- 预算耗尽；
- 人工取消；
- 不可恢复基础设施错误。

`stop_reason`：`MAX_EPOCHS`、`NO_SCREENING_SURVIVOR`、`NO_VALIDATION_IMPROVEMENT`、`TARGET_SCORE_REACHED`、`BUDGET_EXHAUSTED`、`USER_CANCELLED`、`INFRASTRUCTURE_FAILURE`。

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

重试：模型调用指数退避最多三次；数据库死锁短退避；Patch 不合法、Gate Reject 不重试；Active 冲突终止实验，需基于新 Active 重启。

---

## 20. API

### 20.1 Skill

```http
POST   /api/v1/skills
GET    /api/v1/skills
GET    /api/v1/skills/{skill_id}
POST   /api/v1/skills/{skill_id}/import
GET    /api/v1/skills/{skill_id}/versions
GET    /api/v1/skills/{skill_id}/versions/{version_id}
GET    /api/v1/skills/{skill_id}/versions/{version_id}/files
GET    /api/v1/skills/{skill_id}/versions/{version_id}/diff?against={id}
POST   /api/v1/skills/{skill_id}/bindings
POST   /api/v1/skills/{skill_id}/rollback
```

创建请求：

```json
{
  "key": "kernel-log-analysis",
  "name": "Kernel Log Analysis",
  "source_path": "/path/to/skill",
  "invoke_as": "/kernel-log-analysis",
  "harness_key": "claude-skill",
  "install_relative_path": ".claude/skills/kernel-log-analysis",
  "editable_paths": ["SKILL.md", "references/**", "scripts/**", "tests/**"]
}
```

### 20.2 Experiment

```http
POST   /api/v1/skill-optimization/experiments
GET    /api/v1/skill-optimization/experiments
GET    /api/v1/skill-optimization/experiments/{id}
POST   /api/v1/skill-optimization/experiments/{id}/start
POST   /api/v1/skill-optimization/experiments/{id}/cancel
GET    /api/v1/skill-optimization/experiments/{id}/epochs
GET    /api/v1/skill-optimization/experiments/{id}/candidates
GET    /api/v1/skill-optimization/experiments/{id}/events
```

创建请求：

```json
{
  "name": "kernel-log-analysis-opt-001",
  "skill_id": "uuid",
  "base_skill_version_id": "uuid",
  "evaluation_target_id": "uuid",
  "benchmark_snapshot_id": "uuid",
  "split_snapshot_id": "uuid",
  "optimizer_policy_version_id": "uuid",
  "verifier_bundle_version_id": "uuid",
  "max_epochs": 5,
  "candidate_count": 2
}
```

### 20.3 Candidate

```http
GET  /api/v1/skill-optimization/candidates/{id}
GET  /api/v1/skill-optimization/candidates/{id}/patch
GET  /api/v1/skill-optimization/candidates/{id}/diff
GET  /api/v1/skill-optimization/candidates/{id}/comparison
POST /api/v1/skill-optimization/candidates/{id}/promote
POST /api/v1/skill-optimization/candidates/{id}/reject
```

### 20.4 事件与前端更新

事件：`experiment.status_changed`、`epoch.status_changed`、`candidate.created`、`candidate.static_rejected`、`candidate.screening_completed`、`candidate.validation_progress`、`candidate.gate_decided`、`skill.active_changed`、`experiment.completed`、`experiment.failed`。

```json
{
  "event_id": "uuid",
  "event_type": "candidate.gate_decided",
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
skill_optimization_managed_root = <workspace_root_path>/skill-optimization
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

Prompt 必须版本化，不能硬编码在业务函数：

```text
optimizer-prompts/
├── failure_analyst.md
├── success_analyst.md
├── generalization_analyst.md
├── simplification_analyst.md
├── merge_analysis.md
├── propose_candidate.md
└── summarize_decision.md
```

每个 Prompt 必须定义输入、输出 JSON Schema、禁止事项、隐藏答案隔离、editable paths、案例硬编码禁令、证据要求和退化风险。模型结果解析失败时只允许一次格式修复；仍失败则任务失败，不允许正则拼凑不可验证 JSON。

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
  "case_deltas": [],
  "overall_delta": 2.4,
  "median_delta": 2.0,
  "candidate_win_rate": 0.68,
  "candidate_win_probability": 0.97,
  "bootstrap_ci": {"lower": 0.6, "upper": 4.1},
  "family_deltas": {},
  "dimension_deltas": {},
  "runtime": {},
  "tokens": {}
}
```

Bootstrap Seed：

```text
sha256(experiment_id + candidate_id + gate_policy_hash)
```

结果中保存 Seed。

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

**Package Snapshot / Internal Git**：稳定哈希、排序、符号链接拒绝、路径穿越、重复哈希、大小限制、checkout 后哈希一致、内部仓库不修改用户 `.git`、禁用 hooks/submodule。

**Patch Applier**：四种操作、anchor 不唯一、old_text 不存在、越界路径、预算超限、整体回滚。

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

使用真实 claude Skill Harness 小数据集验证：把冻结版本复制到 `<workspace>/.claude/skills/<skill>`、cwd 正确、`/skill-name` 可被发现、独立 HOME、并发不污染、Candidate Report 正常评分、轮询状态更新、Diff 展示、Active 切换后普通评测使用新版本、历史评测仍读取旧版本。

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
request.evaluation_variant
> request.skill_version + evaluation_target
> target binding active
> legacy harness behavior
```

Active 变化不得改变历史 Run 展示。

---

## 30. 实施拆分

### 30.0 当前开发基线（2026-07-31）

第一条后端纵切已落在独立的 `src/analystbench/skill_optimization/` 包：

- 已实现包检查、稳定哈希、普通文件/符号链接/容量限制、AnalystBench 内部 bare Git、不可变版本、Diff 和 checkout 后哈希复核；
- 已实现 Skill、Version、Binding、EvaluationVariant、Experiment、Epoch、Candidate、Snapshot、Run Group、Signal、Comparison、Decision、Event 的 ORM 与 `0014_skill_optimization` 迁移；
- 现有 `EvaluationSubmissionService` 只增加可选 `EvaluationWorkspacePreparer` Protocol；应用工厂和 Worker 注入 Skill Adapter，功能关闭时保持旧行为；
- 已实现冻结版本安装到 `<workspace>/.claude/skills/<skill>`，且只复制声明的 Skill 包；
- 已实现结构化 Patch、文本预算、静态失败候选隔离、每轮两个候选、单次 Screening、三次完整配对验证、灰区自动追加到 5/7 次、逐 Case 中位数和确定性 Bootstrap；
- 已实现 Failure Family、Dimension、Failure Tag Evidence，优化器会读取当前 Evidence 和同实验 Rejected History，但不会读取 hidden/prospective holdout；
- 已实现硬回归 Gate、`provisional`/`validated` 判定、原子 Binding 晋升、下一 Epoch 新基线、连续两轮无 Screening survivor/无验证提升 Early Stop、取消、恢复与事件记录；
- Run Group 使用冻结配置哈希幂等复用；恢复时已完成或已排队的同实验组不会重复创建 Submission，配置漂移则拒绝复用；
- 已提供 `/api/v1/skills`、`/api/v1/evaluation-variants`、`/api/v1/skill-optimization/*` 后端接口，以及实验向导、Epoch 流、Evidence、候选、比较和 Diff 前端；
- 已通过确定性 `/skill` 命令契约并发 E2E：两个冻结版本分别安装到独立 `<workspace>/.claude/skills/<skill>`，并发执行且互不污染。真实 claude E2E 自动发现 PATH 中的 `claude`，也可由 `ANALYSTBENCH_REAL_CLAUDE` 显式指定。

代码闭环已经覆盖本轮列出的 V1 缺口。当前机器没有可发现的 `claude`
可执行文件，因此“真实 claude 二进制验收”和“真实四 Case 先导实验数据”
仍是环境/实验验收项，不能用确定性替身测试代替。下列清单据此区分“代码已完成”
与“仍需真实环境或数据验收”。

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

实现 Prompt Bundle、Optimizer Adapter、Structured Patch、Patch Applier、预算、静态检查、包内测试和 Diff UI。

验收：可生成两个候选；越界 Patch 拒绝；合法 Patch 创建不可变包；所有拒绝有错误码。

### Phase 5：筛选、完整验证和 Gate

实现 Screening、配对调度、中位数、Bootstrap、Gate、灰区增采样和比较页面。

验收：关键回归不能被总分抵消；Gate 可复现；灰区可追加到 5/7 次；展示 Case/Family/Dimension Delta；四 Case 模式只产生 `provisional` 判定，不显示统计显著。

### Phase 6：Promotion 与闭环

实现原子 Active、冲突处理、下一 Epoch、Early Stop、回滚、审计、指标和 E2E。

验收：最多五轮闭环；接受候选成为下一轮父版本；拒绝不影响 Active；任意已验证版本可回滚；Worker 重启可恢复。

### Phase 7：文档和实验

补充操作、API、配置文档，运行真实内核日志实验，并填写研究文档结果。

---

## 31. V1 验收清单

### 功能

- [x] 注册和导入 Skill；
- [x] 用户可配置 source path、invoke name、Harness Key 和 install relative path；
- [x] 每个 Skill 使用与用户仓库隔离的内部 Git；
- [x] 完整包不可变版本；
- [x] EvaluationVariant 绑定 Target 与 SkillVersion，并校验 Harness Key；
- [x] 运行前安装到隔离工作区的项目级 Skill 目录；
- [x] 候选运行隔离；
- [x] 最多五 Epoch；
- [x] 每轮两个候选；
- [x] 所有候选使用 Structured Patch；
- [x] 编辑预算生效；
- [x] 静态违规候选单独拒绝，不中止其他候选；
- [x] 基线/候选配对运行三次；
- [x] 灰区自动追加到 5/7 次；
- [x] Gate 支持硬约束和 Bootstrap；
- [x] 四 Case 开发模式标记 `provisional` 且不宣称显著性；
- [x] prospective holdout 不进入优化器输入；
- [x] Rejected Buffer 下一轮可检索；
- [x] 通过候选原子提升；
- [x] 可手动回滚；
- [x] Run Group 恢复复用和 Early Stop；
- [x] 前端查看 Diff、分数、Family/Dimension 退化和历史。

### 质量

- [ ] 核心代码满足项目覆盖率标准；
- [ ] Gate、Patch、状态机建议覆盖率不低于 90%；
- [ ] 所有 API 有权限检查；
- [x] 无路径穿越和符号链接逃逸；
- [x] 不修改 AnalystBench 仓库或用户源仓库的 Git 状态；
- [x] 并发候选不污染（确定性命令契约 E2E）；
- [x] 历史评测可复现；
- [ ] 真实 claude 二进制并发 E2E；
- [ ] 全量现有评测回归通过。

### 性能

- [ ] 快照和哈希不阻塞 API 主线程；
- [x] 大文件和文件数有上限；
- [ ] 前端详情分页加载；
- [x] 事件轮询更新；
- [x] 同实验 Run Group 缓存命中不重复执行。

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

只有以下全部满足才视为完成：

1. 真实 claude Skill 可从用户配置目录导入内部 Git 并形成不可变包；
2. 两个版本可分别安装到独立工作区的项目级 Skill 目录，并发评测且互不污染；
3. 真实 Benchmark 可生成 OptimizationSignal；
4. 优化器可生成受限 Patch；
5. 候选经过静态、筛选和三次配对验证；
6. Gate 可阻止关键类别回归；
7. 通过候选原子更新 Active；
8. 失败候选和原因可在前端复盘；
9. 新 Active 可进入下一 Epoch；
10. 任一历史已验证版本可回滚；
11. E2E 和现有系统回归测试通过；
12. 当前四 Case 能完成带 `provisional` 标记的先导实验；
13. 在新增独立 Holdout Case 前，不把先导结果写成论文主实验或泛化结论；
14. 研究文档第一组正式主实验结果在数据条件满足后补充。

---

## 附录 A：错误码

```text
SKILL_SOURCE_NOT_FOUND
SKILL_PACKAGE_TOO_LARGE
SKILL_FILE_TOO_LARGE
SKILL_TOO_MANY_FILES
SKILL_PATH_TRAVERSAL
SKILL_SYMLINK_ESCAPE
SKILL_HASH_CONFLICT
SKILL_INSTALL_PATH_INVALID
SKILL_INTERNAL_GIT_FAILED
SKILL_CHECKOUT_HASH_MISMATCH

PATCH_SCHEMA_INVALID
PATCH_PATH_NOT_EDITABLE
PATCH_ANCHOR_NOT_FOUND
PATCH_ANCHOR_AMBIGUOUS
PATCH_OLD_TEXT_NOT_FOUND
PATCH_OPERATION_FAILED
EDIT_BUDGET_EXCEEDED

STATIC_SECRET_DETECTED
STATIC_ABSOLUTE_PATH_DETECTED
STATIC_CASE_LEAK_DETECTED
STATIC_REFERENCE_MISSING
STATIC_SCRIPT_SYNTAX_FAILED
STATIC_PACKAGE_TEST_FAILED

SCREENING_REGRESSION
GATE_MIN_GAIN_NOT_MET
GATE_ERROR_TYPE_REGRESSION
GATE_ROOT_CAUSE_REGRESSION
GATE_UNSUPPORTED_CLAIM_REGRESSION
GATE_CRITICAL_FAMILY_REGRESSION
GATE_NEW_TIMEOUT
GATE_NEW_EMPTY_REPORT
GATE_NEW_EXECUTION_FAILURE
GATE_LATENCY_EXCEEDED
GATE_TOKEN_EXCEEDED
INCONCLUSIVE_AFTER_MAX_REPEATS
INSUFFICIENT_INDEPENDENT_VALIDATION_CASES

ACTIVE_CHANGED_CONFLICT
EXPERIMENT_BUDGET_EXHAUSTED
EXPERIMENT_CANCELLED
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
            finalize_epoch_rejected(epoch, "NO_SCREENING_SURVIVOR")
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
| 最大 Epoch | 5 |
| 每轮候选数 | 2 |
| Screening 重复数 | 1 |
| Full Validation 初始重复数 | 3 |
| 最大重复数 | 7 |
| 最大 Patch 操作 | 4，逐轮衰减 |
| 最大修改文件 | 2 |
| 最大新增 Token | 600 |
| 最大删除 Token | 300 |
| 最小 Overall Delta | +1.0 |
| Bootstrap 样本数 | 10000 |
| 置信度 | 95% |
| 启用统计自动门禁的最少独立 Validation Case | 8 |
| 最大关键家族退化 | -2.0 |
| 最大延迟增长 | 20% |
| 最大 Token 增长 | 20% |
| 连续无提升 Early Stop | 2 Epoch |
