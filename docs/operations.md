# 运维与恢复

## 本地部署

执行 `analystbench serve` 会先运行 Alembic 数据库升级，再启动 API 和独立
Local Worker 子进程。API 与 Worker 使用同一份已配置的本地 SQLite 数据库和
Content Store 路径。任一步骤启动失败时，组合服务退出。

无需保留终端窗口时执行：

```bash
analystbench serve --detach
analystbench service status
analystbench service logs
analystbench service stop
```

后台服务默认将标准输出和错误输出追加到
`data/logs/analystbench.log`，PID 记录位于 `data/run/analystbench.pid`。
启动命令默认等待就绪 60 秒，并使用不经过系统 HTTP 代理的本机直连探测。
较慢机器可使用 `analystbench serve --detach --startup-timeout 120`，或设置
`ANALYSTBENCH_SERVICE_STARTUP_TIMEOUT_SECONDS=120`。
后台启动会拒绝重复实例；停止命令同时终止 API 与 Worker。原有
`analystbench db-upgrade`、`analystbench api` 和 `analystbench worker`
仍用于故障排查及拆分部署。

## 备份与恢复

复制 SQLite 数据库和整个 Content Store 前，先执行
`analystbench service stop`（拆分部署时分别停止 API 与 Worker）。恢复时必须
将两者恢复到匹配的路径，再使用 `analystbench serve` 启动；该命令会先执行
数据库升级。数据库引用不可变的 SHA-256 内容块，只恢复其中一侧会导致数据无效。

## 安全与隐私

执行配置与应用设置都不接收凭据。claude 和 OpenCode 使用用户在本地 CLI 中完成的认证。Worker 以 argv 数组而非 Shell 执行命令，在每个 Case 的临时工作目录中运行；原始输出仅保留在本地 Content Store。API 响应只暴露哈希值和结构化元数据，不直接返回 Agent 原始输出。

## 失败恢复

任务带有租约。重启 Worker 后，过期租约会回到队列。失败的 Benchmark Case 可通过 `POST /api/v1/benchmark-runs/{id}:retry-failed` 重新入队，已成功的 Case 结果会保留。取消为协作式：排队 Case 会被跳过，正在执行的第三方 CLI 会等待完成或超时。
