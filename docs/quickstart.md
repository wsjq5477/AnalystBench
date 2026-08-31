# AnalystBench 快速上手

AnalystBench 支持三种使用方式，选择适合你的路径：

| 路径 | 适合谁 | 需要什么 | 是否需要数据库 |
|------|--------|---------|---------------|
| **A. 单次打分与测评** | 只想评分几份报告，出结果就走 | Case JSON + 报告文件 | ❌ 不需要 |
| **B. 数据库部署与前端支持** | 需要版本管理、批量评测、前端 UI | 本地数据库 + API 服务 | ✅ 需要 |
| **C. Skill 自优化（实验功能）** | 用 Benchmark 自动迭代本地 claude Skill | 路径 B + claude Target + claude Optimizer/Judge | ✅ 需要 |

> 路径 A 和 B 使用相同评分算法；路径 C 复用路径 B 的数据库、Evaluation
> Submission、Worker 和评分链，只在外层增加 Skill 版本、重复对比和 Gate。

---

# 路径 A：单次打分与测评

## A1. 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

已有 `.venv` 时跳过前两步。

## A2. 准备 Case JSON

Case JSON 包含人工标准答案和评分标准。已有 JSON 文件时直接用；没有时按以下方式生成：

### 方式 1：claude Skill 生成（推荐）

```text
/analystbench-case 将以下人工标准答案转换为 Case JSON：
问题分类：HM_PANIC_SYSMGR
问题根因：调度问题，开抢占未REPICK
……
```

### 方式 2：让模型自行生成

让模型阅读 [scoring-input.md](scoring-input.md)，按格式输出 JSON。重点确认：

- 只有一个根因（`id: "root"`，权重 100）、一个问题分类（`id: "category"`，权重 20）
- 每组"证据N/结论N"形成一个分析链（`id: "chain-1"` 等，等分合计 60）
- `quote` 必须是标准答案中的连续原文
- 不把 AI 报告内容写进标准答案
- 不设置"直接原因"评分项

## A3. 准备 AI 报告

AI 报告直接保存为 UTF-8 的 `.txt`、`.md` 或 `.log` 文件即可，不需要转换成 JSON。

推荐命名格式：`<case>-test<序号>-<native|skill|agent>-<次数>.md`

例如 `HM_PANIC_SYSMGR-test1-agent-1.md`。项目会自动解析运行类型和次数。

## A4. 评分

### 方式 1：claude Skill（推荐，语义对齐在当前会话完成）

```text
/analystbench-evaluate 使用 case/case-1.json 直接评分并对比：
case/test-1-agent-1.md
case/test-1-skill-1.md
```

流程：claude 调用 `prepare-alignment` → Python 做关键字匹配草稿 → claude 在当前会话填写语义判定 → `score-with-alignment` 计分。**不起子进程调另一个大模型。**

### 方式 2：CLI 一条命令评分

```bash
.venv/bin/analystbench evaluate \
  ./case/case-1.json \
  ./case/test-1-agent-1.md \
  ./case/test-1-skill-1.md
```

默认调用本机 `claude -p` 做语义判定，Python 执行固定计分。不会导入或写入数据库，只在 `data/results` 输出 Markdown 和审计 JSON。

### 方式 3：分步评分（手动控制每个阶段）

```bash
# 第 1 步：生成评分草稿（不调大模型，Python 做关键字匹配）
.venv/bin/analystbench prepare-alignment \
  ./case/case-1.json \
  ./case/test-1-agent-1.md \
  ./case/test-1-skill-1.md \
  --output ./data/workspaces/alignment-draft.json

# 第 2 步：claude 在草稿中填写语义判定（见方式 1 的流程说明）

# 第 3 步：确定性计分（不调大模型）
.venv/bin/analystbench score-with-alignment \
  ./case/case-1.json \
  ./data/workspaces/alignment-draft.json \
  ./case/test-1-agent-1.md \
  ./case/test-1-skill-1.md
```

适合需要查看或修改对齐草稿、调试计分公式、CI/CD 缓存对齐 JSON 等场景。

## A5. 评分规则

根因/分类/分析链评分规则：

- 根因完全命中 → 直接 100 分并停止后续评分
- 否则：分类正确得 20 分
- 分析链共 60 分按条数均分，每条：日志关键字强匹配占一半 + 结论语义相似度占另一半

关键字由 Python 在完整报告原文中做确定性连续匹配，大模型不参与。未命中时报告会显示最接近的行（仅诊断用途，不参与计分）。

只有关键字和结论都满分时，分析链综合判定才显示"完全命中"。

### 评分输出

所有方式都会：

1. 分别给每份报告评分
2. 在终端显示中文总览、总分、是否通过和评分项判定
3. 自动把第一份报告作为基线，给出分差和提升/退化结论
4. 在 `data/results` 保存 Markdown 和完整审计 JSON

---

# 路径 B：数据库部署与前端支持

## B1. 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

已有 `.venv` 时跳过前两步。

## B2. 初始化数据库

```bash
.venv/bin/analystbench db-upgrade
```

在 `data/` 下创建本地 SQLite 数据库。丢弃旧数据（不可恢复）：

```bash
.venv/bin/analystbench data-reset --yes
```

只删除 `data` 目录下的数据库、内容存储和评分结果，不删除项目根目录的 Case/报告文件。

## B3. 导入并发布 Case

### 方式 1：claude Skill

```text
/analystbench-workflow 导入并发布 HM_PANIC_SYSMGR-case1.json；
测试集是 kernel-log-analysis（Kernel 日志分析测试集），分类是 panic。
```

### 方式 2：CLI

```bash
.venv/bin/analystbench case-import ./HM_PANIC_SYSMGR-case1.json \
  --case-key HM_PANIC_SYSMGR-case1 \
  --test-set kernel-log-analysis \
  --category panic
```

字段有问题时命令会显示具体 Claim、字段含义和可选值。检查通过后一次性整体确认，发布后输出：

```json
{
  "status": "published",
  "case_key": "HM_PANIC_SYSMGR-case1",
  "case_version": 1,
  "test_set": "kernel-log-analysis",
  "category": "panic"
}
```

记住 `case_key` 即可。查看所有已发布 Case：

```bash
.venv/bin/analystbench case-list
```

相同 `case_key` 再次 `case-import` 视为更新：发布新 Revision，保留旧版本。

## B4. 准备 AI 报告

同路径 A3。AI 报告直接保存为 UTF-8 的 `.txt`、`.md` 或 `.log` 文件即可。

## B5. 评分（数据库模式）

### 方式 1：claude Skill

```text
/analystbench-workflow 使用 HM_PANIC_SYSMGR-case1 评分并对比：
HM_PANIC_SYSMGR-test1-agent-1.md
HM_PANIC_SYSMGR-test1-skill-1.txt
```

### 方式 2：CLI

```bash
.venv/bin/analystbench evaluate \
  HM_PANIC_SYSMGR-case1 \
  ./HM_PANIC_SYSMGR-test1-agent-1.md \
  ./HM_PANIC_SYSMGR-test1-skill-1.txt
```

数据库模式使用已发布 Case 的不可变版本，不会再次确认。默认调用本机 `claude -p` 做语义判定，Python 执行固定计分。

## B6. 启动 API 与 Worker

```bash
# 前台运行：自动升级数据库并同时启动 API 与 Worker
.venv/bin/analystbench serve
```

需要释放当前终端时，改用后台模式：

```bash
.venv/bin/analystbench serve --detach
.venv/bin/analystbench service status
.venv/bin/analystbench service logs
.venv/bin/analystbench service stop
```

浏览 `http://127.0.0.1:8000/docs`。前端使用 Case Draft API 审核发布，使用 Evaluation Batch API 创建后台评测。

后台 Worker 使用 `claude` Judge 时，通过 `claude -p` 调 Skill prompt 做语义对齐，然后 Python 计分。
后台模式的 API 和 Worker 输出默认写入 `data/logs/analystbench.log`，PID
记录写入 `data/run/analystbench.pid`。可通过
`ANALYSTBENCH_SERVICE_LOG_PATH` 和 `ANALYSTBENCH_SERVICE_RUNTIME_PATH` 修改路径。
后台启动默认等待就绪 60 秒；较慢机器可增加 `--startup-timeout 120`。
就绪探测直连本机，不受 `HTTP_PROXY`、`HTTPS_PROXY` 影响。
`api`、`worker` 与 `db-upgrade` 命令仍可单独用于调试或拆分部署。

原始报告请求示例：

```json
{
  "case_key": "HM_PANIC_SYSMGR-case1",
  "judge_runner": "claude",
  "raw_reports": [
    {
      "filename": "HM_PANIC_SYSMGR-test1-agent-1.md",
      "content": "完整 AI 报告原文"
    },
    {
      "filename": "HM_PANIC_SYSMGR-test1-skill-1.txt",
      "content": "另一份完整 AI 报告原文"
    }
  ]
}
```

## B7. 评分规则

同路径 A5。

---

<a id="skill-optimization-quickstart"></a>

# 路径 C：Skill 自优化（实验功能）

Skill 自优化会从宿主机已有 Skill 自动建立内部版本，用独立 Git 保存版本，重复运行基线与候选
Benchmark，并且只在 Gate 通过时更新 Active 版本。它不会修改 AnalystBench
主仓库、用户 Skill 源目录或用户源仓库。优化器修改的是 Managed Root 中从
Active 版本派生的不可变候选，不是直接修改正在使用的源 Skill。

## C1. 准备环境

开始前准备好：

- 一个已配置 `skill_base_dir` 的冻结 Harness。该字段直接指向包含各 Skill
  子目录的根目录，宿主机已有 Skill 位于 `<skill_base_dir>/<skill-key>` 并包含
  `SKILL.md`；在设置页点击“新建 Skill”、
  选择该 Harness 后，页面才会即时扫描该目录并让用户选择；
- 已导入的正式 Case 和日志；
- 一个已冻结的 claude Evaluation Target，且命令明确调用目标 Skill，例如
  `claude -p "/my-skill 分析 {input}"`；
- 调用名自动派生为 `/my-skill`，Skill 的 `harness_key` 必须与 Target 的
  Harness Key 完全一致；
- Worker 可以执行冻结配置中的 `claude`，且认证已通过
  Worker 继承的显式环境变量或受控 wrapper 提供。优化命令会使用新的
  临时 `HOME`/XDG 配置目录，普通用户 HOME 中的交互式登录不会自动复制进去；
- Linux/WSL 安装并允许 Worker 运行 `bubblewrap` (`bwrap`)。它用于无网络包内测试；
  只安装二进制不够，当前 WSL/容器还必须允许创建 namespace。

在 `.env.local` 开启功能：

```dotenv
ANALYSTBENCH_SKILL_OPTIMIZATION_ENABLED=true
ANALYSTBENCH_SKILL_OPTIMIZATION_MANAGED_ROOT=./data/skill-optimization
```

Managed Root 必须是已存在、可写的目录。相对路径按进程启动目录解析；
拆分部署时 API、Worker 和 CLI 必须使用同一启动目录，否则应改用绝对路径。
新导入包使用 v2 manifest，把归一化执行 mode 和固定
`ignored_paths` 纳入哈希；setuid/setgid 会被拒绝，运行物化后整包只读
但保留脚本执行位。先创建目录、升级数据库并执行只读预检：

```bash
# Ubuntu/WSL；其他 Linux 发行版使用对应包管理器
sudo apt-get update
sudo apt-get install -y bubblewrap

mkdir -p /absolute/path/to/analystbench-skill-optimization
.venv/bin/analystbench db-upgrade
.venv/bin/analystbench skill-opt preflight --strict
```

`preflight --strict` 会把 `package_test_sandbox=WARN` 也视为非零退出。如
`bwrap` 已安装但 namespace 探测报 `Operation not permitted`，需要在实际
Worker 所在的 WSL/宿主环境放行，不是重装 Python 依赖可以解决的问题。

分别启动后端和前端：

```bash
# 终端 1
.venv/bin/analystbench serve

# 终端 2
cd src/frontend
npm install
npm run serve
```

如果 `npm install` 已执行过，可以直接 `npm run serve`。访问
`http://127.0.0.1:5173/skill-optimization`。如果
`http://127.0.0.1:8000/api/v1/health/ready` 未就绪，先检查服务日志。

## C2. 用三步向导启动实验

点击“新建实验”，依次完成：

1. **Skill**：从下拉框选择明确的 `Harness × Model × Skill` 宿主机组合；页面不创建
   或注册 Skill，选中后后端自动建立内部不可变版本；
2. **Benchmark**：确认所选组合并选择数据模式。开发回归直接勾选 Case；
   独立验证在页面为每个 Case 指定 Train、Validation、Hidden 或
   Prospective；
3. **Gate**：选择 Optimizer，设置提升阈值与 Epoch 数。开发回归第一次
   建议 `max_epochs=1`；`independent_validation` 由前后端强制固定为一个
   Epoch，避免根据同一 Validation 的晋升结果继续调参。

提交后系统自动导入不可变版本、创建 Snapshot，再由同一个冻结
claude Optimizer 依次执行 failure/success/generalization/simplification
四角色 Train-only 分析。提案必须符合严格
`structured_skill_patch.v1`，只允许 `append`/`insert_after`/`replace`/`delete`；
系统按固定角色顺序 round-robin，以 canonical patch hash 去重后默认取
两个候选。随后执行 Screening，并让优胜候选进入三次完整验证。
`development_regression` 中所选已知 Case 同时用于 Evidence、Screening 和
回归 Gate，只能得到 `provisional` 结论。`independent_validation`
中 Train 用于 Evidence/Screening，Validation 只用于完整 Gate；Hidden 和
Prospective 只冻结进 Snapshot，当前闭环不会自动运行。灰区自动追加到
5/7 次，不需要手工重跑。详见
[Skill 自优化运行手册](skill-optimization-runbook.md#独立-trainvalidation-运行)。

## C3. 判断结果与恢复

- 出现 `skill_version_promoted`：候选通过，已成为新的 Active 版本；
- 实验失败或 Gate 未通过：旧 Active 版本保持不变；
- 每个 Epoch 都显示“本轮做了什么，分数如何变化”，包括修改文件、操作、
  Token 增删、Baseline、Candidate、正负 Delta、累计 Delta，以及逐
  Case/Family/Dimension 变化和 Gate 原因；
- 可从页面导出 JSON、Markdown、CSV 总账，也可导出任一不可变版本 ZIP；
- 回滚必须显式选择曾在同一 Target 激活的历史版本，并携带当前
  `lock_version`，不会隐式覆盖并发发生的新 Active；
- V1 一个 Target 最多绑定一个 Active Skill；Independent Snapshot
  可被多个草稿引用，但只有第一个成功启动的 Experiment 能原子消费；
- 运行中断后点击“恢复”，已完成且配置哈希一致的 Run Group 会被复用；
  底层 Submission 还有基于 run config hash 的唯一幂等键，崩溃重试不会重复调用模型；
- Validation 的每个 repeat 用稳定 `pair_seed` 交错 baseline/candidate
  顺序，Seed/位置进入冻结 run config hash；Bootstrap 策略和稳定 Seed
  则进入比较/Gate 结果；
- 连续两个 Epoch 无候选通过 Screening，或连续两个 Epoch 完整验证无提升时，
  系统 Early Stop；
- 4 个 Case、两个候选且一个候选进入三次完整验证时，一个 Epoch 最多生成
  36 份目标 Agent 报告；灰区还会继续增采样。Optimizer 和每份报告的 Judge
  调用另计；Optimizer 默认是四个角色调用，还可能有格式修复/执行重试，
  因此所有角色都使用 claude 时总 CLI/模型调用数会高于 36；
- 4 个开发 Case 只能作为回归检查；通过后 Binding 的
  `active_level=provisional`，不代表独立测试集验证。这不是可随版本流转的
  “版本级别”；回滚会恢复该 Target 历史 Binding 上的级别。
- Full Gate 必须有完整的输出规模估算（`ceil(stdout 字符数/4)`）；缺失或增长
  超阈值都硬拒绝。逐 Case 的 `forbidden_hit_count`/`missing_chain_count`
  重复中位数只要上升也硬拒绝，不能用总分抵消。
- 按 Target 创建的后续普通评测会使用当时的 Active Skill 版本，并把具体
  version/variant 冻结到 Submission；之后的晋升或回滚不会改变历史结果。
- 优化用 Submission/Result 强制标记为不进入普通统计和列表，且只能
  由 Experiment 状态机取消/管理，不能从普通 API 删除破坏总账。

高级排障或脚本化调用可使用 `http://127.0.0.1:8000/docs` 的 Swagger。
命令行预检、总账导出、版本 ZIP 和显式回滚分别使用
`analystbench skill-opt preflight`、`ledger`、`version-export` 和 `rollback`。
真实 claude 并发 E2E 可在私有环境配好隔离 HOME 可用的认证后运行：

```bash
ANALYSTBENCH_REAL_CLAUDE=/absolute/path/to/claude \
  .venv/bin/pytest -q -s \
  tests/test_skill_optimization_e2e.py::test_real_claude_slash_skill_concurrent_e2e
```

包内测试的真实 bubblewrap namespace E2E 默认跳过；只在私有宿主确认
namespace 可用后显式运行：

```bash
ANALYSTBENCH_RUN_BWRAP_E2E=1 \
  .venv/bin/pytest -q -s tests/test_skill_optimization_static_validation.py
```

完整字段、版本与 Gate 设计见
[Skill 自优化系统方案设计](development/AnalystBench-Skill自优化系统方案设计-Codex.md)。
从零配置、独立切分、测试、私有验收和故障定位见
[Skill 自优化运行手册](skill-optimization-runbook.md)。

---

## 语义 Judge 类型（各路径通用）

| Judge | 适用场景 | 说明 |
|-------|---------|------|
| `claude`（默认） | Skill / CLI | 数据库 Worker 用 `claude -p` 调 Skill prompt；Skill 场景下 claude 直接推理 |
| `opencode` | CLI / Worker | 使用 OpenCode subprocess 做语义对齐 |
| `lexical` | 开发调试 | 词法基线，不调大模型，**不能当作正式评分** |

指定 Judge：

```bash
.venv/bin/analystbench evaluate --judge opencode ...
```

claude 或 OpenCode 不可用、超时或返回非法结构时，正式评测直接失败并报告原因，不会静默降级。
