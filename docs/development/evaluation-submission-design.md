# P15 提交测评、报告生成与自动评分设计

状态：Implemented（2026-07-28）

## 目标

在“评测结果”页面增加“提交测评”入口。用户选择一个测试集和一个或多个测评方式后，后台对测试集中的每个 Case：

1. 复制该 Case 的原始日志到本次运行快照。
2. 在隔离工作区分别启动所选测评方式，生成不可变 Markdown 报告。
3. 按 `analystbench-evaluate` Skill 契约，用 Case JSON 对成功生成的报告统一评分和对比。
4. 将运行状态、报告、评分结果和错误产物写入 Case 的时间目录，并在结果页面持续展示。

本阶段保留现有“上传已有报告并评分”功能。新增流程负责从原始日志生成报告，不替代手工报告评分。

## 术语

- **测评方式（Evaluation Method）**：用户配置的报告生成器，例如 Python 脚本或 `claude -p`。它读取原始日志并输出分析报告，不负责评分。
- **测评方式版本（Evaluation Method Version）**：一次冻结的命令模板、限制和输出协议。配置变化产生新版本，历史运行继续引用旧版本。
- **提交测评批次（Evaluation Submission）**：用户一次选择测试集和多个测评方式产生的持久化批次。
- **Case 测评运行（Evaluation Case Run）**：批次中某个 Case 的生成和评分状态。
- **方式运行（Evaluation Method Run）**：一个测评方式对一个 Case 的单次受控子进程执行。
- **评分阶段（Scoring Stage）**：所有方式运行终结后，对成功报告执行的 AnalystBench 评分流程。

界面使用“测评方式”这一用户术语；领域对象和代码应避免把它与 Judge、Scoring Policy 或 Benchmark Run 淵称为同一种对象。

## 正式目录契约

结果根目录继续使用现有可配置项 `results_formal_path`，默认值仍为 `data/results`，不改名。

```text
data/results/
└── <test_set>/
    └── <category>/
        └── <case_key>/
            ├── case.json
            ├── logs/
            │   ├── manifest.json
            │   ├── log.txt
            │   ├── kernel.log
            │   └── snapshot.txt
            └── runs/
                └── 20260728120030/
                    ├── inputs/
                    │   ├── log.txt
                    │   ├── kernel.log
                    │   └── snapshot.txt
                    ├── script.md
                    ├── claude.md
                    ├── run.json
                    ├── result.json
                    ├── result.md
                    └── _artifacts/
                        ├── script/
                        │   └── attempt-1/
                        │       ├── stdout.log
                        │       └── stderr.log
                        └── claude/
                            └── attempt-1/
                                ├── stdout.log
                                └── stderr.log
```

规则：

- `case.json` 与 `logs/` 属于 Case 输入；`runs/` 只保存运行历史。
- 新建和发布 Case 时日志可选；没有日志的 Case 可以存在，但提交包含该 Case 的测试集时必须返回失败。
- `runs/<timestamp>/inputs/` 是本次提交时的日志文件副本，保持相对路径；不为原始日志增加内容哈希或登记状态。
- 时间目录使用本地时间的 `YYYYMMDDHHmmss`。兼容读取已有 12 位 `YYYYMMDDHHmm` 目录，但新提交只写 14 位目录。
- 同一批次内所有 Case 使用同一个时间戳。若目标目录冲突，批次创建失败并重新分配时间戳，不覆盖旧目录。
- 输出报告名由测评方式稳定 `key` 决定，例如 `script.md`、`claude.md`。
- `run.json` 是文件系统运行清单；数据库仍是队列、租约和聚合状态的事实源。
- `result.json` 和 `result.md` 沿用现有直接文件评分结果契约。
- 新结果 ID 为 `<test_set>/<category>/<case_key>/runs/<timestamp>`；列表、统计、读取、移动和删除接口必须同时识别新结构与旧的直属时间目录。
- 写入 `run.json`、报告和结果时使用临时文件加原子替换，避免前端读取半写文件。

## Case 日志契约

Case 日志直接以文件形式存放在 `logs/`。不把正文或内容哈希写入 `case.json`，也不要求日志先进入待登记状态。

`logs/manifest.json` 只记录主日志：

```json
{
  "primary": "log.txt"
}
```

约束：

- 新建 Case 时可以同时上传日志，也可以不上传并在后续补充。
- `logs/` 中除 `manifest.json` 外的普通文件自动作为该 Case 的原始日志，不需要逐个登记或计算内容哈希。
- 只有一个日志文件时自动视为主日志；存在多个日志时必须在 `manifest.json` 或页面中选择一个主日志。
- 主日志路径必须是相对于 `logs/` 的安全路径，不得包含绝对路径、`..` 或逃逸符号链接。
- 上传、替换或删除日志直接更新 `logs/`；改变日志不会创建新的 Case Revision。每次提交通过复制文件到 `runs/<timestamp>/inputs/` 保留本次实际输入。
- 手工放入 `logs/` 的文件在下一次页面刷新和提交预检时自动生效，不出现“待登记”状态。
- 提交时允许通过 `case_paths` 选择本次测评的 Case；页面默认勾选全部日志就绪的 Case，用户可以取消任意可测 Case。
- 没有任何日志、主日志不存在、Case JSON 无效或日志目录包含不安全路径的 Case 自动跳过，不创建 Case Run、Job 或运行目录，也不阻塞其他已选 Case。
- 提交时会再次检查被选 Case；若过滤后没有任何可测 Case，`POST /evaluation-submissions` 失败且不创建批次。
- 批次 Manifest 同时记录请求选择、实际纳入和自动跳过的 Case，保证本次执行范围可追溯。
- P15 的本地 `logs/` 不要求回写 P12 的数据库 CaseTrace；已有 CaseTrace 可以继续存在并物化为普通日志文件，但不是提交测评的前置对象。

## 测评方式配置

一个冻结版本至少包含：

```json
{
  "name": "Python 脚本",
  "key": "script",
  "version": 1,
  "tool_dir": "/opt/analystbench-tools",
  "command_template": "python {tool_dir}/my_script.py {input}",
  "timeout_seconds": 1800,
  "max_output_bytes": 10485760,
  "concurrency_limit": 1,
  "environment_allowlist": [],
  "status": "frozen"
}
```

Claude 示例：

```text
claude -p "分析 {input_dir} 中的全部日志，主日志是 {input}"
```

占位符只允许：

- `{input}`：当前方式隔离工作区中的主日志绝对路径。
- `{input_dir}`：当前方式隔离工作区中的 `logs/` 绝对路径。
- `{workspace}`：当前方式的隔离工作区绝对路径。
- `{tool_dir}`：该测评方式配置并冻结的工具目录绝对路径，用于引用 Python 脚本等本地工具；Claude 等只依赖 PATH 的方式可以不配置。

报告生成阶段禁止提供 `{case}`、`{run_dir}` 和 `{project_root}`，避免生成器接触标准答案、历史结果、其他方式报告或项目内的评测产物。`tool_dir` 不得位于结果根目录或工作区根目录内。

命令执行规则：

- 命令模板解析为 argv，始终 `shell=False`。
- 不支持管道、重定向、命令替换、`&&` 或其他 Shell 组合语法。
- 占位符按单个 argv 参数安全替换，不再次交给 Shell 解释。
- 测评方式只需要把最终报告作为非空 UTF-8 文本写到 stdout；如何生成该文本由用户自己的命令负责。
- 系统捕获 stdout 并保存为 `<key>.md`。不提供 `{output}` 文件协议；退出成功但 stdout 为空仍视为失败。
- stderr、原始 stdout、退出码、终止原因和尝试编号写入 `_artifacts/`，stderr 不混入报告。
- 默认单 Case 超时 30 分钟、总输出上限 10 MiB、每个测评方式并发 1；允许在受控范围内配置。
- 配置保存前执行 `probe`，检查可执行文件、模板、占位符和基本版本信息，不读取或展示凭据。
- 修改已冻结配置会创建新版本。被历史批次引用的版本只能停用，不能删除。
- `key` 只允许小写字母、数字、`-` 和 `_`，必须全局唯一，并不得使用 `result`、`run`、`inputs`、`artifacts` 等保留名。

## 隔离工作区

仅把日志移入 `logs/` 不能防止 Claude 通过父目录发现历史答案，因此报告生成必须使用独立工作区：

```text
data/workspaces/evaluation/
└── <submission_id>/
    └── <case_run_id>/
        ├── script/
        │   └── logs/
        │       ├── log.txt
        │       └── kernel.log
        └── claude/
            └── logs/
                ├── log.txt
                └── kernel.log
```

每个方式运行：

1. 从 `runs/<timestamp>/inputs/` 复制日志到自己的 `logs/`，并默认以只读权限提供；不得用可被子进程修改后影响输入快照的硬链接。
2. 以自己的工作区为 cwd 启动受控子进程。
3. 只向命令模板提供已定义的安全占位符；除只读工具目录 `{tool_dir}` 外，运行时路径都位于该方式自己的隔离工作区。
4. 不复制 `case.json`、`runs/`、其他方式输出、参考答案、评分草稿或结果。
5. 成功后将最终 stdout 冻结到正式运行目录的 `<key>.md`。
6. 收集产物后清理临时工作区；正式目录保留输入副本和审计产物。

不同方式必须使用不同工作区，即使并行执行也不能看到彼此的中间或最终输出。

MVP 的本地权限限制面向受信任命令，不构成恶意程序的强安全沙箱。Claude/OpenCode Profile 仍应使用限制性工具权限和可复现模式；容器或 VM 隔离属于后续增强。

## 提交流程

“评测结果”页面右上角新增“提交测评”按钮，使用三步对话框：

1. **选择测试集**：选择一个测试集并包含其全部 Case，展示 Case 数、日志数量、主日志和阻断问题。
2. **选择测评方式**：多选冻结且已通过 probe 的方式版本，展示名称、版本、命令预览、超时和最近探测结果。
3. **提交确认**：展示 `Case 数 × 测评方式数`、预计任务数、固定评分流程和输出根目录。

提交预检必须验证：

- 测试集存在且能读取其中全部 Case。
- 每个 Case 有合法 `case.json`、至少一个非空日志且恰好一个有效主日志。
- 所选测评方式处于 frozen/active 状态且命令模板合法。
- 输入文件名不与 `<method_key>.md`、`run.json`、`result.json`、`result.md` 和保留目录冲突。
- 目标目录均不存在。

任一 Case 无日志时返回稳定错误 `case_logs_missing` 和具体 Case 路径，整个请求失败。预检全部成功后，一次性写入不可变 Submission Manifest、Case Run 和 Method Run，创建目录与初始 `run.json`/`result.json`，然后返回 HTTP 202。请求进程不执行子进程或评分。

## 状态机与后台任务

提交批次：

```text
queued -> preparing -> generating -> scoring -> completed
   |          |            |           |
   +----------+------------+-----------+-> completed_with_errors
   +----------+------------+-----------+-> failed
queued/running -> cancelling -> cancelled
```

Case 运行：

```text
pending -> copying -> generating -> scoring -> completed
    |          |           |           |
    +----------+-----------+-----------+-> completed_with_errors
    +----------+-----------+-----------+-> failed
```

方式运行：

```text
queued -> preparing -> running -> collecting -> succeeded
   |          |          |          |
   +----------+----------+----------+-> failed
   +----------+----------+----------+-> timeout
queued/running -> cancelling -> cancelled
```

执行原则：

- 使用现有数据库 Job、租约和 Local Worker，不使用 API 进程内后台线程。
- 命令探测与实际执行必须共用同一套可执行文件解析；`claude` 不在 PATH 时，允许通过
  `ANALYSTBENCH_CLAUDE_EXECUTABLE` 指定，或自动使用 WSL 中最新的 VS Code Claude Code
  扩展原生二进制。
- 每个 Case × 测评方式对应一个独立 Job；并发同时受 Worker 全局限制和方式版本 `concurrency_limit` 限制。
- 全部方式运行终结后，为该 Case 入队一个评分 Job。
- 至少一个方式成功时，对成功报告评分，Case 进入 `completed` 或 `completed_with_errors`。
- 所有方式均失败时不启动评分，Case 进入 `failed`。
- 批次允许部分 Case 失败；至少一个 Case 成功时整体为 `completed_with_errors`。
- 重试创建新的 attempt，保留旧 stdout/stderr；成功报告不被其他方式的重试覆盖。
- 若重试改变命令、日志、Case 或评分契约，必须创建新提交批次。

## `run.json`

`run.json` 至少记录：

- Submission ID、Case Run ID、批次时间戳和创建时间。
- 测试集、分类、Case Key 和 Case JSON 哈希。
- 每个日志的相对文件名、主日志标志和大小。
- 所选测评方式版本 ID、配置哈希和安全命令摘要。
- 每个方式运行的状态、attempt、开始/结束时间、退出码、终止原因和报告哈希。
- 明确的评分报告相对路径列表。
- 评分状态、Judge/Skill 契约版本、结果文件和错误。

命令摘要不得包含凭据或未在白名单中的环境变量值。

## 自动评分

评分器固定按项目的 `analystbench-evaluate` Skill 契约程序化编排，而不是启动一个嵌套 Slash Skill：

1. Python 对 `case.json` 和 `run.json` 中明确列出的成功报告执行 `prepare-alignment`。
2. Claude 只填写根因、分类和分析链结论的语义关系，不读取或修改 Python 日志关键字审计。
3. Python 执行 `score-with-alignment`，校验 Case/报告哈希并确定性计分。
4. 写入 `result.json` 和 `result.md`，清理临时 alignment draft。

评分逻辑使用以下显式输入，不允许用 `*.md` 扫描目录：

```text
case.json
runs/20260728120030/script.md
runs/20260728120030/claude.md
```

这样可避免把原始 Markdown 日志、历史 `result.md` 或非本批次文件误当作候选报告。报告生成与评分继续分离：报告文件成功写入并记录哈希后冻结，评分失败不允许重新生成或修改报告。

## API 草案

### 测评方式

- `POST /api/v1/evaluation-methods`：创建第一个草稿版本。
- `GET /api/v1/evaluation-methods`：列出当前版本和启用状态。
- `GET /api/v1/evaluation-methods/{id}`：查看版本详情。
- `POST /api/v1/evaluation-methods/{id}:probe`：探测命令。
- `POST /api/v1/evaluation-methods/{id}:freeze`：冻结版本。
- `POST /api/v1/evaluation-methods/{id}:revise`：从冻结版本创建新草稿。
- `POST /api/v1/evaluation-methods/{id}:archive`：停用，不删除历史版本。

### Case 日志

- `GET /api/v1/local-cases/{case_path}/logs`：直接枚举 `logs/` 并返回主日志。
- `POST /api/v1/local-cases/{case_path}/logs`：上传或替换日志文件，不创建新 Case Revision。
- `DELETE /api/v1/local-cases/{case_path}/logs/{log_path}`：删除指定日志。
- `PUT /api/v1/local-cases/{case_path}/logs/primary`：设置主日志。
- `GET /api/v1/local-cases/tree`：增加 `log_count`、`primary_log` 和 `submission_ready`。

### 提交批次

- `POST /api/v1/evaluation-submissions`：预检、冻结 Manifest 并排队。
- `GET /api/v1/evaluation-submissions/{id}`：读取批次进度和汇总。
- `GET /api/v1/evaluation-submissions/{id}/case-runs`：读取 Case 状态。
- `GET /api/v1/evaluation-case-runs/{id}`：读取方式运行和评分详情。
- `POST /api/v1/evaluation-submissions/{id}:cancel`：协作式取消。
- `POST /api/v1/evaluation-case-runs/{id}:retry-failed`：只重试原配置下的失败 attempt。

### 结果兼容

- `/api/v1/direct-results` 识别 `test_set/category/case/runs/timestamp/result.json`，时间字段取真实时间目录而不是 `runs`。
- 结果统计仍以路径前三层解析测试集、分类和 Case，并忽略 `logs/`、`inputs/` 与 `_artifacts/`。
- 读取、移动和删除只作用于目标时间目录，不得移动或删除 Case 根目录的 `case.json`、`logs/` 或其他运行。

## 前端行为

### 评测结果页

- 标题区新增主按钮“提交测评”。
- 提交成功后立即显示批次节点和生成/评分进度。
- Case 详情展示每个测评方式的状态、耗时、报告文件、错误和重试入口。
- 批次进度分别展示 Case 完成数、报告生成数和评分完成数。
- 正式结果树从 `runs/<timestamp>/result.json` 读取结果；兼容旧的 Case 根目录直属时间目录。

### 设置页

- 保留结果根目录配置。
- 新增“测评方式”管理：创建、查看版本、编辑为新版本、probe、冻结和停用。
- 测评方式可配置 `tool_dir`；命令预览显示占位符替换后的安全示例，但不执行用户输入。

### 测试集页

- 新建 Case 时日志上传可选。
- Case 详情增加“原始日志”列表、主日志标记和“补充/替换原始日志”。
- 缺日志或没有有效主日志时明确标记为不可提交。

## 兼容与迁移

- 继续读取现有 `case.json` 和 Case 根目录直属时间戳结果。
- 新提交只写 `logs/` 与 `runs/` 新结构。
- 已有 Case 可以直接新建 `logs/` 并放入日志；系统自动枚举其中的普通文件，不扫描 Case 根目录或历史结果目录。
- 多日志 Case 缺少主日志配置时只要求用户选择主日志，不要求登记文件或计算哈希。
- 现有临时结果、归档、移动、删除和上传报告评分功能继续保留。

## 验收条件

- 测试集的每个可提交 Case 都同时具有 `case.json`、`logs/` 和唯一主日志。
- 选择两个方式和两个 Case 时生成四个独立方式运行，输出文件名稳定且互不覆盖。
- Fake Python 与 Fake Claude 只能看到各自隔离工作区中的日志，不能看到 `case.json`、`runs/`、历史报告或其他方式输出。
- 子进程命令始终 `shell=False`；危险模板、路径逃逸、符号链接逃逸和保留文件名在提交前被拒绝。
- API 或 Worker 重启后，过期租约能够恢复，已完成报告不重写。
- 一个方式失败时，其他成功报告仍被评分，Case 和批次显示 `completed_with_errors`。
- 评分只接收 Manifest 中的报告列表，并保持 Python 日志证据评分与 Claude 结论语义判断的既有边界。
- `result.json`、`result.md`、报告、运行输入副本、配置版本和失败审计足以解释一次提交。

## 已确认决策

- 结果根目录继续使用现有可配置路径，不改名。
- 新时间目录使用 14 位秒级格式。
- 一个 Case 有且仅有一个主日志；`{input}` 指向主日志，`{input_dir}` 指向全部日志。
- 新建 Case 日志可选，但提交测试集时任一 Case 无日志都会使整个提交失败。
- 日志直接放入 `logs/` 并自动生效，不设置待登记状态、不计算日志内容哈希，也不因日志变化创建 Case Revision。
- 生成器只需输出 stdout 文本，系统将其保存为 `<method_key>.md`，不提供 `{output}` 协议。
- 本地脚本通过冻结的 `{tool_dir}` 定位，子进程 cwd 仍为隔离工作区。
- 单个方式失败时仍评分其他成功报告。
- 后台按 `analystbench-evaluate` Skill 契约程序化编排。
- 原始日志固定进入 `logs/`，结果固定进入 `runs/`。
- 每个方式在只含日志的独立工作区运行，报告生成阶段不暴露 Case JSON 或任何历史结果。
