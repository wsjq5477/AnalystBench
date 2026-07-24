---
name: analystbench-case
description: 将一份人工标准答案转换为 AnalystBench Case JSON，或把已有 Case JSON 交互式审核并发布到本地基准库。适用于用户说"生成 Case""导入标准答案""审核 Case""加入 benchmark 库"等场景；不需要 AI 报告。
---

# AnalystBench Case 生成与入库

标准答案转换和 Case 入库是两个独立阶段。只处理 Case，不索取 AI 报告，也不在 Case 发布时进行报告评分。

## 阶段一：生成 Case JSON

当用户提供人工标准答案原文或文件、但还没有 Case JSON 时：

1. 读取 AnalystBench 项目根目录的 `docs/scoring-input.md`。
2. 按其中"人工标准答案 Case"格式生成 JSON，顶层键只能是 `case` 和 `eval_spec_draft`。
3. 原样保留 `reference_answer`；每个 Claim 的 `quote` 必须是其中的连续原文。
4. 当答案是"问题分类 + 问题根因 + 证据N/结论N"时，只生成一个 `critical root_cause`、一个 `classification` 和每组一个 `analysis_chain`；不生成"直接原因"。
5. 根因权重固定100，分类权重固定20；所有分析链等分且合计60。每条分析链必须写 `evidence_keyword`（证据原文）和 `conclusion`（结论原文）；`causal_edges=[]`；使用 `root_category_chain` 评分策略。
6. 根因 ID 固定为 `root`，问题分类固定为 `category`，分析链依次使用 `chain-1`、`chain-2`；其他通用评分项使用 `claim-1`、`claim-2`。禁止旧的 `g1`、`g2` 格式。
7. 不确定信息写入 `unresolved_items`，不能替用户猜测。
8. 用户只要求转换时，输出或按用户指定路径保存 JSON 后停止，不自动入库。
9. `case.case_key` 使用输出文件名去掉 `.json` 后的完整名称；不要另起语义化名称。
10. 确认测试集与用例分类，并写入 `case.test_set`、`case.category`。

## 阶段二：审核并发布

当用户明确要求把某个 Case JSON 加入本地基准库时，在项目根目录执行：

```bash
analystbench case-import <case.json> --yes
```

此命令会：
1. 审核 Case JSON 并自动确认所有审核项
2. 发布到本地基准库（数据库）
3. **自动同步 case.json 到 `data/results/{test_set}/{category}/{case_dir}/` 目录**，确保前端 `/local-cases/tree` 能立即看到

如需手动逐项确认，去掉 `--yes`。确认完成后命令同样会自动同步文件。

最终只向用户报告 `case_key`、测试集、分类、版本和发布状态。内部 ID 不要求用户理解或保存。

## 交互规则

- JSON 合法时只进行一次整体确认，不逐个确认 `review_required`。
- 后端指出字段错误时，原样展示 Claim 上下文和字段含义。
- 后端返回 `failed` 时，原样说明 `error.code` 和 `error.message`，不猜测修复值。
- `case_key` 由文件名决定，禁止为了处理冲突而随意改成语义化名称。
- 发布前必须确认项目标准化后的评分项数量与原始结构一致。
