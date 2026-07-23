# Agent Runner 设计：Claude Code 与 OpenCode

状态：Accepted（P0.1，2026-07-21）

## 目标与边界

Agent Runner 在本地后台执行 Claude Code 或 OpenCode，为指定 Case 生成 Candidate Report。它不生成 Eval Spec、不充当 Judge，也不直接计分。

第一版只评价最终报告。Runner 会保存结构化事件和执行元数据，但 Trace、工具调用质量、Token 效率和执行步骤不进入分数。

## 为什么生成与评分分离

    Candidate Generation Run
      -> Agent Case Run
      -> frozen Candidate Report
      -> Benchmark Run
      -> Score Result

Agent 输出和 Judge 输出都可能波动。先冻结 Candidate Report，再独立执行 Benchmark，可以重用同一报告比较不同 Eval Spec/Judge/评分策略，也可以只重跑 Agent 而不污染历史评分。

## Runner 协议

每个 Runner 实现以下能力：

- probe：检查可执行文件、版本和基本可用性，不读取或回显凭据。
- build_command：由结构化配置生成 argv，不拼接 Shell 命令字符串。
- prepare_workspace：把 Case 材料复制到独立临时工作区并生成只读任务说明。
- execute：启动子进程、流式读取事件、处理超时和取消。
- normalize：从事件中提取最终 Assistant 文本，形成 Candidate Report。
- collect：保存 stdout/stderr、事件、使用量、退出状态和内容哈希。

Runner 返回统一 AgentRunArtifact：runner_id/version、cli_version、model、agent、prompt_hash、workspace_hash、policy_hash、started/finished、exit_code、termination_reason、usage、final_report、raw_events_ref 和 stderr_ref。

## Claude Code Runner

基础形式：

    claude -p <prompt> --output-format json

受控模式可增加 `--bare`、显式 settings、model 和 allowedTools。`--bare` 会跳过用户/项目自动发现的 hooks、skills、plugins、MCP 和 memory，因此更适合可复现 Benchmark，但认证必须由显式 Anthropic 凭据或 settings helper 提供。

本地会话模式允许 Claude CLI 使用当前登录和本机配置，便于首次运行；Manifest 必须将其标记为 environment_controlled=false，并记录可安全采集的配置文件哈希和命令参数，不能读取认证内容。

官方参考：[Run Claude Code programmatically](https://code.claude.com/docs/en/headless) 和 [CLI reference](https://code.claude.com/docs/en/cli-usage)。

## OpenCode Runner

基础形式：

    opencode run --format json --model <provider/model> --agent <agent> --dir <workspace> <prompt>

第一版每个 Agent Case Run 启动独立命令。后续可选择 `opencode serve` + `--attach` 或 OpenCode SDK 降低冷启动并增强流式控制；这不是 MVP 必需条件。

OpenCode Profile 生成独立配置，将 edit、external_directory、web 和 bash 等权限映射到执行策略。Runner 不修改用户的全局 OpenCode 配置或认证存储。

官方参考：[OpenCode CLI](https://opencode.ai/docs/cli/)、[OpenCode SDK](https://opencode.ai/docs/sdk/) 和 [OpenCode Agents/Permissions](https://opencode.ai/docs/agents/)。

## Execution Profile

版本化 Profile 至少包含：

- runner：claude-code 或 opencode。
- executable：受信任可执行文件路径或名称。
- model、agent、CLI flags 和 adapter version。
- prompt template version 与 Case 材料布局版本。
- environment mode、tool permission policy 和 network policy。
- timeout、graceful_cancel_timeout、max_output_bytes 和 concurrency_limit。
- 可选安全环境变量名称白名单；不保存值。

Profile 冻结后不可修改。任何参数、Prompt 或权限变化都产生新版本。

## 工作区与权限

- 每次执行创建独立目录，只复制该 Case 明确声明的材料。
- 子进程 cwd 必须是临时工作区，不能是 AnalystBench 仓库或原始 Dataset 目录。
- 默认只允许读、列举和搜索工作区内容；编辑、外部目录、网络与命令执行需要 Profile 显式启用。
- 允许编辑时也只修改临时副本，最终报告仍从 Assistant 输出提取。
- 本地权限限制不是恶意代码的强安全沙箱；MVP 只面向受信任 Agent/Case。容器/VM 隔离属于后续扩展。

## Prompt 与最终报告

统一 Prompt 包含任务目标、材料索引、输出要求和安全边界。Runner 不要求 Agent 写入特定文件；正常情况下取最后一个成功的 Assistant 最终文本作为 Candidate Report。

若成功退出但没有非空最终文本，Agent Case Run 失败。Runner 不从 stderr、thinking/tool 事件或工作区临时文件猜测最终报告。

## 后台任务与状态

    queued -> preparing -> running -> collecting -> succeeded
       |          |          |           |
       +----------+----------+-----------+-> failed
    queued/running -> cancelling -> cancelled

Local Worker 通过数据库租约领取 Agent Case Run。取消先发送正常终止信号，超过宽限期后再结束进程树。Worker 崩溃导致租约过期时，任务进入 interrupted；是否自动重试取决于错误类别和重试策略。

## 失败分类

- runner_unavailable：CLI 不存在或版本不兼容。
- authentication_required：CLI 未登录或凭据不可用。
- invalid_profile：参数、权限或模型配置无效。
- workspace_prepare_failed：材料复制或清单校验失败。
- timeout / cancelled / process_exit_nonzero。
- output_limit_exceeded / output_invalid / final_report_missing。
- worker_interrupted：Worker 或主机中断。

认证、无效 Profile 和稳定的非零退出不自动重试；Worker 中断和确定的临时启动故障可以重试。重试会创建新 attempt，不能覆盖原始事件。

## 凭据边界

AnalystBench 不创建、导入、展示或持久化 Claude/OpenCode 凭据。CLI 使用它自身的登录状态或允许的环境变量。Manifest 只记录变量名、认证模式和非敏感配置哈希。

## 可比较性

Claude Code 与 OpenCode 可以作为不同 Candidate 直接比较，前提是 Case Revision、任务 Prompt、材料布局、工具/网络权限、超时和评分输入一致。Agent/模型/CLI 版本属于被比较变量，必须记录而不要求相同。
