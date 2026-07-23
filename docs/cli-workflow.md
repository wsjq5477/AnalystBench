# 本地命令行工作流

MVP 暂无前端，完整的 Benchmark A/B 评测可完全通过命令行完成。

1. 执行 `analystbench db-upgrade` 初始化或升级本地数据库。
2. 执行 `analystbench dataset-import examples/generic-analysis.json`，再用 `analystbench dataset-version-show <dataset-version-id>` 获取 Case Revision ID。
3. 使用 `candidate-create` 创建两个候选对象，并通过 `candidate-version-create` 创建两个不可变版本。
4. 为每个候选版本准备报告数组：`[{"case_revision_id":"...","report":"..."}]`，然后用 `candidate-report-import` 导入。
5. 使用 `scoring-policy-create v1` 创建评分策略。
6. 针对每个 Case 执行 `eval-spec-generate <case-revision-id> <policy-id>`；用 `eval-spec-draft-show <draft-id> --output spec.json` 导出草稿，人工审核和修改 JSON，将 `review.status` 设为 `approved`，将每个 `review_required` 设为 `false`，然后运行 `eval-spec-freeze <case-revision-id> spec.json`。
7. 用 `benchmark-run <dataset-version-id> <candidate-version-id> <policy-id>` 将每个候选版本加入队列，并执行 `analystbench worker` 直到队列完成。
8. 用 `benchmark-status` 查看状态，用 `benchmark-export` 输出结果产物，用 `compare <baseline-run-id> <candidate-run-id>` 比较结果。

每条命令都会输出可用于脚本调用的 ID 或 JSON。Worker 是唯一需要持续运行的组件。
