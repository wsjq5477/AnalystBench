---
name: analystbench-workflow
description: 在 claude 中用一个入口完成 AnalystBench Case 导入发布，或使用本地 Case JSON/数据库 case_key 对多份 AI 原始报告评分。只在项目返回确认问题时向用户提问。
---

# AnalystBench 一键工作流

在项目根目录执行命令。先读取 `docs/quickstart.md` 和 `docs/scoring-input.md` 的相关小节。

## 判断用户意图

- 用户给出一个 Case JSON 路径，并要求导入、审核、发布：执行"导入 Case"。
- 用户给出一个 Case JSON 路径和报告路径，并要求评分：直接执行本地文件评分，不导入数据库。
- 用户给出已发布 `case_key` 与一份或多份原始报告路径，并要求评分或对比：直接执行"评分报告"。
- 用户只有人工标准答案原文：按 `/analystbench-case` 的规则生成 Case JSON；不要索取 AI 报告。
- 用户只有 AI 报告原文且要评分：直接使用原文，不生成 Report JSON；只有用户明确要求封装时才调用 `/analystbench-report-draft`。

## 导入 Case

1. 向用户确认 `case_key`（用户命名，如 `kdiag-SYSMGR_PANIC-1`），不从文件名推断。后端会把它写回入库后的 Case JSON（磁盘上的源文件保持不变）。
2. 从 JSON 的 `case.test_set`、`case.category` 读取测试集和分类（纯字符串）；缺失时询问 `case_key`、测试集、分类这三项。
3. 执行 `db-upgrade`，再执行：

   ```bash
   .venv/bin/analystbench case-import <case.json> \
     --case-key <用例标识> \
     --test-set <测试集标识> \
     --category <分类标识>
   ```

4. 项目返回确认问题时，每次只展示第一项的字段路径、问题、当前值、建议值和选项，等待用户回答。不得直接修改 JSON 或替用户确认。
5. 发布后只报告 `case_key`、测试集、分类和版本。

## 评分报告

1. 若用户给出 Case JSON 路径，直接执行，不运行 `db-upgrade`：

   ```bash
   .venv/bin/analystbench evaluate <case.json> <report1.md> [report2.txt ...]
   ```

2. 若用户给出已发布 `case_key`，先执行 `db-upgrade`，再执行数据库模式：

   ```bash
   .venv/bin/analystbench evaluate <case_key> <report1.md> [report2.txt ...]
   ```

3. 默认使用 claude 语义 Judge。用户明确要求 OpenCode 时追加 `--judge opencode`；不得把 `--judge lexical` 当正式评分。
4. 文件模式出现未决项时向用户指出具体字段，不得替用户修改；数据库模式不重新审核已发布 Case。
5. 返回实际评分模式与 Case 来源、每份报告的总分、根因/分类/分析链综合判定、警告和对比结论，并链接 Markdown 报告路径。关键字分只采用 Python 结果；未命中时展示实际关键字和最接近报告行。不要粘贴完整审计 JSON。

## 约束

- 不要求用户处理数据库 ID、Batch ID 或 Revision ID。
- 不重新审核已发布 Case。
- claude/OpenCode 失败时原样说明错误；不得静默退回字符匹配。
