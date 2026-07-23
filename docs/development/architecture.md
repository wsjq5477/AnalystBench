# 后端架构

状态：Accepted（P0.1 Agent Execution 范围修订，2026-07-21）

## 技术基线

推荐使用 Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、SQLite 和 pytest。SQLite 是 MVP 默认存储；ORM 和迁移脚本避免依赖 SQLite 专有行为，为 PostgreSQL 迁移保留空间。

## 组件

    API / CLI
        |
    Application Services
        |-- Dataset Service
        |-- Agent Execution Service
        |-- Eval Spec Service
        |-- Evaluation Service
        `-- Comparison Service
        |
    Evaluation Core
        |-- Eval Spec Validator
        |-- Candidate Analyzer
        |-- Claim Retriever
        |-- Claim/Edge Alignment
        `-- Deterministic Scorer
        |
    Ports / Adapters
        |-- Repository Adapters
        |-- LLM Adapters
        |-- Claude Code Runner
        |-- OpenCode Runner
        |-- Suite Registry
        `-- Local Worker
        |
    SQLite + Content Store

## 依赖规则

- Evaluation Core 只依赖领域类型和标准库，不依赖 FastAPI、SQLAlchemy 或具体模型 SDK。
- Application Services 编排事务和状态，不包含评分数学规则。
- API 和 CLI 调用同一 Application Service，不复制业务逻辑。
- Agent Runner 只负责启动外部 Agent、采集事件和规范化最终报告，不参与 Eval Spec 生成或评分。
- LLM 通过协议接口接入；Core 不识别 OpenAI、Ollama 等供应商名称。
- Suite 只能注册模板、Prompt、默认策略和确定性检查器，不能修改 Core 数据表和计分流程。

## 进程模型

MVP 包含两个进程角色：API/CLI 进程和 Local Worker。API 创建后台任务并立即返回任务标识；Worker 从数据库领取持久化 Job，执行 Claude Code/OpenCode 子进程、模型调用与评分。开发模式可在单进程中显式运行 Worker，但不使用易丢失的内存队列作为正式运行方式。API 或 Worker 重启后，未完成任务必须可以继续领取或重试。

Agent CLI 是 Worker 启动的受控子进程。每次执行使用独立工作目录、显式 Prompt/权限/超时和捕获的标准输出/错误输出；Runner 不通过 Shell 字符串拼接命令，参数必须以 argv 数组传递。

## 数据存储

- 关系数据库保存元数据、版本、状态、结构化产物和结果索引。
- 较大文本以内容哈希寻址存放在本地 Content Store；数据库保存哈希、长度、媒体类型和相对路径。
- Prompt 模板、模型参数、JSON 输出和错误摘要进入 Run 审计记录。
- Agent 原始 JSON 事件、最终报告和执行元数据进入 Content Store；临时工作区不是事实源。
- API 密钥只来自环境变量或本地密钥配置，不写入数据库和日志。

## 一致性和并发

- 创建冻结版本、领取 Job、提交单 Case 结果必须使用事务。
- 冻结对象不可原地更新；修改会创建新草稿/版本。
- Worker 通过租约时间和 attempt 标识避免重复提交；结果写入使用幂等键。
- SQLite 模式只承诺单主机、低并发 Worker；并发扩展属于后续阶段。

## 可观测性

- 结构化日志至少包含 request_id、run_id、case_id、job_id 和 attempt。
- 不记录完整标准答案、Candidate Report 或 API 密钥。
- Run 和 Case Run 保存耗时、模型调用次数、重试次数及 Token 用量（供应商返回时）。
- Agent Run 额外保存 CLI 版本、PID/退出码、超时/取消原因、执行策略和产物哈希。
