# Skill 自优化私有环境运行手册

本文面向要在自己的私有环境中运行 Skill 自优化的人，覆盖从配置、预检、
创建实验到总账导出、版本导出和回滚的完整路径。

## 先明确边界

当前仓库已经实现本地代码闭环：不可变 Skill 版本、内部 Git、隔离安装、
结构化 Patch 与预算、静态验证、重复配对评测、Gate、Active 晋升、逐 Epoch
总账、导出、回滚、断点恢复和预检。

下列内容不是仓库中的替身测试可以证明的，必须由你在私有环境完成：

- 本机真实 `claude` 的登录、权限、版本和项目级 `/skill` 发现行为；
- Optimizer、被测 Target 和 Judge 都使用真实 CLI 的完整实验；
- 私有 Case 的分数、成本、耗时、稳定性和独立 Holdout 结果；
- 由真实实验数据支持的研究结论。

本文不会把“测试通过”写成“Skill 已真实提升”，也不会把
`development_regression` 的样本内结果写成泛化结论。

## 系统究竟会修改什么

```text
冻结 Harness.skill_base_dir/<skill-key>         （只作为导入来源）
                         │
                         ▼
             Managed Root 内部 Git v1
                         │
                 结构化 Patch 候选
                  ┌──────┴──────┐
                  ▼             ▼
            immutable v2a   immutable v2b
                  │
        Screening + paired Gate
                  │
            通过才切换 Binding
                  ▼
             Target 的 Active
```

优化器绝不直接编辑源 Skill。创建候选、验证失败、Gate 拒绝和回滚都不会
向 `Harness.skill_base_dir`、用户仓库或 AnalystBench 源码仓库写 commit。
候选内容只存在于 AnalystBench 的 Managed Root；提升只原子更新
`SkillTargetBinding.active_version_id`。如需把某个版本带走，先导出 ZIP，
人工审核后再自行处理，平台不会自动同步回源目录。

新导入版本的 `package_hash` 使用 v2 manifest：除路径、bytes 和大小外，还把
“是否可执行”的归一化 mode 以及固定 `ignored_paths` 规则纳入哈希。
setuid/setgid 文件在导入时拒绝。物化到
运行工作区后整包只读，但脚本保留只读可执行位（`0555`）；其他文件为
`0444`。已存 v1 manifest 仍按旧规则复核，新版本不再生成 v1。

## 1. 安装与目录

要求 Python 3.12+、Git、Node.js/npm，以及可运行的 `claude` CLI。Linux/WSL 还应
安装 `bubblewrap` (`bwrap`)：严格预检会
实际创建 namespace 来确认它可用，声明了包内测试的候选必须在该无网络
沙箱中通过。

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
sudo apt-get update
sudo apt-get install -y bubblewrap
cd src/frontend
npm install
cd ../..
```

`apt-get` 示例适用于 Ubuntu/WSL；其他 Linux 发行版使用对应包管理器。
已安装 `bwrap` 不等于当前 WSL/容器允许 user namespace，后续以预检结果为准。

### 1.1 隔离 HOME 与认证

Optimizer、Target 和 Judge 命令均继承 Worker 的显式环境变量，但运行时会把
`HOME`、`USERPROFILE`、XDG 目录和 `CLAUDE_CONFIG_DIR` 重定向到每次命令
自己的临时目录。这能防止候选之间共享
配置和缓存，但不会复制服务用户真实 HOME 中的交互式登录。

私有环境应把 CLI 需要的认证以明确环境变量传给 API/Worker，或使用一个受控
wrapper 可执行文件完成认证。不要把完整用户 `.claude/`、`.config/`、hooks 或
其他 Skill 复制进隔离 HOME。`claude --version` 只证明二进制
可执行，不证明实际模型请求已认证；完整实验前需从同一 Worker 环境做一次
最小真实请求。

这是“进程环境与用户状态隔离”，不是通用文件系统或网络沙箱。Optimizer、
Target 和 Judge 仍会按 Worker 的操作系统权限运行；只有声明式 Skill 包内测试
额外使用 bubblewrap 的只读、无网络 namespace。

为 Managed Root 选择一个专用绝对路径。它不能是：

- 用户源 Skill 目录或其子目录；
- AnalystBench 源码仓库；
- `results`、Worker workspace 或临时结果目录。

```bash
mkdir -p /srv/analystbench/skill-optimization
```

在项目根目录的 `.env.local` 中配置：

```dotenv
ANALYSTBENCH_SKILL_OPTIMIZATION_ENABLED=true
ANALYSTBENCH_SKILL_OPTIMIZATION_MANAGED_ROOT=/srv/analystbench/skill-optimization
```

不要在 `.env.local` 中写 `$(pwd)` 或依赖 shell 展开的相对路径；这里需要真实
绝对路径。API、Worker、CLI 和测试命令必须读取同一数据库及同一配置。

## 2. 升级数据库并预检基础环境

```bash
.venv/bin/analystbench db-upgrade
.venv/bin/analystbench skill-opt preflight --strict
```

预检不创建实验、不调用 Optimizer、不跑 Case。它检查功能开关、Managed Root
绝对路径与可写性、Git、受支持 CLI、bubblewrap namespace、数据库迁移、核心表和磁盘空间。
`FAIL` 总是返回非零退出码；加 `--strict` 后 `WARN` 也返回非零，适合验收脚本。
`package_test_sandbox=WARN` 表示未声明包内测试的 Skill 仍可运行，但声明了测试的
候选会被稳定错误 `skill_package_test_sandbox_unavailable` 静态拒绝。

## 3. 配置源 Skill、Harness 和 Target

假设 Skill Key 是 `my-skill`。把源 Skill 放在：

```text
/private/claude-config/
└── skills/
    └── my-skill/
        ├── SKILL.md
        ├── references/       可选
        ├── scripts/          可选
        └── tests/            可选
```

在前端“设置 → Harness”中创建 Harness：

- `skill_base_dir`：`/private/claude-config/skills`，即直接包含 `my-skill` 等子目录的根目录；
- command template：必须明确包含 `/my-skill`，例如
  `claude -p "/my-skill 分析 {input}"`；
- 如命令包含 `{model}`，同时创建 Model；否则使用无 Model Harness；
- 先“检测”，再“冻结”。

冻结 Harness 后，在设置页点击“新建 Skill”，先选择该 Harness；页面只在此时
扫描它的 `skills/*/SKILL.md`，用户从下拉框选择一个已有 Skill 并保存。
这个“新建”只建立 AnalystBench 内部配置和不可变版本，不会在宿主机创建目录。
测评和自优化页面随后直接选择 `Harness × Model × Skill` 组合。后端根据冻结
Harness 和所选目录名自动得到：

| 字段 | 派生值 |
|---|---|
| 源目录 | `<skill_base_dir>/my-skill` |
| `name` | `my-skill` |
| `invoke_as` | `/my-skill` |
| 安装路径 | `.claude/skills/my-skill` |
| Harness Key | 所选冻结 Harness 的 key |

不手填 Skill Key、源目录或调用名。Target 的 Harness Key、Skill 的 Harness Key 和命令
中的 `/my-skill` 必须一致。同一 Target 可以有多个 Skill 绑定；每次普通测评、
定时测评和自优化都携带明确的 Skill 选择，因此不会在多个 Active Skill 间猜测。
运行时，平台从内部 Git checkout 指定版本并只读
安装到每次运行自己的 `<workspace>/.claude/skills/my-skill`，并向目标
命令注入 `ANALYSTBENCH_SKILL_VERSION_ID=<实际冻结版本 UUID>`。该环境变量用于
审计/调试，不代替 Submission Manifest、EvaluationVariant、package hash 和内部 Git
commit 这些持久化运行身份。

如果不用普通向导而要完全脚本化，以下 `curl` 均要求 API 已就绪；先在另一
终端运行 `.venv/bin/analystbench serve`（完整服务方式见第 7 节）。然后按
同一规则选择并纳管宿主机 Skill，保存返回的 `skill.id` 和 `initial_version.id`：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/host-skills:adopt \
  -H 'Content-Type: application/json' \
  -d '{
    "harness_id": "FROZEN_HARNESS_ID",
    "key": "my-skill"
  }'
```

## 4. 准备 Case

Case 使用正式结果目录中的三段相对路径：

```text
<test-set>/<category>/<case-key>
```

每个目录至少需要有效 `case.json` 和 `logs/` 中的日志。Snapshot 会冻结：

- 问题输入和全部日志的内容哈希；
- Eval Spec/Reference 的内容哈希；
- 每个 Case 的 `source_group_key`。

开始、恢复和推进实验时都会复核哈希。创建 Snapshot 后如修改 Case、日志或
Eval Spec，实验会以 drift 错误停止；应创建新 Snapshot 和新实验，不能让旧
实验悄悄使用新数据。

## 5. 创建冻结 Optimizer 与 Verifier

普通前端向导会自动完成本节。完全脚本化时，创建、探测并冻结 Optimizer
Execution Profile：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/execution-profiles \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "my-skill-optimizer",
    "runner": "claude",
    "configuration": {
      "executable": "/absolute/path/to/claude",
      "timeout_seconds": 1800,
      "max_output_bytes": 10485760,
      "environment_mode": "local",
      "allowed_tools": ["Read", "Grep", "Glob"]
    }
  }'
curl -sS -X POST \
  http://127.0.0.1:8000/api/v1/execution-profiles/PROFILE_ID:validate
curl -sS -X POST \
  http://127.0.0.1:8000/api/v1/execution-profiles/PROFILE_ID:freeze
```

只有 `validate` 返回 `available: true` 才继续。该探测只验证可执行文件和版本
命令，不验证模型认证、配额或权限；私有验收仍需在同一 Worker 环境运行最小
真实请求。然后冻结 Policy 和 Verifier：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/skill-optimization/policies \
  -H 'Content-Type: application/json' \
  -d '{
    "key": "my-skill-optimizer-v1",
    "execution_profile_id": "PROFILE_ID",
    "prompt_bundle": {
      "instruction": "根据失败证据做小步、通用、可迁移的优化，不写入具体 Case 答案。"
    },
    "config": {}
  }'

curl -sS -X POST http://127.0.0.1:8000/api/v1/skill-optimization/verifiers \
  -H 'Content-Type: application/json' \
  -d '{
    "key": "my-skill-verifier-v1",
    "static_policy": {},
    "gate_policy": {
      "min_overall_delta": 1.0,
      "max_latency_growth": 0.2,
      "max_token_growth": 0.2,
      "minimum_independent_validation_cases": 8,
      "bootstrap_samples": 2000,
      "bootstrap_confidence": 0.95,
      "min_candidate_win_probability": 0.0,
      "require_bootstrap_lower_bound_positive": true
    },
    "judge_config": {"runner": "claude"}
  }'
```

Verifier 版本会一起冻结静态策略、Gate/统计策略与完整 Judge 配置。实际
Judge runner/configuration 进入每个 Run Group 的 config hash；中断恢复时任何漂移都会
拒绝复用。Full Validation 的 Bootstrap 以 Case 为重采样单元，根据实验、Epoch
和 Candidate 身份导出稳定 Seed，并把最终 Seed 保存在比较/Gate 结果中。
每个 Validation repeat 另外根据 Experiment/Epoch/Candidate/Repeat 生成
稳定 `pair_seed`，交错选择 baseline→candidate 或 candidate→baseline 的运行
创建顺序；`pair_seed` 和 `pair_position` 一起进入冻结 run config hash
和 Submission context。
底层 Submission 同时使用 `skillopt:<run_config_hash>` 幂等键；即使 Worker 在创建
Submission 后、写入 Run Group 前崩溃，恢复也会取回原批次，不重复调用模型。

当前 Optimizer pipeline 使用同一个冻结 claude Profile 依次运行
`failure_analyst`、`success_analyst`、`generalization_analyst` 和
`simplification_analyst` 四个显式版本角色。它们只看有界的 Train/Development
聚合证据和同实验 Rejected History，输出必须匹配
`structured_skill_patch.v1`。只支持 `append`、`insert_after`、
`replace`、`delete`；`create`、`old_text`、多余字段、unified diff 和
shell 命令会被拒绝。非法 JSON 只做一次同 Runner 格式修复；
`AgentRunnerError` 最多尝试三次，重试前分别退避 1/2 秒。各角色提案
按固定角色顺序 round-robin，以 canonical patch hash 去重后取满候选数。

空 `static_policy` 不表示关闭检查，而是使用严格默认 Patch 预算和五项静态验证。
如果要声明 Skill 包内测试，`tests/` 必须存在，并在 Skill 自己的
`manifest.json` 中提供参数数组：

```json
{
  "package_tests": {
    "argv": ["python", "-m", "pytest", "-q", "tests"],
    "timeout_seconds": 30
  }
}
```

只支持受限 Python/pytest argv，不经 shell 执行；包内测试运行时 Skill 副本只读、
环境清空、禁止网络和子进程。未声明 `package_tests.argv` 会记录
`not_configured`，即使目录中存在 `tests/` 也不会隐式运行或伪装成已运行。

## 6. 先跑上下文预检

选择并纳管宿主机 Skill、冻结 Target、创建 Optimizer Execution Profile/Policy 和 Snapshot
后，使用实际标识再次预检：

```bash
.venv/bin/analystbench skill-opt preflight --strict \
  --skill-key my-skill \
  --target-id TARGET_ID \
  --profile-id PROFILE_ID \
  --policy-id POLICY_ID \
  --verifier-id VERIFIER_ID \
  --snapshot-id SNAPSHOT_ID \
  --case-path test-set/category/case-1 \
  --case-path test-set/category/case-2
```

这里会进一步检查 Skill 来源、Harness `skill_base_dir`、Target 冻结状态、
`/my-skill` 是否出现在命令中、Target 实际命令与 Optimizer Profile 要求的
Runner 是否可执行、Verifier Judge runner、Snapshot 内容和 Case 日志。也可在
实验详情页点击“环境预检”，或调用：

```http
POST /api/v1/skill-optimization/preflight
```

## 7. 启动服务和前端

最简单的方式是让一个命令同时启动 API 与 Worker：

```bash
.venv/bin/analystbench serve
```

后台运行：

```bash
.venv/bin/analystbench serve --detach
.venv/bin/analystbench service status
.venv/bin/analystbench service logs
```

需要拆分排障时使用两个终端：

```bash
# 终端 1
.venv/bin/analystbench api --host 127.0.0.1 --port 8000

# 终端 2
.venv/bin/analystbench worker
```

第三个终端启动前端：

```bash
cd src/frontend
npm run serve
```

确认：

```text
API readiness  http://127.0.0.1:8000/api/v1/health/ready
Swagger        http://127.0.0.1:8000/docs
前端           http://127.0.0.1:5173/skill-optimization
```

## 8. Development Regression 运行

普通前端三步向导支持在 `development_regression` 和
`independent_validation` 之间选择。开发回归的操作为：

1. 从下拉框选择 `Harness × Model × Skill` 宿主机组合；
2. 选择 `Development Regression`，并勾选日志就绪的
   Case；
3. 选择 Optimizer、可执行文件、Judge、阈值和最大 Epoch；
4. 第一次用真实 CLI 时将 `max_epochs` 设为 `1`；
5. 点击“创建并启动”。

这种模式将所选已知 Case 用于 Evidence、Screening 和完整回归验证。它能发现
明显退化、验证状态机与 Gate，但结论只能是 `provisional`。三次重复降低随机
波动，不会把 4 个 Case 变成 12 个独立样本，也不能证明泛化。

每轮默认生成两个候选，Screening 只留下一个进入完整配对验证。初始重复数为
3，灰区自动增加到 5/7；连续两轮无 Screening survivor 或无验证提升会 Early
Stop。只有 Gate 的最小提升、关键退化、执行失败、时延和 Token 约束都通过，
候选才切换为 Active。

当前目标 Harness 不保证返回 provider 的真实 usage，因此 Method Run artifact 固定
持久化 `token_count=ceil(最终 stdout 字符数/4)` 和
`token_count_source=approximate_output_characters`。它是可复现的“输出报告规模”
估算，不是输入+输出总 Token 或账单用量。Full Gate 要求每个配对 Case 的基线和
候选都有该值；缺失时以 `token_usage_missing` 硬拒绝，平均 Case 中位估算值的增长
超过 `max_token_growth` 时以 `token_growth_exceeded` 硬拒绝，不会静默跳过。

每个 Case 还会对重复运行的 `forbidden_hit_count` 和 `missing_chain_count`
分别取中位数。候选的任意一项高于基线即以
`candidate_guardrail_metric_increased` 硬拒绝；不再只把“是否出现过”压成一个
Failure Tag，也不允许总分抵消这类退化。

每个 Epoch 的目标 Agent 报告生成次数为：

```text
(baseline + 2 个候选) × Train/Development Case × 1 次 Screening
+ (baseline + 入选 candidate) × Validation/Development Case × repeats
```

因此 4 个 Development Case、2 个候选、3 repeats 时是 `12 + 24 = 36` 次目标
Agent 报告生成。
每从 3 增采样到 5、或从 5 到 7，都再增加 `4 × Validation Case 数`
次目标报告生成。还需另计四个角色的 Optimizer 调用（及可能的
格式修复/执行重试）和每份报告的 Judge 调用；所有三者都是 claude 时，
“36 次报告生成”不等于“36 次 claude 总调用”。

## 独立 Train/Validation 运行

普通前端向导已提供切分编辑器：选择 `Independent Validation`，然后将每个
Case 分配到 Train、Validation、Hidden、Prospective 或不纳入。也可用下面的
Swagger/API 完全脚本化。先保证：

- Train 与 Validation 路径不重叠；
- 同一原始事件的变体使用相同 `source_group_key`，且不能跨 split；
- Validation 至少达到
  `ANALYSTBENCH_SKILL_OPTIMIZATION_MINIMUM_INDEPENDENT_VALIDATION_CASES`
  （默认 8）；
- Train 只用于 Evidence 和 Screening；Gate 的完整配对比较使用 Validation；
- 一个 `independent_validation` Experiment 只允许一个 Epoch；前端会锁定
  `max_epochs=1`，后端也会拒绝其他值；
- Hidden/Prospective 只冻结与隔离，当前闭环不会自动运行最终 Hidden Test。

创建 Snapshot：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/skill-optimization/data-snapshots \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_key": "test-set",
    "mode": "independent_validation",
    "train_case_paths": [
      "test-set/train/case-1",
      "test-set/train/case-2"
    ],
    "validation_case_paths": [
      "test-set/validation/case-101",
      "test-set/validation/case-102",
      "test-set/validation/case-103",
      "test-set/validation/case-104",
      "test-set/validation/case-105",
      "test-set/validation/case-106",
      "test-set/validation/case-107",
      "test-set/validation/case-108"
    ],
    "hidden_test_case_paths": [],
    "prospective_holdout_case_paths": []
  }'
```

用返回的 Snapshot ID、当前 Active 版本和已冻结 Policy/Verifier 创建实验：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/skill-optimization/experiments \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "my-skill-independent-001",
    "skill_id": "SKILL_ID",
    "base_skill_version_id": "CURRENT_ACTIVE_VERSION_ID",
    "evaluation_target_id": "TARGET_ID",
    "data_snapshot_id": "SNAPSHOT_ID",
    "optimizer_policy_version_id": "POLICY_ID",
    "verifier_bundle_version_id": "VERIFIER_ID",
    "max_epochs": 1
  }'
```

如该 Skill/Target 已有 Binding，`base_skill_version_id` 必须等于当前 Active，
不能从任意候选或过期版本旁路启动。最后启动：

```bash
curl -sS -X POST \
  http://127.0.0.1:8000/api/v1/skill-optimization/experiments/EXPERIMENT_ID:start
```

独立 Snapshot 是一次性 Validation 身份：可以先创建引用它的草稿 Experiment，
但只有第一个成功启动的 Experiment 能原子消费该 Snapshot；其他引用者之后启动
会收到 `optimization_independent_snapshot_consumed`。不能把同一 Validation Snapshot
反复用于新实验选择候选。需要新一次独立验证时，准备未被
用于先前决策的 Case，并创建新 Snapshot。

满足独立 Case 数和统计门禁才可能产生 `validated`；这是当前
Skill × Target Binding 的 `active_level`，仍不是 Hidden Test 结果。
最终 Hidden/Holdout 必须在候选完全冻结后另行运行，并且不得把结果反馈给同一
候选继续优化，否则它不再是 Hidden。

## 9. 看清每轮做了什么、升降多少分

实验详情页的“优化总账”显示初始分数、`ACTIVE PATH SCORE`、累计 Delta 和
Promoted/Retained 轮数。每个 Epoch 固定记录：

- 父版本、选中候选和最终 Active 版本；
- Optimizer 声明的 rationale、目标 Failure Family/Dimension；
- 实际修改文件、操作类型/数量、增删 Token、Patch 哈希和静态验证；
- Baseline、Candidate、本轮正负 Delta、累计 Delta；
- 逐 Case、Family、Dimension 的升降；
- Screening、完整验证、Gate 判定、拒绝原因和 Active 决策。

分数来自持久化的配对评测结果，不采用 Optimizer 自报分数。
`ACTIVE PATH SCORE` 是总账中的链式审计量：

```text
initial baseline score + 所有已 promote Epoch 的 paired delta
```

被拒绝/保留候选的 Delta 不进入 Active Path。该值不是对最终 Active 另做的一次
全新重测，也不保证等于某个完整验证中的候选绝对分；每轮基线与候选是独立
配对采样，绝对分可以因非确定性漂移。因此研究报告应同时保留每轮 Baseline、
Candidate 和 Delta，不把 Active Path Score 写成独立 Holdout 分数。

某轮没有合法的完整比较时，该轮可显示明确标记的 Screening 比较供诊断；
它不能触发 Promotion，也不能进入 Active Path 累计。完全没有合法比较时分数
显示为空，而不是伪造 `0`。

优化基线/候选 repeat 会复用正式生成和评分链，但产物是运维实验数据：
`result.json` 强制标记 `included_in_statistics=false` 和
`result_purpose=skill_optimization`，普通结果/排行榜统计与普通 Submission 列表均
默认排除。这些底层 Submission 只能由 Experiment 状态机管理；普通取消/删除 API
会以 `optimization_submission_managed_by_experiment` 拒绝，避免破坏总账和恢复身份。

详情 API 支持 Epoch 分页：

```text
GET /api/v1/skill-optimization/experiments/EXPERIMENT_ID/detail?epoch_offset=0&epoch_limit=20
```

完整机器可读总账：

```text
GET /api/v1/skill-optimization/experiments/EXPERIMENT_ID/ledger
```

## 10. 导出实验总账

前端实验详情提供 JSON、Markdown 和 CSV 按钮。CLI 使用：

```bash
.venv/bin/analystbench skill-opt ledger EXPERIMENT_ID \
  --format json --output ./artifacts/experiment.json
.venv/bin/analystbench skill-opt ledger EXPERIMENT_ID \
  --format markdown --output ./artifacts/experiment.md
.venv/bin/analystbench skill-opt ledger EXPERIMENT_ID \
  --format csv --output ./artifacts/experiment.csv
```

输出文件已存在时命令拒绝覆盖；确认后显式加 `--overwrite`。HTTP 下载入口：

```text
GET /api/v1/skill-optimization/experiments/EXPERIMENT_ID/export?format=json
GET /api/v1/skill-optimization/experiments/EXPERIMENT_ID/export?format=markdown
GET /api/v1/skill-optimization/experiments/EXPERIMENT_ID/export?format=csv
```

## 11. 导出不可变版本 ZIP

前端 Managed Skill 版本列表可以下载任一版本。CLI：

```bash
.venv/bin/analystbench skill-opt version-export SKILL_ID VERSION_ID \
  --output ./artifacts/my-skill-v2.zip
```

ZIP 是从内部 Git 指定 commit 重新 checkout、复核 package hash 后生成的确定性
制品，并带 `.analystbench/version-manifest.json`。ZIP 条目也保留只读执行语义：
原可执行文件为 `0555`，其他文件为 `0444`。导出不会改变 Active，也不会
写回源 Skill。

## 12. 显式回滚

只允许回滚到曾在同一个 Skill × Target Binding 上激活过的历史版本。先读取
当前 Binding 的 `lock_version` 和历史：

```text
GET /api/v1/skills/SKILL_ID/bindings
GET /api/v1/skills/SKILL_ID/binding-history?evaluation_target_id=TARGET_ID
```

然后执行：

```bash
.venv/bin/analystbench skill-opt rollback \
  SKILL_ID TARGET_ID HISTORICAL_VERSION_ID \
  --expected-lock-version CURRENT_LOCK_VERSION \
  --reason "private acceptance rollback" \
  --yes
```

`--yes` 是必要的显式确认。若回滚前 Active 已被另一进程改变，乐观锁会拒绝
请求；重新读取 Binding 后再决定，不能盲目重试旧操作。回滚只改变 Binding，
不会删除任何版本，也不会覆盖源 Skill。目标版本不必是
`validated`；可回滚集合是“同一 Skill × Target Binding 上曾经 Active”的版本，
并恢复它当时记录的 `active_level`。

晋升或回滚后，不显式指定 Variant/Version、而是按 Target 创建的下一次普通评测
会解析该 Binding 的当前 Active，并把具体 `EvaluationVariant × SkillPackageVersion`
冻结进 Submission。后续 Active 变化不会改变该历史 Submission。

## 13. 本地确定性测试

先跑 Skill 自优化聚焦测试：

```bash
.venv/bin/pytest -q -s \
  tests/test_skill_optimization.py \
  tests/test_skill_optimization_optimizer_pipeline.py \
  tests/test_skill_optimization_patch_policy.py \
  tests/test_skill_optimization_static_validation.py \
  tests/test_skill_optimization_reporting.py \
  tests/test_skill_optimization_preflight.py \
  tests/test_skill_optimization_cli.py \
  tests/test_skill_optimization_surface.py \
  tests/test_skill_version_lifecycle.py \
  tests/test_skill_optimization_e2e.py \
  tests/test_evaluation_submission.py \
  tests/test_direct_results_routes.py \
  tests/test_agent_runner.py \
  tests/test_worker.py \
  tests/test_migrations.py
```

`tests/test_skill_optimization_static_validation.py` 中需要真实 namespace 的 bubblewrap
E2E 默认跳过，避免把外层容器/Codex 沙箱不允许 namespace 误报为业务失败。
只在待验收的私有 Worker 宿主显式运行：

```bash
ANALYSTBENCH_RUN_BWRAP_E2E=1 \
  .venv/bin/pytest -q -s tests/test_skill_optimization_static_validation.py
```

此命令应包含“正常包内测试通过”和“试图访问外部文件/网络时被沙箱拒绝”
的用例。如报 `Operation not permitted` 或 namespace 创建失败，先修复 WSL/宿主权限；
不要关闭沙箱来让候选测试通过。

再跑全量后端与本功能静态检查：

```bash
.venv/bin/pytest -q -s
.venv/bin/ruff check \
  alembic/env.py \
  alembic/versions/0017_skill_optimization_ledger.py \
  alembic/versions/0018_evaluation_submission_idempotency.py \
  src/analystbench/skill_optimization \
  src/analystbench/execution/isolation.py \
  src/analystbench/execution/runner.py \
  src/analystbench/evaluation/direct.py \
  src/analystbench/evaluation/submission.py \
  src/analystbench/runtime/jobs.py \
  src/analystbench/worker.py \
  src/analystbench/api/routes/direct_results.py \
  src/analystbench/api/routes/skill_optimization.py \
  src/analystbench/api/routes/skills.py \
  src/analystbench/cli.py \
  src/analystbench/db/models.py \
  tests/test_skill_optimization*.py \
  tests/test_skill_version_lifecycle.py \
  tests/test_worker.py tests/test_agent_runner.py \
  tests/test_direct_results_routes.py tests/test_migrations.py
```

若要复核当前 V1 的核心风险覆盖率，至少对
`analystbench.skill_optimization.gate`、`patch` 和 `experiment` 生成
`pytest-cov` 报告；当前开发验收结果分别为 Gate 95%、Patch 98%、状态机
可执行区间 90.3%。`experiment.py` 还包含 CRUD、分页、详情和兼容读取面，因此
整文件覆盖率（当前 80.0%）不能替代状态机区间口径。

WSL 中如 pytest capture 在 teardown 出现与业务无关的临时文件错误，保留
`-s` 重跑并以实际测试断言为准，不要把 capture 故障误判为功能失败。

前端聚焦测试与构建：

```bash
cd src/frontend
node --test tests/optimization-dialog.test.mjs tests/optimization-ledger.test.mjs
npm run build
npm run test:sites
```

这些命令证明本地代码契约，不证明私有 claude 或私有数据效果。

## 14. 真实 claude 私有验收

先确认同一运行用户可调用 CLI，并从 API/Worker 将继承的环境发起一次最小真实
模型请求。具体请求参数依你锁定的 CLI 版本而定；`--version` 不是认证测试：

```bash
/absolute/path/to/claude --version
.venv/bin/analystbench skill-opt preflight --strict
```

若当前认证只存在普通 `~/.claude` 中，先改成 Worker 可继承的显式认证或受控
wrapper；不要把整个配置目录复制到临时 HOME。

运行真实 `/skill` 发现与并发隔离 E2E：

```bash
ANALYSTBENCH_REAL_CLAUDE=/absolute/path/to/claude \
  .venv/bin/pytest -q -s \
  tests/test_skill_optimization_e2e.py::test_real_claude_slash_skill_concurrent_e2e
```

这条 E2E 只证明真实 claude 能发现两个隔离工作区中的冻结 Skill 版本并且并发
不串包；它不证明完整优化有提升。完整私有验收还需要：

1. 用真实 Optimizer、Target 和 Judge 创建 `max_epochs=1` 实验；
2. 保存预检 JSON、实验总账三种导出、候选版本 ZIP 和服务日志；
3. 核对每轮修改、逐 Case 分数和 Gate 原因与原始结果一致；
4. 验证 Gate 拒绝时 Active 不变，通过时只切换 Binding；
5. 按同一 Target 发起一次普通评测，确认 Submission 冻结的是新 Active
   Variant/Version；
6. 执行一次显式回滚，再发起新的普通评测，并确认新 Submission 使用回滚
   版本、之前的历史 Run 仍指向原版本；
7. Development Regression 可再扩大 Epoch/Case；Independent Validation 必须保持单
   Epoch 且不重用已消费的 Snapshot；
8. 最后以未暴露的 Holdout 单独评估，不使用优化 Experiment 接口。例如在
   Final Active 和重复次数已预注册后，为每次重复发起一个只包含 Holdout Case 的
   普通 Submission：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/evaluation-submissions \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_key": "test-set",
    "case_paths": ["test-set/hidden/case-901"],
    "target_ids": ["TARGET_ID"],
    "judge_runner": "claude"
  }'
```

9. 一旦读取 Holdout 结果，就把该集合登记为已暴露，不再用于调整同一候选；
10. 研究文档只填写实际运行所得数据、硬件/CLI/模型版本和失败样本。

## 15. 常见故障

| 现象或错误 | 常见原因 | 处理 |
|---|---|---|
| `skill_optimization_disabled` / `feature_switch=FAIL` | 开关未启用，或服务未重启 | 修改项目根 `.env.local`，重启 API 与 Worker |
| `managed_root_configured/absolute/writable=FAIL` | 未显式配置、用了相对路径、目录不可写 | 改为专用绝对路径，创建目录并检查当前运行用户权限 |
| `git_executable=FAIL` | Worker 的 PATH 找不到 Git | 在实际服务用户环境安装/配置 Git，重启后预检 |
| `agent_runners=FAIL` | claude 未安装或版本命令失败 | 用服务用户直接执行 `claude --version`，确认 PATH；通过后仍要另做最小真实认证请求 |
| `package_test_sandbox=WARN` | `bwrap` 缺失，或当前 WSL/容器不允许 namespace | 安装 bubblewrap，并用实际 Worker 用户运行预检；严格验收不应忽略 WARN |
| `skill_package_test_sandbox_unavailable` | Skill 声明了 `package_tests.argv`，但安全沙箱不可用 | 修复 Worker 宿主的 bubblewrap/namespace；不要关闭测试或沙箱来绕过 |
| `database_migration_head=FAIL` | 未升级或 API/CLI 指向不同数据库 | 停止服务，确认配置后运行 `db-upgrade` |
| `harness_skill_directory` / `skill_source_directory=FAIL` | `skill_base_dir` 指错层级，或缺少 `<key>/SKILL.md` | Harness 直接配置包含各 Skill 子目录的根目录 |
| `evaluation_target_frozen=FAIL` | Target/Harness 尚未检测和冻结 | 在设置页完成 probe 和 freeze |
| `skill_invocation_in_harness=FAIL` | Target 命令没有精确 `/skill-key` | 修订 Harness 生成新版本，再冻结新 Target |
| `evaluation_target_skill_binding_conflict` | 同一 Target 已绑定另一个 Active Skill | 使用正确 Target；V1 不在普通评测时隐式选择多 Skill |
| `optimization_base_not_active` | 用旧版本或未晋升候选作为新实验基线 | 读取当前 Binding Active；如确需旧版，先显式回滚 |
| `optimization_independent_validation_epoch_limit` | 独立验证设置了多个 Epoch | 将 `max_epochs` 设为 `1`；不用同一 Validation 反复选择候选 |
| `optimization_independent_snapshot_consumed` | 另一个实验已成功启动并消费该 Independent Snapshot | 为新的未暴露 Validation Case 创建新 Snapshot；不复用旧验证集调参 |
| `optimization_split_overlap` | 同一 Case 出现在多个 split | 修正 Snapshot 列表 |
| `optimization_source_group_overlap` | 同源事件的不同裁剪跨 Train/Validation | 统一 `source_group_key` 并放到同一 split |
| `optimization_case_input_drift` / `optimization_eval_spec_drift` | Snapshot 后修改了日志、问题或评分标准 | 保留旧实验审计，创建新 Snapshot 和新实验 |
| Patch/Static candidate 被拒绝 | 越界路径、操作/文件/Token/比例超预算，或安全、引用、语法、包内测试失败 | 在候选详情看稳定错误码；修正 Skill/Policy，勿放宽到绕过安全边界 |
| Gate 未提升 | 最小增益不足、关键 Case/Family/Dimension 回归、执行失败、耗时或 Token 超限 | 查看 Epoch 总账和逐 Case Delta；旧 Active 会保持不变 |
| `token_usage_missing` / `token_growth_exceeded` | 配对 artifact 缺少输出规模估算，或候选增长超阈值 | 检查所有基线/候选 Method Run artifact；不要把缺失 usage 当作 0 |
| `candidate_guardrail_metric_increased` | 任一 Case 的 `forbidden_hit_count` 或 `missing_chain_count` 重复中位数上升 | 查看逐 Case 比较并修正候选；该硬回归不能由总分抵消 |
| `optimization_submission_managed_by_experiment` / `optimization_result_managed_by_experiment` | 试图通过普通 API 取消、删除或改写优化产物 | 通过 Experiment 状态机管理；先导出总账，不破坏恢复身份 |
| `skill_binding_conflict` | 晋升/回滚期间 Active 被并发改变 | 重新读取 Binding 和 `lock_version` 后重新决策 |
| 实验中断 | API/Worker 重启、CLI 失败或环境漂移 | 查 `service logs`，修复后点“从断点恢复”；完整且哈希一致的 Run Group 会复用 |
| 前端请求失败 | API 未 ready 或代理目标错误 | 检查 `/health/ready`；必要时设置 `VITE_API_TARGET=http://127.0.0.1:8000` 后重启前端 |

不要用删除数据库、清空 Managed Root、直接编辑内部 Git 或覆盖源 Skill 的方式
“修复”实验状态。先导出审计材料，再根据稳定错误码处理。
