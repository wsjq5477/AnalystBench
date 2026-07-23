---
name: analystbench-report-draft
description: 将一份 AI 报告原文可选地封装为带候选名称和运行元数据的 AnalystBench Report JSON。仅在用户明确要求生成 Report JSON、补充模型元数据或对接旧接口时使用；直接评分 txt、md、log 报告不需要调用本 Skill。
---

# AnalystBench 可选报告封装

Report JSON 不是评分前置步骤。用户只是要评分或对比原始报告时，改用 `analystbench-evaluate` 直接传入报告文件。

只有用户明确要求 Report JSON 时才执行：

1. 完整保留 `candidate_report` 原文，不摘要、不改写。
2. 顶层只输出 `candidate`、`candidate_report`、`claim_hints`、`unresolved_items`。
3. `candidate.name` 使用原始文件名 stem；可把模型、运行类型、测试序号、次数和耗时写入 `candidate.metadata`。
4. `claim_hints` 默认填 `[]`。不要调用模型重复提取结论，也不要预填匹配关系或分数。
5. `unresolved_items` 默认填 `[]`；只有报告内容确实截断或输入缺失时才记录。
6. 只输出 JSON，不导入数据库、不运行评分。

最小结构：

```json
{
  "candidate": {
    "name": "HM_PANIC_SYSMGR-test1-agent-1",
    "metadata": {
      "run_type": "agent",
      "attempt": 1
    }
  },
  "candidate_report": "完整 AI 报告原文",
  "claim_hints": [],
  "unresolved_items": []
}
```
