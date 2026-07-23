---
name: analystbench-workflow
description: 在 Claude 中完成 AnalystBench Case 导入发布，或用本地 Case JSON/已发布 case_key 对多份 AI 原始报告评分。只在项目返回确认问题时向用户提问。
---

# AnalystBench 一键工作流

在项目根目录执行命令。原始 AI 报告不转换 JSON，也不切分成 Candidate Claim。

## 判断用户意图

- Case JSON + "导入/发布"：按 `/analystbench-case` 审核后执行 `case-import`。
- Case JSON + 一份或多份报告：使用本地文件评分，不导入数据库。
- 已发布 `case_key` + 报告：使用数据库模式 `evaluate`。
- 只有人工标准答案：调用 `/analystbench-case`；只有报告不能单独评分。

## 本地 Case JSON 评分

1. 生成 Python 评分草稿：

   ```bash
   .venv/bin/analystbench prepare-alignment <case.json> <report1.md> [report2.md ...] --output ./data/workspaces/alignment-draft.json
   ```

2. 当前 Claude 阅读完整报告、`case.gold_claims` 和草稿；只填写
   `reports.<报告名>.semantic_alignment.alignments`。只判断根因、分类和结论语义；不得修改哈希和 `python_keyword_audits`，不得定位日志、提取 quote 或创建 Candidate Claim。

3. 调用确定性计分：

   ```bash
   .venv/bin/analystbench score-with-alignment <case.json> ./data/workspaces/alignment-draft.json <report1.md> [report2.md ...]
   ```

4. 展示总分、根因、分类、每条分析链的 Python 关键字强匹配分与语义结论分。校验失败时只说明字段和原因，请用户确认或修改，不修改用户文件。

## 数据库模式

```bash
.venv/bin/analystbench evaluate <case_key> <report1.md> [report2.md ...]
```

数据库 Worker 仍会调用本机 Claude/OpenCode 对完整报告做语义判断；Python 负责关键字强匹配与最终计分。不得静默降级为 lexical。
