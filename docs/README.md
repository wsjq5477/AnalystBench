# AnalystBench 文档导航

`docs/` 根目录只放面向用户的安装、使用、配置和运维文档。工程实现规格和内部调研按用途放入子目录，避免用户指南与 Agent 开发上下文混在一起。

## 用户文档

| 文档 | 用途 |
|---|---|
| [快速上手](quickstart.md) | 单次评分、数据库部署、前端和 Skill 自优化入门 |
| [评分输入格式说明](scoring-input.md) | Case JSON、评分策略和 AI 报告输入格式 |
| [AnalystBench Skills 说明](skills.md) | 支持的 Agent Skill 及完整工作流 |
| [本地命令行工作流](cli-workflow.md) | 数据库模式下的完整 CLI 评测流程 |
| [运维与恢复](operations.md) | 服务启动、日志、备份、恢复与安全说明 |
| [Benchmark Suite 设计](benchmark-suites.md) | 用户可用的领域 Suite 与扩展方式 |

`images/` 只存放以上用户文档引用的图片资源。

## Agent 开发与内部调研

| 目录 | 内容 |
|---|---|
| [development/](development/README.md) | 给 Codex、claude 等 Agent 工具和开发者使用的工程设计、契约、决策与验收文档 |
| [benchmark/](benchmark/README.md) | 为设计和改进 Benchmark 开展的外部项目、数据集与评测方法调研 |
| [skillopt/](skillopt/README.md) | 为 Skill 自优化方法、实验和调优开展的调研 |
