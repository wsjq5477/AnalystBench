# 评分输入格式说明

AnalystBench 接收一份 UTF-8 Case JSON，以及一份或多份 UTF-8 AI 报告原文。AI 报告不需要转换成 JSON。

Case 只需审核并发布一次。之后用稳定的 `case_key` 对任意数量的报告反复评分。JSON 中不要包含 Markdown 代码围栏，也不需要填写数据库 ID、内容哈希或 `source_ref`。

## 评分执行方式

正式评分直接读取完整 AI 报告原文，不把报告切分成句子、Candidate Claim 或 Report JSON。

本地 Case JSON 的 Claude Skill 工作流是：先运行 `prepare-alignment` 生成对齐草稿；Python 在草稿中预先写入每条分析链的 `python_keyword_audits`；Claude 只补充每个 Gold Claim 的语义判定和 `conclusion_similarity`；最后运行 `score-with-alignment` 校验哈希后计分。

日志 `evidence_keyword` 的强匹配完全由 Python 处理，不能由大模型改写或判定；模型只判断根因、分类和分析链结论的语义。

## 一、人工标准答案 Case

顶层固定包含 `case` 和 `eval_spec_draft`。

### `case` 字段

| 字段 | 必填 | 含义与推荐输入 |
|---|---|---|
| `case_key` | 是 | 用例编号，使用 Case JSON 文件名去掉 `.json` 后的完整名称（如 `HM_PANIC_SYSMGR-case1`）。由脚本或用户传入，不由 AI 另起名称。 |
| `test_set` | 是 | 所属测试集，包含稳定的 `key` 和显示用 `name`。`key` 为测试集标识（如 `kdiag`）。也可以在 `case-import` 参数中填写。 |
| `category` | 是 | 问题类型，包含 `key` 和 `name`。`key` 为问题类型标识（如 `SYSMGR_PANIC`）。也可以在 `case-import` 参数中填写。 |
| `problem_statement` | 是 | 只描述待分析的问题和已知现象，不提前泄露标准答案。 |
| `reference_answer` | 是 | 人工确认的标准答案完整原文，不得用 AI 报告替代或改写。 |
| `traces` | 否 | 日志、snapshot、调用栈等原始材料数组；不需要时省略或填 `[]`。 |

### 根因、问题分类与分析链的标准化规则

当人工答案采用“问题分类 + 问题根因 + 证据N/结论N”格式时，评分项必须按以下方式生成：

1. 一个 `root_cause` Claim，对应人工答案中的唯一根因。
2. 一个 `classification` Claim，对应人工答案中的问题分类。
3. 每一组“证据N + 结论N”生成一个 `analysis_chain` Claim。日志关键字写入 `evidence_keyword`，结论写入 `conclusion` 与 `statement`。
4. 不得把同一组证据和结论拆成两个 Claim；三组证据/结论就是三个分析链 Claim。
4. 不生成“直接原因”评分项，也不使用 `direct_cause` 类型。
5. 根因权重固定为 100，问题分类固定为 20；所有分析链 Claim 等分 60 分。三条分析链每条为 20（关键字 10、结论 10）；四条时每条为 15（关键字 7.5、结论 7.5）。
6. `causal_edges` 填 `[]`。该模式的计分路径由 `scoring_strategy` 明确指定。

### `eval_spec_draft.claims` 字段

| 字段 | 必填 | 含义与允许值 |
|---|---|---|
| `id` | 是 | 根因固定为 `root`；问题分类固定为 `category`；分析链按顺序使用 `chain-1`、`chain-2`；其他通用评分项使用 `claim-1`、`claim-2`。 |
| `type` | 是 | 根因使用 `root_cause`，问题分类使用 `classification`，分析链使用 `analysis_chain`。通用 Case 还可使用 `trigger`、`symptom`、`localization`、`mechanism`、`impact`、`action`。 |
| `statement` | 是 | 用于评分的标准化原子结论。候选报告需表达相同语义，不要求逐字相同。 |
| `importance` | 是 | `critical`、`high`、`normal`、`low` 之一。根因使用 `critical`，证据链推荐 `normal`。 |
| `weight` | 是 | 根因固定 100，分类固定 20；分析链等分且合计 60，允许小数。 |
| `evidence_keyword` | 分析链必填 | 用于 Python 强匹配的稳定、必要日志片段；仅忽略大小写与空白差异，必须连续出现在 AI 报告原文中才得该半分。不要附带非必要的时间戳、源码行号或函数前缀。 |
| `conclusion` | 分析链必填 | 对应证据的标准结论；与 `statement` 保持同义，语义 Judge 返回 0～1 相似度。 |
| `quote` | 是 | `reference_answer` 中连续且逐字一致的原文，用于证据追溯。分析链可引用对应的完整“证据N + 结论N”片段。 |
| `review_required` | 是 | AI 生成草稿时填 `true`；项目发布时会在用户确认后冻结为 `false`。 |

`statement` 是要评分的结论，允许标准化表达；`quote` 是人工答案里的连续原文，不能写总结或拼接多个不连续片段。

### 评分策略

根因/分类/分析链 Case 必须包含：

```json
"scoring_strategy": {
  "mode": "root_category_chain",
  "root_cause_score": 100,
  "category_score": 20,
  "chain_total_score": 60
}
```

计分顺序固定为：

1. 根因完全命中：直接得 100 分，停止后续评分，不计算问题分类和分析链。
2. 根因部分命中、未命中或矛盾：根因得 0 分，进入分类与分析链评分。
3. 问题分类语义完全正确得 20 分，否则得 0 分。分类编码、中文名或英文名只要表示同一问题类别即可命中；例如 `HM_PANIC_SYSMGR` 与 `sysmgr panic` 等价。仅写泛化的 `panic` 不足以命中该分类。
4. 分析链共 60 分，按分析链条数均分。每条的一半由 `evidence_keyword` 强匹配决定（命中即满、未命中即零）；另一半由语义 Judge 对 `conclusion` 给出的 0～1 相似度决定。
5. 未完全命中根因时，最终分为 `分类得分 + 所有分析链得分 - forbidden_claims 扣分`，最多 80 分；没有幻觉扣分。

关键字部分始终由 Python 对完整报告原文确定性计算，Claude/OpenCode 不参与。评分报告会显示实际使用的关键字；未命中时还会显示最接近的报告行，但最近行只用于解释，不参与计分。

### 其他评分字段

| 字段 | 必填 | 含义与推荐输入 |
|---|---|---|
| `causal_edges` | 是 | 根因/分类/分析链模式填 `[]`。通用加权模式才单独定义因果边。 |
| `forbidden_claims` | 是 | 明确且常见的错误结论；没有时填 `[]`。命中后按其 `penalty` 扣分。 |
| `unresolved_items` | 是 | 无法从人工答案确认、需要用户判断的问题；没有时填 `[]`。非空时导入会要求用户解决或排除。 |

### 推荐 Case 示例

```json
{
  "case": {
    "case_key": "1",
    "test_set": {"key": "kdiag", "name": "Kdiag 内核诊断"},
    "category": {"key": "SYSMGR_PANIC", "name": "SYSMGR Panic"},
    "problem_statement": "分析系统出现 suspend-to-mem 超时 panic 及 sh 进程卡住的根因。",
    "reference_answer": "问题分类：HM_PANIC_SYSMGR\n问题根因：调度问题，开抢占未REPICK，导致线程开抢占后可能跑错核\n分析链：\n证据1：suspend to mem is timeout\n结论1：休眠超时\n证据2：cpuhp: listener devmgr.actv handling cpu1 event: 2 enter\n结论2：cpuhp卡主\n证据3：liblinux_remove_cpu\n结论3：卡在liblinux_remove_cpu的schedule，怀疑调度相关"
  },
  "eval_spec_draft": {
    "claims": [
      {
        "id": "root",
        "type": "root_cause",
        "statement": "调度问题，开抢占未REPICK，导致线程开抢占后可能跑错核",
        "importance": "critical",
        "weight": 100,
        "quote": "调度问题，开抢占未REPICK，导致线程开抢占后可能跑错核",
        "review_required": true
      },
      {
        "id": "category",
        "type": "classification",
        "statement": "HM_PANIC_SYSMGR",
        "importance": "high",
        "weight": 20,
        "quote": "问题分类：HM_PANIC_SYSMGR",
        "review_required": true
      },
      {
        "id": "chain-1",
        "type": "analysis_chain",
        "statement": "休眠超时",
        "importance": "normal",
        "weight": 20,
        "evidence_keyword": "suspend to mem is timeout",
        "conclusion": "休眠超时",
        "quote": "证据1：suspend to mem is timeout\n结论1：休眠超时",
        "review_required": true
      },
      {
        "id": "chain-2",
        "type": "analysis_chain",
        "statement": "cpuhp卡主",
        "importance": "normal",
        "weight": 20,
        "evidence_keyword": "cpuhp: listener devmgr.actv handling cpu1 event: 2 enter",
        "conclusion": "cpuhp卡主",
        "quote": "证据2：cpuhp: listener devmgr.actv handling cpu1 event: 2 enter\n结论2：cpuhp卡主",
        "review_required": true
      },
      {
        "id": "chain-3",
        "type": "analysis_chain",
        "statement": "卡在liblinux_remove_cpu的schedule，怀疑调度相关",
        "importance": "normal",
        "weight": 20,
        "evidence_keyword": "liblinux_remove_cpu",
        "conclusion": "卡在liblinux_remove_cpu的schedule，怀疑调度相关",
        "quote": "证据3：liblinux_remove_cpu\n结论3：卡在liblinux_remove_cpu的schedule，怀疑调度相关",
        "review_required": true
      }
    ],
    "scoring_strategy": {
      "mode": "root_category_chain",
      "root_cause_score": 100,
      "category_score": 20,
      "chain_total_score": 60
    },
    "causal_edges": [],
    "forbidden_claims": [],
    "unresolved_items": []
  }
}
```

## 二、AI 分析报告

正式评分默认直接接收 UTF-8 原始报告文件，不需要转换成 JSON。Claude/OpenCode 读取完整报告原文，Python 在同一原文中做日志关键字强匹配。

Report JSON 只是可选的元数据封装，适合旧接口或需要附加模型、Prompt、耗时等信息时使用：

| 字段 | 必填 | 含义与推荐输入 |
|---|---|---|
| `candidate.name` | 是 | 候选模型、Agent 或 Prompt 版本的可读名称。正式评测显示名以输入文件名 stem 为准。 |
| `candidate.description` | 否 | 候选说明。 |
| `candidate.metadata` | 否 | 模型、Prompt、Agent 版本等比较信息；不要放密钥。 |
| `candidate_report` | 是 | AI 分析报告完整原文，不得摘要、改写或只保留结论。 |
| `claim_hints` | 否 | 兼容字段，推荐填 `[]`。正式评分直接读取完整报告，不要求用户提前提取结论。 |
| `unresolved_items` | 否 | 报告自身声明的未决问题；不用时填 `[]`。 |

最小输入示例：

```json
{
  "candidate": {
    "name": "HM_PANIC_SYSMGR-test1-agent-1",
    "metadata": {"run_type": "agent", "attempt": 1}
  },
  "candidate_report": "这里放 AI 分析报告完整原文",
  "claim_hints": [],
  "unresolved_items": []
}
```

## 三、发布与评分

发布 Case：

```bash
.venv/bin/analystbench case-import ./kdiag-SYSMGR_PANIC-1.json \
  --test-set kdiag \
  --test-set-name "Kdiag 内核诊断" \
  --category SYSMGR_PANIC
```

评分并对比多份报告：

```bash
.venv/bin/analystbench evaluate \
  kdiag-SYSMGR_PANIC-1 \
  ./HM_PANIC_SYSMGR-test1-agent-1.md \
  ./HM_PANIC_SYSMGR-test1-skill-1.txt
```

`evaluate` 默认调用本机 `claude -p` 做语义判定，再由 Python 按上述固定公式计分。
使用 OpenCode 时增加 `--judge opencode`。`--judge lexical` 仅供开发调试，不能作为正式结果。

同名 Case 文件再次导入会发布新的 Revision 并保留旧版本；不会修改源 JSON。数据库评分使用已发布的不可变版本，不会自动读取后来修改的同名本地 JSON。评分报告会显示本地文件路径，或数据库 Case 版本与 Eval Spec ID。
