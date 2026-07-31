# P19 Harness 与 Model 解耦评测设计

状态：Accepted（2026-07-29）

## 背景

P15 的 Evaluation Method 把报告生成工程、模型选择、命令模板、执行限制和结果名称
冻结在同一个版本中。当前结果因此使用以下一体化名称：

```text
script-only
claude(glm5.1)-skill
claude(glm5.1)-native
hmdiagAgent(deepseek-v4-flash)
```

这种结构能执行和评分，但不能可靠回答以下问题：

- 同一个 Harness 使用不同模型时，质量和生成耗时如何变化。
- 同一个模型使用不同 Harness 时，质量和生成耗时如何变化。
- 一个新模型加入后，哪些 Harness 支持它，提交时会产生哪些运行组合。

不能通过解析 `candidate_name` 的括号和连字符恢复这些维度。Harness、Model 和一次
可运行组合必须成为结构化、可冻结的领域对象。

## 已确认边界

- AnalystBench 只选择传给 Harness 的模型名称。
- 模型 endpoint、API Key、temperature、max tokens 和其他运行配置由各 Harness
  工程在本地管理。
- 支持模型切换的 Harness 在命令行接收模型参数，例如：

  ```text
  claude -p "分析一个日志：{input}" --model glm5.1
  ```

- `claude-native`、`claude-skill` 和 `hmdiag-agent` 是独立 Harness；可以使用
  family 标签表达同属一个工程族，但 family 不作为运行身份。
- `script-only` 是不使用模型的正式基线，不创建虚假的 `none` 模型。
- 提交页面默认选择每个 Harness 下全部已启用且可用的模型组合。
- 定时计划固定所选运行组合；之后新增 Harness、Model 或组合不会静默扩大计划。
- 本阶段不改变评分算法、Judge 契约、Case 输入、隔离工作区和 P18 实际命令耗时
  定义。

## 目标

1. 将 Harness 工程和 Model 选择从现有 Evaluation Method 中拆开。
2. 用结构化 Evaluation Target 表达一个 Harness 与一个可空 Model 的可运行组合。
3. 一次 Submission 可以展开并冻结多个 Target，对每个 Case 分别生成报告和评分。
4. 结果可以按 Harness 或 Model 聚合质量、成功率和生成耗时。
5. 新流程兼容历史 Method、Submission 和只有 `candidate_name` 的直接文件结果。

## 非目标

- 不连接或管理模型供应商 API。
- 不保存、读取或展示模型凭据和 endpoint。
- 不统一不同 Harness 的 temperature、token 上限或其他本地模型参数。
- 不检测本地配置中的模型是否真实存在，也不为探测调用模型产生费用。
- 不把评分 Judge 作为被测 Model 维度；Judge 继续是独立且必须冻结的评分依赖。
- 不在 P19 引入跨机器执行集群、成本核算或 Token 用量排名。
- 不把多个并发 Target 的耗时相加作为批次墙钟时间。

## 术语

- **Harness**：读取 Case 日志并生成分析报告的工程和编排方式。
- **Harness Version**：Harness 的不可变命令、工程版本、Prompt/Skill 配置和执行限制。
- **Model**：供用户选择并传给 Harness 的逻辑模型身份。
- **Model Version**：不可变的模型显示信息和命令行参数值。
- **Evaluation Target**：一个 Harness Version 与一个可空 Model Version 的已验证
  可运行组合，同时承担 Harness–Model 兼容绑定职责。
- **Target Run**：一个 Target 对一个 Case 的实际子进程运行。
- **Target Key**：Target 的稳定机器标识，也是报告和评分候选的身份。

界面不再使用“测评方式”指代 Harness 与 Model 的组合。历史页面和 API 可以继续
显示“旧测评方式”，但新配置统一使用“Harness”“模型”和“运行组合”。

## 领域模型

### 用户配置最小化

设置页只暴露运行所需的最小字段：

- Harness：`key`、命令模板、超时时间、共享并发数。
- Model：一个模型名称；该值同时作为 key、显示名和传给 `{model}` 的 argv 参数。
- Harness 是否需要模型由命令模板是否包含 `{model}` 自动推断。

`name`、`family`、`tool_dir`、独立 `argument` 和输出上限继续作为后端兼容及
内部默认字段保留，不再要求用户在创建表单中配置。已有版本和旧 API 请求仍可读取和
执行这些字段。

### Harness Version

Harness 继续采用同 Key 单调递增版本。冻结版本至少包含：

```json
{
  "id": "uuid",
  "key": "claude-native",
  "name": "claude Native",
  "family": "claude",
  "version": 1,
  "model_policy": "required",
  "tool_dir": "/opt/claude-native",
  "command_template": "claude -p \"分析一个日志：{input}\" --model {model}",
  "timeout_seconds": 1800,
  "max_output_bytes": 10485760,
  "concurrency_limit": 2,
  "source_revision": {
    "kind": "git",
    "revision": "commit-sha",
    "dirty": false
  },
  "status": "frozen",
  "content_hash": "sha256:..."
}
```

`model_policy` 在 P19 只允许：

- `required`：命令模板必须包含一次 `{model}`，只能通过带 Model 的 Target 运行。
- `none`：命令模板不得包含 `{model}`，只能创建 Model 为空的 Target。

Harness Version 负责：

- 工程入口、Prompt、Skill/native 等 Harness 行为。
- `{input}`、`{input_dir}`、`{workspace}`、`{tool_dir}` 和 `{model}` 命令模板。
- 超时、最大输出和跨模型共享的 Harness 并发上限。
- 可获取时记录外部工程 Git commit 或制品 digest；dirty 状态必须进入运行清单。

冻结数据库配置不等于冻结外部 `tool_dir` 内容。正式运行必须重新采集实际
`source_revision`；与冻结值不同或工程为 dirty 时允许运行，但结果标记为非受控，
不得伪装成相同 Harness Version 的直接 A/B。

### Model Version

Model 是轻量选择目录，不管理本地运行配置：

```json
{
  "id": "uuid",
  "key": "glm-5.1",
  "name": "GLM 5.1",
  "version": 1,
  "argument": "glm5.1",
  "status": "frozen",
  "content_hash": "sha256:..."
}
```

规则：

- `key` 是 AnalystBench 内部稳定身份。
- `argument` 是默认传给 Harness `{model}` 的单个 argv 参数。
- Model 不保存 endpoint、凭据或生成参数。
- 修改 `argument` 创建新版本，不能原地改变已冻结版本。
- 同 Key 的历史版本继续可读，提交页面默认展示最新冻结且未归档版本。

### Evaluation Target

Evaluation Target 同时是兼容绑定和可运行组合：

```json
{
  "id": "uuid",
  "target_key": "claude-native@glm-5.1",
  "harness_version_id": "uuid",
  "model_version_id": "uuid",
  "model_argument": "glm5.1",
  "concurrency_limit": null,
  "status": "frozen",
  "content_hash": "sha256:...",
  "probe": {
    "available": true,
    "checked_at": "2026-07-29T12:00:00Z"
  }
}
```

规则：

- `required` Harness 的 `model_version_id` 必填；`none` Harness 必须为空。
- `model_argument` 默认继承 Model Version；只有同一模型在该 Harness 中使用不同
  CLI 名称时才覆盖，例如 `zhipu/glm-5.1`。
- 同一 Harness Version 与 Model Version 最多存在一个未归档 Target。
- `target_key` 使用 `<harness-key>@<model-key>`；无模型 Target 使用 Harness Key。
- Harness Key 和 Model Key 不允许包含 `@`，Target Key 必须是安全、跨平台的报告
  文件名 stem。
- Target 的可选 `concurrency_limit` 只限制该组合；为空时不增加组合级限制。
- Target 冻结后不可修改；改变模型参数映射或限制创建新 Target。

示例：

| Target Key | Harness | Model |
|---|---|---|
| `script-only` | `script-only` | `null` |
| `claude-skill@glm-5.1` | `claude-skill` | `glm-5.1` |
| `claude-native@glm-5.1` | `claude-native` | `glm-5.1` |
| `hmdiag-agent@deepseek-v4-flash` | `hmdiag-agent` | `deepseek-v4-flash` |

### Target Run

新提交中每个 Case × Target 创建一个 Target Run。至少保存：

- Submission、Case Run 和 Target ID。
- Harness Version ID、Model Version ID 和 Target 配置快照。
- 状态、attempt、审计产物和错误。
- P18 的 `started_at`、`finished_at`、`duration_ms`。
- 最终报告路径与内容哈希。

数据库物理迁移可以复用并扩展现有
`evaluation_submission_method_runs`，但应用层和新 API 必须使用 Target Run 术语。
历史行继续通过原 `method_id` 读取；新行引用 `target_id`，两者不能同时为空或同时
有值。

## 命令解析与执行

Harness 命令继续使用现有 `shlex.split(..., posix=True)` 和 `shell=False` 契约。
先把模板解析成 argv，再对每个 argv token 做安全占位符替换，不把结果交回 Shell。

模板：

```text
claude -p "分析一个日志：{input}" --model {model}
```

解析后的实际 argv：

```json
[
  "claude",
  "-p",
  "分析一个日志：/workspace/logs/log.txt",
  "--model",
  "glm5.1"
]
```

模板中的引号只负责参数分组，不作为字符传给 Harness。`{model}` 的值始终作为现有
argv token 的普通文本替换，不能引入额外参数、Shell 操作符或命令替换。

Target probe 只验证：

- Harness 可执行文件、`tool_dir` 和模板合法。
- Harness `model_policy` 与 Target 的 Model 是否一致。
- `model_argument` 非空且可安全替换为单个 argv token。
- Harness/Model/Target 均为可用状态。

probe 不执行真实 Case、不调用模型，也不读取 Harness 的本地 endpoint 或凭据。
模型名称不被本地 Harness 接受时，由实际 Target Run 返回稳定执行错误。

隔离工作区、只复制日志、禁止接触 `case.json` 和历史结果、stdout 作为最终报告等
规则继续沿用 P15。

## 提交与矩阵展开

提交页面先选择 Case，再按 Harness 展开 Target：

```text
[x] script-only

[x] claude-native
    [x] GLM 5.1
    [x] GLM 5.2

[x] claude-skill
    [x] GLM 5.1
    [x] GLM 5.2

[x] hmdiag-agent
    [x] DeepSeek V4
    [x] DeepSeek V4 Flash
```

默认勾选所选 Harness 下全部 `frozen`、未归档且 probe 成功的 Target。用户可以取消
任意模型组合，但不能选择未配置的任意 Harness × Model 笛卡尔积。

确认页必须显示：

```text
Case 数 × Target 数 = 生成任务数
```

并列出最终 Target Key，避免默认全选在无提示的情况下扩大运行时间和本地资源消耗。

新提交 API 接收 `target_ids`，不接收前端临时拼出的 `harness_id + model_id`。
服务端重新验证并在 Submission Manifest 中冻结：

- Harness、Model、Target 的 ID、版本和 content hash。
- Target Key、显示名称和最终 `model_argument`。
- Harness 命令模板、执行限制和 source revision。
- 请求 Target、实际纳入 Target 和被拒绝原因。
- Case、日志、Judge 和既有评分依赖快照。

报告文件使用 `<target_key>.md`。评分器使用 Target Key 作为稳定
`candidate_name`；界面可以用 Harness 名称和 Model 名称组合出更友好的
`display_name`，但不得用显示名称关联得分和耗时。

## 状态、并发与重试

Target Run 沿用 Method Run 状态机、取消、超时、产物收集和重试语义。Job 认领时
同时满足：

1. Worker 全局并发上限。
2. Harness Version 并发上限，统计该 Harness 跨所有 Model 的活动 Target Run。
3. Target 自身可选并发上限。

Model 并发和供应商限流不属于 P19；由 Harness 本地配置处理。未来确有跨 Harness
共享模型资源限流需求时，再增加独立 Model 资源门禁。

Harness 和 Target 限制必须在 JobQueue 的原子 claim 边界检查，不能依赖偶然的
Worker 数量。租约续期、owner 校验和终态写入继续复用现有机制。

重试使用原 Target 快照，不重新解析最新 Model 或最新 Harness 版本。改变 Harness、
Model、Target、Case 日志或评分契约必须创建新 Submission。

## 结果契约

`run.json` 和 `result.json` 的新生成结构使用 `generation.targets[]`：

```json
{
  "generation": {
    "targets": [
      {
        "target_key": "claude-native@glm-5.1",
        "display_name": "claude Native · GLM 5.1",
        "harness": {
          "key": "claude-native",
          "version": 1,
          "content_hash": "sha256:..."
        },
        "model": {
          "key": "glm-5.1",
          "version": 1,
          "argument": "glm5.1",
          "content_hash": "sha256:..."
        },
        "status": "succeeded",
        "attempt": 1,
        "started_at": "2026-07-29T12:00:00Z",
        "finished_at": "2026-07-29T12:01:20Z",
        "duration_ms": 80000
      }
    ]
  }
}
```

无模型基线的 `model` 为 `null`。评分 `summary.reports[].candidate_name` 与
`target_key` 一致，前端和 Markdown 通过 Target 元数据展示名称。

兼容规则：

- 旧 `generation.methods[]` 继续读取并投影为 legacy Target。
- 旧结果只有 `candidate_name` 时不得自动解析括号；显示为 legacy，允许用户通过
  独立映射元数据补充 Harness/Model 维度，但不改写原始结果文件。
- 现有 `result.json`、`result.md` 和旧报告文件继续可读。
- 新评分算法、分数、通过判定和排名不因结构化 Target 而改变。

## 对比与聚合

### 单次 Case

正式结果继续展示每个 Target 的：

- 分数与是否通过。
- 实际生成耗时。
- 生成状态、重试和审计产物。
- Harness 与 Model 结构化身份。

`script-only` 参加总榜并标记为“无模型基线”，但不进入“固定模型比较不同 Harness”
视图。

### 跨 Case 质量

界面统一称“质量”，避免把连续评分误写成准确率。每个 Target 至少展示：

- 平均得分。
- 通过率。
- 报告生成成功率。
- 实际评分 Case 数、请求 Case 数和覆盖率。

只统计成功报告的平均得分必须同时展示生成失败和覆盖率，不能让失败较多的 Target
因为幸存者偏差获得更好观感。

### 跨 Case 耗时

生成耗时至少展示：

- 中位数。
- P95。
- 有耗时样本数。
- 超时率和运行失败率。

聚合只使用 P18 的实际子进程 `duration_ms`，不混入排队、准备或评分时间。

### 两种透视

- Harness 视图：固定同一 Harness Version，比较不同 Model Version。
- Model 视图：固定同一 Model Version，比较不同 Harness Version。

两 Target 的直接 A/B 只使用共同成功且完成评分的 Case 做成对分差和耗时差，同时
单独列出各自缺失、失败和超时 Case。

只有 Dataset、Case、日志快照、评分规则、Judge、相关 Harness/Model 版本和执行
环境满足比较要求时才标记为受控。固定 Harness 比模型时 Harness Version 必须相同；
固定模型比 Harness 时 Model Version 必须相同。否则仍可展示，但必须列出差异并标记
为非受控比较。

## 定时测评

P16 的 `method_ids` 在新计划中替换为 Harness × Model 选择。保存计划时，后端自动
创建或复用精确的冻结 Target：

- 页面直接选择 Harness 与 Model，不提供手工“新建运行组合”步骤。
- 计划保存创建或编辑时，把选择固化为冻结 Target。
- 新增 Model 或 Harness 不自动进入已有计划。
- Target 被归档后，已有计划保留引用但后续触发预检失败，不能静默替换。
- 新计划默认选择当前全部可用 Harness × Model；保存后冻结明确列表。
- Schedule Run 配置快照和 Submission Manifest 保存相同 Target 结构。

历史计划继续按旧 Method ID 触发兼容流程，用户编辑后迁移为 Target。

## API 与界面

新增资源：

```text
GET/POST  /api/v1/evaluation-harnesses
GET       /api/v1/evaluation-harnesses/{id}
POST      /api/v1/evaluation-harnesses/{id}:probe
POST      /api/v1/evaluation-harnesses/{id}:freeze
POST      /api/v1/evaluation-harnesses/{id}:revise
POST      /api/v1/evaluation-harnesses/{id}:archive

GET/POST  /api/v1/evaluation-models
POST      /api/v1/evaluation-models/{id}:revise
POST      /api/v1/evaluation-models/{id}:archive

GET/POST  /api/v1/evaluation-targets
POST      /api/v1/evaluation-targets/{id}:probe
POST      /api/v1/evaluation-targets/{id}:freeze
POST      /api/v1/evaluation-targets/{id}:archive
```

新 Submission 和 Schedule 写接口使用 `target_selections` 接收 Harness × Model，
后端解析为内部 `target_ids` 快照。`target_ids` 和旧 `evaluation-methods` API
继续用于历史兼容，不再作为新页面主入口。

设置页只保留：

1. Harness：工程、版本、命令、模型策略和共享并发。
2. Model：显示名和命令行参数。

运行组合不是用户配置资源。提交和定时测评页面按 Harness 分组直接展示可选 Model，
Target 只作为后端不可变快照用于历史追溯、执行和对比。

结果页增加：

- 全部 Target 总览。
- 固定 Harness 比较模型。
- 固定 Model 比较 Harness。
- 平均得分、通过率、覆盖率、成功率、中位耗时和 P95。

## 版本、归档与删除

- Harness、Model 和 Target 冻结后不可原地修改。
- 被 Target、Submission、Schedule 或历史 Run 引用的对象不可物理删除，只能归档。
- 未冻结且未被引用的草稿允许删除。
- 归档不删除报告、结果目录或历史运行。
- 历史结果继续引用精确版本和快照，不自动跟随同 Key 新版本。

这套规则取代旧 Evaluation Method 删除时级联删除整个历史 Submission 的做法；P19
不应为了删除配置而销毁评测事实。

## 数据迁移

迁移分为兼容和切换两部分：

1. 新增 Harness、Model、Target 表以及 Target Run 引用。
2. 保留现有 Evaluation Method 和历史 Method Run 外键。
3. 每个旧 Method 可投影为一个 `legacy` 无结构化 Model 的只读 Target；不从 Key、
   name 或命令文本猜测模型。
4. 新提交只使用 Target，旧 Submission、Schedule 和结果继续走兼容读取。
5. 用户可以显式创建 Harness、Model 和 Target 替代旧 Method；系统不改写历史
   Manifest 和结果文件。
6. 等全部活跃计划迁移后再评估是否移除旧 Method 写接口，P19 不物理删除旧表。

本次四个直接文件结果可以通过显式映射补充索引：

```text
claude(glm5.1)-native       -> claude-native@glm-5.1
claude(glm5.1)-skill        -> claude-skill@glm-5.1
hmdiagAgent(deepseek-v4-flash) -> hmdiag-agent@deepseek-v4-flash
script-only                    -> script-only
```

映射是用户确认的元数据，不是通用文件名解析规则。

## 安全与审计

- `{model}` 只能产生一个普通 argv token，继续禁止 Shell。
- API、Manifest、日志和前端不读取或展示 Harness 本地模型凭据。
- Model 和 Target probe 不调用真实模型。
- Harness 继续只能看到自己的隔离日志副本，不能看到标准答案、其他 Target 报告或
  历史结果。
- 审计记录 Harness/Model/Target 版本、哈希、最终模型参数和外部工程 revision。
- 失败信息可以包含模型参数名，但不得回显 Harness 本地环境变量值。

## 验收条件

- 可以分别创建 `claude-native`、`claude-skill`、`hmdiag-agent` 和
  `script-only` Harness。
- 可以创建 GLM 5.1、GLM 5.2、DeepSeek V4 等 Model，测评时直接选择 Harness ×
  Model。
- `required` Harness 的实际 argv 精确包含所选模型参数；`script-only` 不包含模型。
- 提交默认选中全部可用 Harness × Model，并准确展示 Case × Target 任务数。
- 同一 Case 的报告、得分、状态和 P18 耗时按 Target Key 一一对应。
- 可以固定 Harness 比较不同 Model，也可以固定 Model 比较不同 Harness。
- 聚合同时展示质量、覆盖率、生成成功率、中位耗时、P95 和样本数。
- Harness 共享并发和 Target 并发在原子 Job claim 中生效。
- 定时计划固定 Target，不因新增 Model 自动扩大。
- 旧 Method、Submission、Schedule、`generation.methods[]` 和直接文件结果继续可读。
- 全量迁移、API、Worker、评分、前端、生产构建和兼容回归通过。

## 已确认决策

- AnalystBench 只向 Harness 传递模型名称，本地工程负责全部模型运行配置。
- Harness、Model 独立管理，通过 Evaluation Target 表达兼容绑定和运行组合。
- 用户不手工管理 Target；提交和计划保存时由后端自动创建或复用冻结 Target。
- 支持模型的 Harness 使用 `{model}`；无模型基线禁止使用 `{model}`。
- `claude-native` 与 `claude-skill` 是独立 Harness。
- 提交默认选择每个 Harness 下全部可用 Model。
- 结果按 Harness 和 Model 两个维度比较质量与实际生成耗时。
- 定时计划固定精确 Target，不自动跟随新增组合。
