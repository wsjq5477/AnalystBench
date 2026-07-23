# MVP 发布检查清单

- [x] 新建 SQLite 数据库可迁移至当前 Alembic head。
- [x] API 健康检查与持久化本地 Worker 均有自动化测试覆盖。
- [x] 数据集、候选项、Eval Spec、Benchmark、对比、CLI 与 Suite 流程均有自动化覆盖。
- [x] Agent 执行通过 `claude -p` / `opencode run` 的直接 argv 适配器完成，具备临时工作目录、输出限制及无凭据配置。
- [x] Eval Spec 冻结会拒绝未解决的审核项和伪造的原文引证。
- [x] Benchmark 清单引用已冻结版本；成功的 Case 结果不可变，失败 Case 可重试。
- [x] 已提供离线 generic-analysis 与 KDiag 示例数据集。
- [x] 已在 `docs/operations.md` 记录备份、恢复、安全与隐私说明。

## 发布前环境检查

依次执行 `python -m ruff check .`、`python -m pytest`、`analystbench db-upgrade`、`analystbench --help` 和 `analystbench suite-list`。然后启动 `analystbench api` 并访问 `/api/v1/health/ready`。
