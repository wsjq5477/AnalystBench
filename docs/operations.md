# 运维与恢复

## 本地部署

执行 `analystbench db-upgrade`，然后在两个独立终端分别启动 `analystbench api` 和 `analystbench worker`。API 与 Worker 使用同一份已配置的本地 SQLite 数据库和 Content Store 路径。

## 备份与恢复

复制 SQLite 数据库和整个 Content Store 前，先停止 API 与 Worker。恢复时必须将两者恢复到匹配的路径，执行 `analystbench db-upgrade` 后再重启服务。数据库引用不可变的 SHA-256 内容块，只恢复其中一侧会导致数据无效。

## 安全与隐私

执行配置与应用设置都不接收凭据。Claude Code 和 OpenCode 使用用户在本地 CLI 中完成的认证。Worker 以 argv 数组而非 Shell 执行命令，在每个 Case 的临时工作目录中运行；原始输出仅保留在本地 Content Store。API 响应只暴露哈希值和结构化元数据，不直接返回 Agent 原始输出。

## 失败恢复

任务带有租约。重启 Worker 后，过期租约会回到队列。失败的 Benchmark Case 可通过 `POST /api/v1/benchmark-runs/{id}:retry-failed` 重新入队，已成功的 Case 结果会保留。取消为协作式：排队 Case 会被跳过，正在执行的第三方 CLI 会等待完成或超时。
