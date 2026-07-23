# AnalystBench Skills 说明

`src/analystbench/skills` 下目前保留 4 个有效 Skill。它们是给 Claude Code、Codex 等 Agent 使用的操作说明包：负责识别用户意图、准备正确输入、调用 AnalystBench CLI，并处理必要的人工确认。

Skill 不是评分算法本身。真正的 JSON 校验、数据持久化、语义判定和确定性计分都由 AnalystBench 后端完成。

## 整体流程

当前采用“Case 审核一次、报告反复评分”的流程：

```text
人工标准答案
    │
    ▼
analystbench-case ──→ Case JSON ──→ 审核并发布 ──→ case_key

case_key + 一份或多份 AI 原始报告
    │
    ▼
analystbench-evaluate ──→ 分数、通过状态、解释和多报告对比
```

`analystbench-workflow` 是这条流程的统一入口：它根据用户给出的是 Case、报告还是 `case_key`，选择生成、导入发布或批量评分操作。

## 4 个 Skill 分别做什么

| 名称 | 输入 | 产出或动作 | 适用场景 |
|---|---|---|---|
| `analystbench-workflow` | Case JSON，或 `case_key` 加多份原始报告 | 导入并发布 Case，或批量评分并对比 | 希望用一个入口完成常规操作 |
| `analystbench-case` | 人工标准答案原文/文件，或已有 Case JSON | 生成 Case JSON；用户明确要求时，交互审核并发布 | 新建、审核或发布标准答案 Case |
| `analystbench-report-draft` | 一份 AI 报告原文/文件 | 可选地封装 Report JSON | 明确需要额外元数据或兼容旧接口 |
| `analystbench-evaluate` | 已发布的 `case_key` 与一份或多份原始报告 | 直接评分，并以第一份报告为基线自动对比 | 同一个标准答案反复评测多份报告 |

## `analystbench-workflow`

这是面向普通使用者的一键入口，主要负责路由和编排：

- 给出 Case JSON 并要求导入时，执行 Case 审核和发布；
- 给出已发布 `case_key` 与原始报告时，直接执行批量评分和对比；
- 只有标准答案原文时，遵循 `analystbench-case` 的生成规则；
- 只有 AI 报告原文且需要评分时，不生成中间 JSON；
- 只有后端返回必须确认的问题时才暂停询问用户。

它不会自行修改原始 JSON、替用户确认结论，也不会在语义 Judge 失败后降级为字符匹配。

示例：

```text
/analystbench-workflow 导入并发布 ./case.json

/analystbench-workflow 使用 my-case 评分并对比 report-a.md 和 report-b.txt
```

## `analystbench-case`

这个 Skill 负责人工标准答案一侧，包含两个可独立执行的阶段：

1. 把标准答案原文转换成顶层只有 `case` 和 `eval_spec_draft` 的 Case JSON；
2. 用户明确要求入库时，调用 Case Draft/Import 流程，处理必要的确认问题并发布不可变 Case。

它会保留标准答案原文，要求 Claim 的 `quote` 是连续原文，并按当前根因/分类/分析链规则生成评分项。它不要求 AI 报告，也不会在发布 Case 时顺便评分。

关键规则包括：

- 根因 Claim ID 固定为 `root`，权重为 100；
- 分类 Claim ID 固定为 `category`，权重为 20；
- 分析链依次使用 `chain-1`、`chain-2`，等分且合计 60；
- 不确定信息写入 `unresolved_items`，不能替用户猜测；
- `case_key` 使用输出文件名去掉 `.json` 后的完整名称；
- 发布前确认测试集、分类、评分项数量和关键根因。

## `analystbench-report-draft`

这个 Skill 不是评分前置步骤，只在用户明确要求时把 AI 报告封装为可选 Report JSON。

- `candidate`：报告来源的显示信息；
- `candidate_report`：完整报告原文；
- `claim_hints`：兼容字段，默认 `[]`；
- `unresolved_items`：报告截断、信息不足等未决项。

它不需要查看标准答案，不判断报告是否正确，也不预填匹配关系和分数。报告与 Gold Claim 的语义对齐在正式评分时完成。

关键规则包括：

- `candidate_report` 必须保留原文；
- 不调用模型重复提取结论；
- 不修改报告原文；
- 仅补充用户需要的模型、运行类型、测试序号或耗时元数据。

## `analystbench-evaluate`

这个 Skill 使用已经发布的 Case 评测一份或多份报告：

- 输入使用可读的 `case_key`，不让用户处理 Dataset、Revision、Run 等内部 ID；
- 默认使用 Claude Code 做语义判定，也可按用户要求使用 OpenCode；
- Python 后端校验 Judge 输出并执行固定计分公式；
- 第一份报告作为基线，后续报告给出分差和提升、退化或不变结论；
- 输出每份报告的总分、通过状态，以及根因、分类和分析链各分项。

`--judge lexical` 只用于开发调试，不能作为正式评分结果。Claude Code 或 OpenCode 失败时，Skill 应原样报告错误，不能静默降级。

## 如何选择

| 你的输入与目标 | 应使用 |
|---|---|
| 只有人工标准答案，想生成或发布 Case | `analystbench-case` |
| 只有一份 AI 报告，想直接评分 | `analystbench-evaluate` |
| 明确需要把报告封装成带元数据的 JSON | `analystbench-report-draft` |
| 已有 `case_key`，想评一份或多份报告 | `analystbench-evaluate` |
| 想用一个入口自动判断导入还是评分 | `analystbench-workflow` |

日常使用优先从 `analystbench-workflow` 开始；需要精确控制某个阶段时，再直接调用另外三个专用 Skill。

## 目录与安装

仓库中目前保留两份相同的 Skill 文件：

- `src/analystbench/skills`：随项目源码维护和打包的源目录；
- `.claude/skills`：Claude Code 的项目级发现目录。

仅把 Skill 放到 `src/analystbench/skills`，并不意味着 Claude Code 或 Codex 会自动发现它。运行环境仍需通过项目级目录、用户级 Skill 目录、安装脚本或符号链接暴露 Skill。每个可发现 Skill 的根目录至少要有一个合法的 `SKILL.md`。

常见附属文件的作用：

| 路径 | 作用 |
|---|---|
| `SKILL.md` | 必需入口，定义触发场景、工作流、约束和输入输出 |
| `agents/openai.yaml` | 可选的界面元数据，如显示名、简介和默认提示词 |
| `scripts/` | 可选的确定性辅助脚本，例如输入校验 |
| `references/` | 可选的详细协议或接口参考 |

为避免两份副本以后漂移，建议以 `src/analystbench/skills` 为唯一源目录，并增加安装或同步步骤，将它们复制或链接到目标 Agent 的发现目录。

## 已删除的旧目录

以下目录不属于当前流程，已从源码目录和 `.claude/skills` 中删除：

- `analystbench-reference-draft`：其标准答案转换能力已由 `analystbench-case` 覆盖；
- `analystbench-score`：旧的单次 Evaluation Session 入口，已由“发布 Case + `analystbench-evaluate`”流程替代；
- `analystbench-quick-import`：只有空目录，没有 `SKILL.md` 或实现。

历史设计文档中仍可能出现 `analystbench-score`，用于记录 P10 阶段的设计演进，不代表当前推荐入口。
