# P16 定时测评设计

状态：Implemented（2026-07-28）

## 目标

在 AnalystBench 内部增加可持久化的定时测评能力。用户在“评测结果”页面配置每日执行时间、测试集、Case 范围、测评方式和 Judge 后，Local Worker 到期自动创建普通 P15 测评批次。

定时触发只负责创建批次，不建立第二套报告生成或评分流程。生成、隔离、并发、重试、评分和正式结果目录继续完全复用 P15：

```text
定时计划到期
  -> 创建 Evaluation Submission
  -> Method Run 生成报告
  -> Case Run 自动评分
  -> results/<test_set>/<category>/<case>/runs/<timestamp>/
```

## 非目标

- P16 MVP 不实现任意 Cron 表达式、按周或按月日历规则，只支持“每天固定本地时间”。
- 不依赖 Linux Cron、Windows Task Scheduler、Codex 自动化或外部调度平台。
- 不在 API 或前端进程中执行用户命令；命令仍只由 Local Worker 执行。
- 不为定时批次建立新的结果格式、评分算法或重试语义。
- 不自动切换到测评方式的新版本，不自动补日志，也不绕过 P15 预检。

## 术语

- **定时计划（Evaluation Schedule）**：用户维护的每日执行配置。
- **计划执行（Evaluation Schedule Run）**：计划在某个时间点的一次触发记录。
- **计划时间（Scheduled For）**：本次执行原本应触发的时刻。
- **立即运行（Run Now）**：用户手工触发计划，但不改变下次正常执行时间。
- **补跑（Catch-up）**：Worker 恢复后，为最近一次错过的计划时间触发一次执行。

## 用户配置

每个计划包含：

| 字段 | 规则 |
|---|---|
| 名称 | 用户可读名称，必填，同一名称不要求唯一 |
| 测试集 | 一个现有本地测试集 |
| Case 模式 | `all_ready` 或 `selected` |
| Case 列表 | `selected` 模式必填，保存完整 `test_set/category/case` 路径 |
| 测评方式 | 一个或多个已冻结的 Evaluation Method ID |
| Judge | `claude` 或 `opencode` |
| 时区 | IANA 时区，默认 `Asia/Shanghai` |
| 每日时间 | `HH:mm`，按计划时区解释 |
| 启用状态 | 新建后默认启用 |

### Case 选择

`all_ready` 模式在每次触发时动态枚举测试集：

- 自动纳入当时所有 `submission_ready=true` 的 Case。
- 新增且日志就绪的 Case 会自动进入后续执行。
- 缺日志、主日志无效、Case JSON 无效或路径不安全的 Case 自动跳过。

`selected` 模式保存用户明确选择的 Case：

- 每次触发重新检查这些 Case。
- 日志未就绪的选中 Case自动跳过，不阻塞其他有效 Case。
- 不自动纳入计划创建后新增的 Case。

两种模式过滤后都没有可测 Case 时，不创建 Evaluation Submission；计划执行记录为 `skipped_no_cases` 并保存预检原因。

### 测评方式版本

计划保存具体的冻结 Method ID，而不是只保存 Key：

- 每次运行使用创建或编辑计划时选定的确切版本。
- 新建并冻结同 Key 的新版本后，计划不会静默切换。
- 用户必须编辑计划才能采用新版本。
- 方式被停用或不存在时，本次计划执行预检失败，不自动替换为其他版本。

计划执行在触发时保存完整配置快照，历史记录不受后续计划编辑影响。

## 时间与触发语义

### 每日计划

- MVP 只支持每天一次固定本地时间。
- 数据库存储 IANA 时区、`HH:mm` 和 UTC `next_run_at`。
- 所有比较使用 UTC；前端按计划时区展示下次执行时间。
- 每次成功认领到期计划后，立即计算并保存下一个未来触发时间。

### 错过计划

API、Worker、机器关机或休眠时不会执行命令。Worker 恢复后：

- 若计划已经过期，只补跑“最近一次”错过的计划时间。
- 不为连续错过的多天创建多个历史批次。
- 补跑成功认领后，`next_run_at` 直接推进到下一个未来时间。

例如计划每天 23:00，机器停机三天后在 09:00 恢复，只补跑最近一个 23:00，不补三次。

### 重叠处理

同一个计划存在未终结的 Evaluation Submission 时：

- 不创建第二个批次。
- 本次记录为 `skipped_overlap`，并链接仍在运行的批次。
- 下次正常计划时间不受影响。

不同计划可以同时到期，继续受现有 JobQueue、Worker 和测评方式并发上限约束。

### 立即运行

- `Run Now` 使用计划当前配置创建一次计划执行。
- 不修改 `next_run_at`。
- 同样执行 Case、Method 和重叠预检。
- 连续点击通过幂等键和运行中检查避免重复批次。

## 持久化模型

### `evaluation_schedules`

至少包含：

- `id`
- `name`
- `dataset_key`
- `case_mode`
- `case_paths_json`
- `method_ids_json`
- `judge_runner`
- `timezone`
- `local_time`
- `enabled`
- `next_run_at`
- `last_triggered_at`
- `created_at`
- `updated_at`

### `evaluation_schedule_runs`

至少包含：

- `id`
- `schedule_id`
- `trigger_key`，全局唯一，用于到期触发幂等
- `trigger_type`：`scheduled`、`catch_up` 或 `manual`
- `scheduled_for`
- `config_snapshot_json`
- `status`
- `submission_id`，成功创建批次后填写
- `error_json`
- `created_at`
- `updated_at`

计划执行状态：

```text
queued
  -> submitted
      -> completed
      -> completed_with_errors
      -> failed
      -> cancelled
  -> skipped_no_cases
  -> skipped_overlap
  -> failed_preflight
```

计划执行一旦产生历史记录，计划配置的删除操作改为“停用并隐藏”；没有任何执行记录的计划可以物理删除。界面必须明确区分“删除”和“停用”。

## Worker 调度

Local Worker 每轮空闲轮询时先执行一次轻量到期扫描：

1. 查询 `enabled=true` 且 `next_run_at <= now` 的计划。
2. 在数据库事务中创建唯一 `trigger_key` 的 Schedule Run，并推进 `next_run_at`。
3. 原子写入 `evaluation_schedule_trigger` Job。
4. JobQueue 按现有租约机制认领触发 Job。
5. 触发 Job完成预检并调用 P15 `create_submission`。
6. 保存 `submission_id`，后续状态从关联批次同步。

约束：

- 多个 Worker 同时运行时，同一计划时间只能成功创建一个 `trigger_key`。
- Worker 在认领后崩溃时，沿用 JobQueue 过期租约恢复，不生成第二个批次。
- 计划触发失败只影响该 Schedule Run，不终止 Worker。
- 到期扫描不得执行文件复制、命令或评分等重任务。

## P15 集成

定时批次调用与手工提交相同的服务入口，并增加来源信息：

- Evaluation Submission 增加可空的 `schedule_run_id`。
- 手工提交的 `schedule_run_id` 为 `null`。
- 定时提交使用触发时解析出的 `case_paths` 和固定 Method ID。
- P15 在创建 Submission 时重新执行全部安全预检。
- Submission Manifest 继续冻结 Case、日志和 Method 配置。
- 计划执行终态与关联 Submission 终态保持一致。

定时触发不得直接写 `runs/`，只有 P15 创建批次成功后才能生成运行目录。

## API

### 计划管理

- `GET /api/v1/evaluation-schedules`
- `POST /api/v1/evaluation-schedules`
- `GET /api/v1/evaluation-schedules/{id}`
- `PUT /api/v1/evaluation-schedules/{id}`
- `DELETE /api/v1/evaluation-schedules/{id}`
- `POST /api/v1/evaluation-schedules/{id}:enable`
- `POST /api/v1/evaluation-schedules/{id}:disable`
- `POST /api/v1/evaluation-schedules/{id}:run-now`

### 执行记录

- `GET /api/v1/evaluation-schedules/{id}/runs`
- `GET /api/v1/evaluation-schedule-runs/{id}`

API 返回：

- 计划时区下的每日时间。
- UTC 与格式化后的下次执行时间。
- 最近一次 Schedule Run、关联 Submission 和终态。
- 当前是否存在运行中批次。
- 预检失败、跳过和重叠原因。

## 前端

### 入口

“评测结果”页标题区在“提交测评”旁增加“定时测评”。

### 计划列表

展示：

- 名称和启用状态。
- 测试集、Case 模式和测评方式 Key。
- 每日时间及时区。
- 下次执行时间。
- 最近一次执行状态和关联批次。

操作：

- 新建。
- 编辑。
- 启用或停用。
- 立即运行。
- 查看执行记录。
- 删除未执行过的计划。
- 停用并隐藏已有历史记录的计划。

### 新建与编辑

沿用手工提交的测试集、Case 和测评方式选择组件：

- 默认 Case 模式为“全部日志就绪的 Case”。
- 固定选择模式下，缺日志 Case 置灰。
- 只展示已冻结且未停用的测评方式。
- 保存前展示下一次执行时间预览。
- 表单失败必须显示具体字段原因。

定时批次创建后，普通“测评批次”列表立即显示该批次，并标记来源计划。

## 错误与可观测性

稳定错误至少包含：

- `evaluation_schedule_not_found`
- `evaluation_schedule_invalid`
- `evaluation_schedule_method_unavailable`
- `evaluation_schedule_no_ready_cases`
- `evaluation_schedule_overlap`
- `evaluation_schedule_trigger_conflict`

Schedule Run 的 `error_json` 保存稳定错误码、中文消息和相关 Case 或 Method；不保存凭据、环境变量或命令输出。

Worker 日志记录：

- `schedule_due_claimed`
- `schedule_submission_created`
- `schedule_skipped`
- `schedule_trigger_failed`

## 测试

至少覆盖：

- 每日时间与 `Asia/Shanghai` 的下一次执行计算。
- Worker 停机后只补跑最近一次。
- 多 Worker 并发扫描不重复触发。
- 上一批次未结束时记录 `skipped_overlap`。
- `all_ready` 动态纳入新 Case 并跳过缺日志 Case。
- `selected` 只运行明确选择且当前可测的 Case。
- Method 被停用或删除时预检失败，不自动换版本。
- `Run Now` 不改变下一次正常执行时间。
- Schedule Run 与 Submission 状态和来源关联正确。
- API/Worker 重启后触发 Job 可恢复。
- 前端创建、编辑、启停、立即运行和历史查看。
- 生产构建和 Sites Worker 测试。

## 验收条件

- 用户无需操作系统定时任务即可创建每天执行的测评计划。
- 到期后自动产生普通 P15 批次和正式结果。
- 同一计划时间在多 Worker 下最多产生一个批次。
- 停机多天恢复后最多补跑一次。
- 同一计划的运行中批次不会与下一次触发重叠。
- 动态和固定 Case 模式均遵守缺日志自动跳过规则。
- 历史执行可追溯到计划配置快照、Submission、报告和评分结果。
- 定时执行不改变 P15 的隔离、安全、并发和评分契约。

## 待确认决策

- P16 MVP 仅支持“每天固定时间”，不开放 Cron 表达式。
- 默认时区为 `Asia/Shanghai`，每个计划可以单独修改。
- 默认 Case 模式为动态 `all_ready`。
- Method 固定到选定版本，不自动跟随同 Key 新版本。
- 同计划重叠时跳过本次，不排队等待。
- 停机恢复后只补跑最近一次，不补多天。
- `Run Now` 不改变下次正常执行时间。
