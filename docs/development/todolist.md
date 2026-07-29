# 开发 Todo 与阶段门禁

状态：Accepted（P0.1 Agent Execution 范围修订，2026-07-21）

原则：每个阶段先确认文档和验收条件，再进入实现；发现会改变既有契约的问题时暂停编码，先更新文档并请项目负责人确认。

## P0 设计定稿

- [x] 明确无前端 MVP 范围和非目标。
- [x] 设计组件边界、依赖方向和本地部署模型。
- [x] 设计领域对象、版本、快照和 Run Manifest。
- [x] 设计 Eval Spec v1 结构与冻结校验。
- [x] 给出评分公式、惩罚、门禁和指标草案。
- [x] 设计 LLM 结构化契约和失败语义。
- [x] 设计 REST API、CLI 映射和 Run 生命周期。
- [x] 设计 Suite 扩展与测试策略。
- [x] 确认 p0-decisions.md。
- [x] 根据确认结果更新文档状态为 Accepted。

验收门禁：P0 决策无未解决项，核心文档相互一致。

## P0.1 Agent Execution 范围修订

- [x] 将 Claude Code/OpenCode CLI 直接执行纳入 MVP。
- [x] 设计 Candidate Generation Run 与 Benchmark Run 分离。
- [x] 设计 Runner、临时工作区、权限、产物与失败模型。
- [x] 更新领域模型、API、测试策略和阶段顺序。
- [x] 确认 p0.1-decisions.md。
- [x] 根据确认结果将受影响文档改为 Accepted。

验收门禁：执行环境、权限、隔离、重试、CLI 管理和默认限制均已确认。

## P1 工程骨架

- [x] 建立 Python 包、API、CLI、Worker、迁移和测试目录。
- [x] 配置管理、结构化日志、错误基类和健康检查。
- [x] SQLite/Alembic 基线与 CI 测试命令。
- [x] 本地 Python 开发命令。

验收门禁：空库迁移、API/Worker 启动、健康检查和基础测试通过。

## P2 数据与版本中心

- [x] Dataset、Case Revision、Dataset Version。
- [x] Candidate、Candidate Version、Candidate Report。
- [x] Execution Profile、Candidate Generation Run、Agent Case Run 与产物引用。
- [x] Content Store、canonical JSON 和 SHA-256。
- [x] Eval Spec/Policy/Prompt/Model Profile 版本元数据。
- [x] JSON 导入导出与不可变性/归档规则。

验收门禁：可构造完整冻结 Run Manifest，历史版本不会被后续修改污染。

## P3 Agent Execution Lite

- [ ] AgentRunner 协议、结构化 argv 和 CLI probe。
- [ ] 临时 Case 工作区、材料清单和权限策略。
- [ ] Claude Code Runner：`claude -p` + JSON 输出。
- [ ] OpenCode Runner：`opencode run --format json`。
- [ ] 持久化后台 Job、Worker 租约、取消、超时和失败分类。
- [ ] Candidate Generation Run、Agent Case Run、原始事件和 Candidate Report 冻结。

验收门禁：两个 Fake CLI 完成后台端到端测试；真实 CLI 未安装或未认证时返回明确错误且不泄露凭据。

## P4 Eval Spec 闭环

- [ ] Pydantic/JSON Schema 和语义校验器。
- [ ] 原文区间、图引用、环、权重和待确认项校验。
- [ ] Generator Adapter、Prompt v1、草稿生成任务。
- [ ] 草稿编辑、验证、冻结和新版本派生 API/CLI。

验收门禁：固定标准答案可生成、人工修订并冻结合法 Eval Spec；伪造引用不可冻结。

## P5 可解释评分核心

- [ ] Candidate Analyzer 和抽取缓存。
- [ ] 轻量候选检索、Claim Judge、Edge Judge、Forbidden Judge。
- [ ] 纯函数评分器、惩罚、门禁和指标。
- [ ] 完整中间结果与引用追溯模型。

验收门禁：全部 golden fixtures 通过，相同结构化输入产生逐字段一致结果。

## P6 Benchmark 执行

- [ ] 持久化 Job、Worker 租约、重试、取消和恢复。
- [ ] Case Run 状态机、缓存键、幂等提交。
- [ ] Run 汇总、部分失败和结果导出。

验收门禁：进程中断后可恢复；失败 Case 可单独重试且不覆盖成功结果。

## P7 Candidate A/B 对比

- [ ] Manifest 可比较性检查。
- [ ] Case 交集、汇总变化、提升/退化和冲突变化。
- [ ] 非受控比较警告和差异清单。

验收门禁：固定两个 Candidate 的对比结果与预期完全一致。

## P8 CLI 与 Suite

- [ ] 完整 CLI 闭环和机器可读输出，包括 Agent 后台运行与状态查询。
- [ ] Suite Registry、generic-analysis、KDiag v0。
- [ ] 示例数据集和离线演示命令。

验收门禁：无需调用 REST 客户端即可从空库完成一次 A/B Benchmark。

## P9 交付

- [ ] 安全与隐私检查、日志脱敏、错误恢复演练。
- [ ] 本地部署冒烟、备份恢复说明、运维文档。
- [ ] API/CLI 使用说明和 MVP 发布检查表。

验收门禁：新环境按文档可部署并复现示例 Benchmark。

## Implementation status

- P3 Agent Execution Lite: completed and verified with a fake Claude-compatible CLI.
- P4 Eval Spec loop: completed and verified for generation, review-gated freezing, and forged-quote rejection.
- P5 Explainable scoring: completed and verified for atomic report claims, one-to-one alignment, process scores, and root-cause contradiction penalties.
- P6 Benchmark execution: completed and verified for durable queued execution, result persistence, run summaries, cancellation, retry, and export APIs.
- P7 Candidate A/B comparison: completed and verified for direct-manifest checks, uncontrolled warnings, coverage differences, and score deltas.
- P8 CLI and suites: completed and verified for an offline CLI workflow, generic-analysis, KDiag v0, and portable example datasets.
- P9 delivery: completed with code, documentation, migration consistency, CLI, security, recovery, and local deployment checks.

## 后续阶段

Web 前端、Agent Trace/OTLP 评分、更多 Agent Adapter、远程执行集群和 EvalOps 不进入上述 P0～P9；启动前需单独创建并确认设计阶段。

## P10 单入口交互式评分会话

- [x] Evaluation Session、问题、答案和状态持久化。
- [x] Case/Report 草稿预检与正式对象自动转换。
- [x] 一份 Case 对多份报告的自动导入、冻结和 Benchmark 编排。
- [x] 前端可复用的 Evaluation Session API。
- [x] 单命令 `analystbench score` 调试入口。
- [x] Claude `/analystbench-score` 交互技能。
- [x] 端到端测试和中文快速上手。

验收门禁：用户仅提供草稿文件；无问题时自动评分，有问题时只确认明确字段；不手工编辑 JSON 或处理内部 ID。

实现状态：已完成。真实 Claude Skill 前向测试使用一份 Case 和一份报告，经“全部接受建议”后自动完成评测并返回结果。

## P11 Case 基准库与多报告批量评测

- [x] 拆分 Case JSON 生成、Case 人工审核和 Case 发布。
- [x] Case Draft 持久化、一次整体确认和不可变发布。
- [x] 已发布 Case 列表与 `case_key` 用户入口。
- [x] Report Draft 独立预检与非阻断警告。
- [x] 一份已发布 Case 对多份报告的后台批量评分与自动对比。
- [x] `case-import`、`case-list`、`evaluate` CLI。
- [x] 前端可复用的 Case Draft、Report Draft、Evaluation Batch API。
- [x] 中文 Claude/OpenCode Skill 与 Quickstart 更新。
- [x] 端到端测试和迁移验证。

验收门禁：标准答案只审核一次；报告评分不再重复确认 Case；用户只使用 `case_key` 和文件路径，不处理内部 ID。

实现状态：已完成。新增 API 端到端测试覆盖一次整体确认、发布后复用、两份报告后台评分、提示引用警告和自动对比；真实 Claude Code 只读前向检查确认两个项目级 Skill 可用。

## P12 Case 测试集与分类存储

- [x] Dataset 明确作为测试集，并新增稳定 `dataset_key`。
- [x] 新增测试集内的 CaseCategory 正式分类，不再使用 tags 代替。
- [x] Case 保存源文件名，`case_key` 由用户在导入时命名（不再从文件名推断）。
- [x] 新增 CaseTrace，持久化日志、snapshot、堆栈等原始材料引用。
- [x] 同一测试集发布新 Case 时生成包含全部最新 Case Revision 的版本。
- [x] `case-import` 与 Case Draft API 接收测试集、分类和源文件名。
- [x] `case-organize` 与 API 支持将已审核旧 Case 重新归档，无需复审评分项。
- [x] 本地数据库迁移、Claude/Codex Skill、中文输入说明和端到端测试。

验收门禁：Case 文件名、测试集和分类均可追溯；同一测试集的多个 Case 进入同一版本快照；旧 Case 迁移不手改 JSON 或数据库。

实现状态：已完成。本地 `HM_PANIC_SYSMGR-case1` 已归入“kernel日志分析测试集 / panic”。

## P13 根因/分类/分析链评分

- [x] 将“根因 + 日志N/结论N”自动标准化为一个根因和 N 个证据链评分项。
- [x] 删除“直接原因”评分项；根因完全命中直接100分且停止后续评分。
- [x] 根因未完全命中时，证据链等分80分，部分命中得一半。
- [x] 按候选结论命中率计算最多20分的幻觉扣分。
- [x] 可读报告展示计分路径、证据链基础分、命中率和幻觉扣分。
- [x] 同步 Case/Evaluate Skill、输入说明和回归测试。

验收门禁：一根因、一个问题分类、三条分析链的输入必须冻结为5个评分项；根因完全命中得100；根因不完全命中时按分类20分与分析链60分计算。

实现状态：已完成代码与测试，现有 Case 通过同名导入发布新 Revision 后生效。

## P14 语义 Judge 与评分项新命名

- [x] 正式评分默认调用 Claude Code，支持切换 OpenCode。
- [x] 大模型只判定 `match`、`partial_match`、`missing`、`contradiction`，Python 校验结构并执行固定计分。
- [x] 禁止正式评分静默退回字符匹配；lexical 仅保留为显式开发调试模式。
- [x] 根因使用 `root`，证据链使用 `chain-N`，通用评分项使用 `claim-N`，候选结论使用 `candidate-N`。
- [x] 语义 Judge 直接读取完整 AI 报告；Candidate Claim 仅用于引用与审计。
- [x] CLI、Evaluation Batch API、后台 Worker、中文报告和 Skill 使用同一 Judge 配置。
- [x] 新增语义 Judge 输出校验、重复 Candidate 对齐和固定计分回归测试。

验收门禁：正式报告必须标注 Claude 或 OpenCode Judge；Judge 失败时整次评分失败且保留错误；数据库和结果只保留新 ID 格式。

实现状态：代码与自动化测试完成，旧数据库和结果在切换时一次性清理，不做旧结果兼容。

## P15 提交测评、报告生成与自动评分

状态：MVP 已实现（2026-07-28）。详细契约见 `evaluation-submission-design.md`。

### P15.0 文档与契约

- [x] 确认 Case 输入使用 `case.json + logs/`，运行结果使用 `runs/<YYYYMMDDHHmmss>/`。
- [x] 确认每种测评方式使用只含日志的独立工作区，不暴露标准答案或历史结果。
- [x] 确认测评方式、批次、Case Run、Method Run、状态机和失败语义。
- [x] 确认按 `analystbench-evaluate` Skill 契约程序化编排评分。

验收门禁：目录、输入占位符、隔离、部分失败和评分边界无未决项。

### P15.1 Case 日志与目录投影

- [x] 新建 Case 时日志上传可选，后续可在测试集页面补充、替换或删除。
- [x] `logs/` 中普通文件自动作为原始日志，不增加待登记状态或内容哈希。
- [x] 单日志自动作为主日志，多日志必须通过 `logs/manifest.json` 或页面选择主日志。
- [x] 日志变化不创建新 Case Revision；提交时复制到 `runs/<timestamp>/inputs/` 固定本次输入。
- [x] 本地 Case Tree 返回日志数量、主日志和可提交状态。

验收门禁：Case 可在没有日志时创建；提交测试集时任一 Case 无日志或主日志无效，整个提交返回失败且不创建 Job。

### P15.2 版本化测评方式与通用 Runner

- [x] Evaluation Method 草稿、版本、probe、冻结、Frozen 修订和级联删除。
- [x] 安全命令模板与 `{input}`、`{input_dir}`、`{workspace}`、`{tool_dir}` 占位符。
- [x] 通用 Command Runner 使用 argv、`shell=False`、超时、输出上限和进程树取消。
- [x] 用户命令负责输出文本，系统把 stdout 冻结为 `<method_key>.md`，stderr/原始输出按 attempt 审计。
- [x] Local Worker 串行执行满足默认并发 1，API 拒绝未声明的凭据字段。

验收门禁：Fake Python/Fake Claude 可生成报告；`{tool_dir}` 能定位隔离工作区外的本地脚本；Shell 注入、路径逃逸、空输出和超限输出均返回稳定错误。

### P15.3 持久化提交批次与隔离执行

- [x] Evaluation Submission、Case Run、Method Run 和不可变 Manifest。
- [x] 提交预检、14 位批次时间戳、日志快照和原子 `run.json`。
- [x] 每种方式创建独立、只含只读日志的临时工作区，结束后清理。
- [x] 数据库 Job、Worker 租约、取消、部分失败和原配置重试。
- [x] 报告冻结后再进入评分，生成重试不覆盖成功报告。

验收门禁：Worker 中断后可恢复；不同方式互不可见；历史 Case/结果不进入生成工作区。

### P15.4 自动评分

- [x] 只按 Manifest 中成功报告调用现有 `evaluate_direct` 程序化评分链路。
- [x] Python 日志证据评分与 Claude 结论语义判断保持既有边界。
- [x] 至少一个报告成功时继续评分并生成 `result.json`、`result.md`。
- [x] 所有方式失败时不启动评分；评分失败不修改已冻结报告。
- [x] 评分临时产物沿用现有直接评分清理规则，运行错误写入审计产物。

验收门禁：原始 Markdown 日志、历史 `result.md` 和非 Manifest 文件不会被误评分；相同冻结输入的 Python 计分一致。

### P15.5 前端闭环

- [x] 评测结果页新增“提交测评”和测试集/方式/确认三步对话框。
- [x] 实时展示批次、Case、方式运行和评分进度。
- [x] 支持查看评分报告、stdout/stderr 审计、部分失败和失败重试。
- [x] 设置页新增测评方式版本管理。
- [x] 测试集页新增日志列表、主日志和可提交状态。
- [x] 兼容旧时间目录、现有临时结果和上传报告评分流程。

验收门禁：用户无需 CLI 或内部 ID，即可从测试集原始日志提交两种方式、等待评分并查看对比结果。

### P15.6 测试与交付

- [x] 日志缺失、单/多主日志、目录隔离和方式修订测试。
- [x] Fake Runner、Worker、排队取消、审计产物和自动评分端到端测试。
- [x] 两个 Case × 两种方式 × 自动评分的后端闭环测试。
- [x] 数据库迁移、API 路由、前端生产构建和全量回归检查。

验收门禁：新环境可按文档配置两个测评方式并完成可恢复、可审计、无答案泄漏的批量测评。

## P16 AnalystBench 内置定时测评

状态：MVP 已实现（2026-07-28）。详细契约见 `scheduled-evaluation-design.md`。

### P16.0 文档与契约

- [x] 确认 MVP 只支持每天固定本地时间，不开放 Cron 表达式。
- [x] 确认动态全部可测 Case 与固定选择 Case 两种模式。
- [x] 确认计划固定 Method 版本，不自动跟随同 Key 新版本。
- [x] 确认重叠跳过、最近一次补跑和 Run Now 不改正常计划。
- [x] 确认计划、计划执行、Submission 来源和配置快照的数据契约。

验收门禁：时间、补跑、重叠、版本、Case 选择和历史删除语义无未决项。

### P16.1 数据模型与时间计算

- [x] 新增 Evaluation Schedule、Schedule Run 和数据库迁移。
- [x] 实现 IANA 时区、每日 `HH:mm`、UTC `next_run_at` 和下一次执行计算。
- [x] 为计划执行生成唯一触发键并冻结配置快照。
- [x] Evaluation Submission 增加可空 Schedule Run 来源。

验收门禁：跨重启的下一次执行稳定，重复触发键不能产生第二条执行记录。

### P16.2 调度与 Worker

- [x] Worker 空闲轮询增加轻量到期扫描。
- [x] 原子创建 Schedule Run 与 `evaluation_schedule_trigger` Job。
- [x] 多 Worker 抢占、Job 租约恢复和最近一次补跑。
- [x] 同计划存在运行中 Submission 时记录 `skipped_overlap`。
- [x] 触发失败不终止 Worker，也不直接写正式结果目录。

验收门禁：同一计划时间最多创建一个批次；Worker 中断恢复不重复提交。

### P16.3 预检与 P15 集成

- [x] `all_ready` 每次动态枚举日志就绪 Case。
- [x] `selected` 每次过滤固定 Case，缺日志项自动跳过。
- [x] 校验固定 Method ID 仍为冻结且未停用状态。
- [x] 复用 P15 `create_submission`、Manifest、隔离、并发和评分流程。
- [x] Schedule Run 与 Submission 状态、错误和终态同步。

验收门禁：定时批次与手工批次生成相同目录和评分结果，不出现第二套执行逻辑。

### P16.4 API

- [x] 计划列表、创建、读取、编辑和删除 API。
- [x] 启用、停用和 Run Now API。
- [x] 计划执行历史和单次详情 API。
- [x] 返回下次执行、最近状态、关联批次及稳定错误码。
- [x] 未执行计划可物理删除；有历史计划只能停用并隐藏。

验收门禁：所有写操作具备明确校验和幂等语义，422/409 错误可直接供前端展示。

### P16.5 前端闭环

- [x] 评测结果页新增“定时测评”入口和计划列表。
- [x] 新建/编辑表单复用测试集、Case、Method 和 Judge 选择。
- [x] 展示启用状态、时区、每日时间、下次执行和最近结果。
- [x] 支持启停、Run Now、执行历史、删除或停用。
- [x] 定时生成的普通批次显示来源计划并可进入正式结果。

验收门禁：用户无需 CLI 即可创建、观察、立即触发和停用每日计划。

### P16.6 测试与交付

- [x] 下一次执行、时区、补跑、重叠和多 Worker 并发测试。
- [x] 动态/固定 Case、Method 失效和 Run Now 测试。
- [x] Schedule Run 到 Submission 再到正式结果的端到端测试。
- [x] API 错误、重启恢复和删除/停用语义测试。
- [x] 前端生产构建、Sites Worker 和深浅主题检查。

验收门禁：定时测评在进程重启、机器停机和重复 Worker 下仍可恢复、可审计且不重复执行。

## P17 本地服务统一启动

状态：已实现（2026-07-28）。

- [x] 新增 `analystbench serve`，按数据库升级、Worker、API 的顺序启动。
- [x] 新增 `serve --detach`，后台运行并将日志与 PID 写入本地 `data` 目录。
- [x] 新增 `service status`、`service logs` 和 `service stop` 生命周期命令。
- [x] 保留 API 与 Worker 的独立进程职责和原有拆分调试命令。
- [x] Case Draft 生成统一由数据库 Job 和 Local Worker 执行。
- [x] 增加启动顺序、后台记录和过期 PID 清理测试。
- [x] 端口预检允许正常复用刚关闭的监听地址，避免服务重启被残留 socket 状态误判。

验收门禁：一条命令完成迁移和启动；关闭终端不影响后台服务；重复启动被拒绝；
停止时 API 与 Worker 一起退出；API 进程不直接执行持久化后台任务。

## P18 测评方式执行耗时

状态：已实现（2026-07-29）。详细契约见
`evaluation-method-timing-design.md`。

### P18.0 文档与契约

- [x] 确认以 Method Run 为计时对象，手工提交和定时测评复用同一结构。
- [x] 确认耗时只覆盖实际命令执行，不包含排队、工作区准备和评分。
- [x] 确认失败、超时、取消、重试和旧结果的计时语义。
- [x] 确认前端批次详情、综合得分区与正式结果总览展示位置。
- [x] 确认终态批次可删除，运行中批次必须先取消。

验收门禁：计时起止点、结果结构、重试和前端展示无未决项。

### P18.1 持久化与执行计时

- [x] Method Run 增加 `started_at`、`finished_at`、`duration_ms` 和数据库迁移。
- [x] 使用 UTC 时间记录审计点，使用单调时钟计算毫秒耗时。
- [x] 成功、非零退出、超时和运行中取消统一写入终态计时。
- [x] 重试重置当前计时，启动前取消或启动失败保持空值。

验收门禁：排队时间不进入实际执行耗时，并发方式互不覆盖且耗时非负。

### P18.2 结果与 API

- [x] Case Run API 和 `run.json` 的每个 Method 返回计时字段。
- [x] `result.json` 增加独立 `generation.methods[]`，按方式 Key 对应评分报告。
- [x] `result.md` 排名表增加生成耗时且不改变评分和排名。
- [x] 旧结果缺少计时字段时兼容读取并显示空值。

验收门禁：同一 Case 的 `claude`、`script`、`skill` 均能在本次正式结果中找到各自
耗时。

### P18.3 前端闭环

- [x] 批次详情为运行中的方式显示“已运行”，终态显示固定耗时。
- [x] 正式结果“综合得分”和“总览”增加“生成耗时”。
- [x] 审计弹窗展示开始时间、结束时间和耗时。
- [x] 秒、分秒和空值使用统一格式并复用深浅主题变量。

验收门禁：用户能在运行期间观察耗时，并在结果详情中直接对比质量与速度。

### P18.4 测评批次删除

- [x] 新增终态 Submission 安全删除 API 和稳定 404/409 错误。
- [x] 删除关联 Case Run、Method Run、Job、工作区和正式结果目录。
- [x] 目录清理与数据库事务失败时可恢复，不删除 Case 输入和测评方式。
- [x] 前端批次详情增加确认删除入口并刷新批次、结果和 Dashboard 数据。

验收门禁：终态批次可以完整删除；运行中批次不会被部分删除；定时计划和触发历史
继续保留。

### P18.5 测试与交付

- [x] 覆盖成功、超时、排队、并发及既有取消/重试状态计时路径。
- [x] 覆盖手工提交与定时触发产生相同结果结构。
- [x] 覆盖旧结果兼容、API、`run.json`、`result.json` 和 Markdown。
- [x] 运行数据库迁移、后端回归、前端测试、生产构建和 Sites Worker 测试。

验收门禁：计时可持久化、可审计、可展示，并且不改变现有生成和评分行为。

实现状态：后端、迁移、前端和自动化测试已完成；本地服务已升级到 0012 并重载。
内置浏览器受 WSL 路径桥接限制，截图级深浅主题视觉验收待后续补充。

## P19 Harness 与 Model 解耦评测

状态：核心实现与回归已完成（2026-07-29）；历史名称映射、旧计划迁移和完整视觉验收仍待后续批次。详细契约见
`harness-model-evaluation-design.md`。

### P19.0 文档与兼容契约

- [x] 确认 AnalystBench 只选择模型名称，本地 Harness 管理 endpoint、凭据和参数。
- [x] 确认 Harness、Model、Evaluation Target 和 Target Run 的领域边界。
- [x] 确认 `required` 与 `none` 两种模型策略及 `script-only` 无模型基线。
- [x] 确认提交默认全选可用 Target，定时计划固定精确 Target。
- [x] 确认固定 Harness 比 Model、固定 Model 比 Harness 的质量和耗时口径。
- [ ] 将新对象、版本和可比较性规则同步到 `domain-model.md`、`architecture.md` 和
  `api-design.md`。

验收门禁：模型选择、命令参数、基线、版本、比较、兼容和定时行为无未决产品边界。

### P19.1 数据模型与迁移

- [x] 新增 Harness Version、Model Version 和 Evaluation Target 持久化模型。
- [x] Target 唯一引用一个 Harness Version 和一个可空 Model Version。
- [x] 扩展 Method Run 作为内部执行桥接并投影 Target Run，保留 P18 计时字段。
- [x] Submission、Schedule 和运行清单支持冻结 `target_ids` 与完整 Target 快照。
- [x] 增加 Alembic 迁移、外键、唯一约束、状态和 content hash。
- [x] 保留旧 Evaluation Method、Method Run、Schedule 和结果的兼容路径。

验收门禁：升级不改写或丢失历史运行；新 Target 版本不可变且所有引用可追溯。

### P19.2 Harness、Model 与 Target 管理

- [x] 实现 Harness 创建、probe、冻结、修订和归档。
- [ ] 实现未引用 Harness 草稿删除。
- [x] 校验 `{model}` 与 `model_policy`，继续使用 argv 和 `shell=False`。
- [x] 实现轻量 Model 目录，只保存显示信息和命令行 `argument`。
- [x] 实现 Target 兼容绑定、模型参数覆盖、probe、冻结和归档。
- [x] probe 不调用真实模型、不读取本地 endpoint 或凭据。
- [x] 采集 Harness Git revision 和 dirty 状态供运行审计。

验收门禁：三个 Agent Harness 和 `script-only` 均可配置；错误模型组合不能冻结或
提交。

### P19.3 Submission、Worker 与并发

- [x] 提交 API 使用冻结 `target_ids`，服务端重新校验并展开 Case × Target。
- [x] Manifest、工作区、报告文件和审计产物使用稳定 Target Key。
- [x] Target 冻结时解析 `{model}`，实际 argv 精确传递选中模型参数。
- [x] 使用内部 Materialized Method 复用 P15 隔离、取消、重试、租约和评分编排。
- [x] JobQueue 原子执行 Worker 全局、Harness 共享和 Target 可选并发门禁。
- [x] 失败、超时、取消和重试继续保留 P18 实际执行耗时。

验收门禁：两个 Case × 三个 Harness × 多个 Model 能并发生成互相隔离的报告，
Harness 共享上限不会被不同模型绕过。

### P19.4 结果、评分与聚合比较

- [x] `run.json` 和 `result.json` 增加 `generation.targets[]` 结构化身份和计时。
- [x] 评分候选使用 Target Key，显示名不再承担结果关联。
- [x] `result.md`、批次详情和组合对比页展示结构化 Harness、Model、得分、状态和生成耗时。
- [x] 实现全 Target 总览、固定 Harness 比 Model、固定 Model 比 Harness 三种视图。
- [x] 聚合平均得分、通过率、覆盖率、生成成功率、中位耗时、P95 和样本数。
- [x] 成对比较只使用共同 Case，并列出缺失、失败、超时和非受控差异。
- [x] `script-only` 参加总榜并标记为无模型基线，不进入固定模型透视。

验收门禁：同一批次既能判断 Harness 差异，也能判断 Model 差异；失败任务不会因
幸存者偏差从质量或速度统计中消失。

### P19.5 定时测评与前端闭环

- [x] 设置页拆分 Harness、Model 和运行组合管理。
- [x] 提交页按 Harness 展开兼容 Model，默认全选并显示 Case × Target 任务数。
- [x] 定时计划保存精确 Target，新组合不自动加入已有计划。
- [ ] 旧 Method 计划可继续运行，编辑后转换为 Target 计划。
- [ ] 批次详情、正式结果和 Dashboard 使用结构化 Target 元数据。
- [ ] 深浅主题下验证长名称、空 Model、失败状态和聚合表格。

验收门禁：用户无需拼接名称或处理内部 ID，即可配置组合、提交矩阵、观察运行并按
Harness/Model 比较。

### P19.6 历史兼容、测试与交付

- [ ] 旧 `generation.methods[]` 投影为 legacy Target，旧报告和结果继续可读。
- [ ] 为已确认的四个直接文件候选提供显式映射元数据，不正则解析历史名称。
- [ ] 覆盖模板校验、参数注入、无模型基线、版本、归档和删除规则。
- [ ] 覆盖矩阵展开、共享并发、取消、重试、定时触发和进程恢复。
- [ ] 覆盖聚合统计、共同 Case、失败覆盖率、旧数据和非受控比较。
- [ ] 运行迁移、后端全量测试、前端单测、生产构建和 Sites Worker 测试。

验收门禁：新旧评测可以并存，历史事实不被删除或错误归类，完整回归通过后再停用
旧 Evaluation Method 写入口。
