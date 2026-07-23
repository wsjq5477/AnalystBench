# Benchmark Run 生命周期

状态：Accepted（P0.1 Agent Execution 范围修订，2026-07-21）

Candidate Generation Run 与 Agent Case Run 的执行状态见 agent-runner-design.md。两类 Run 相互独立：Agent 成功产出并冻结 Candidate Report 后，Benchmark Run 才能引用它。

## 创建前预检

创建 Run 时必须验证 Dataset Version 已冻结、每个 Case 有匹配的冻结 Eval Spec、Candidate 覆盖策略满足、Scoring Policy 可用、模型配置可解析、Suite/Core/Schema 版本兼容。Benchmark Run 不隐式启动 Agent；缺少报告时按 strict/partial 规则处理。

预检成功后一次性写入不可变 Run Manifest，并为每个 Case 建立 Case Run。Run 作为数据库持久化后台任务由 Local Worker 异步领取；API 创建 Run 后返回 202，不在请求进程中执行评测。

## Run 状态

    queued -> running -> completed
                       -> completed_with_errors
                       -> failed
                       -> cancelled

- queued：Manifest 已冻结，等待 Worker。
- running：至少一个 Case 已领取且仍有未终结 Case。
- completed：所有非 skipped Case 成功。
- completed_with_errors：至少一个成功，同时存在 failed 或 skipped Case。
- failed：没有可用的成功 Case，或发生 Run 级配置/一致性错误。
- cancelled：取消请求后不再启动新 Case，且没有成功 Case；若已有成功结果则使用 completed_with_errors。

## Case Run 状态

    pending -> extracting -> aligning -> scoring -> succeeded
        |          |           |          |
        +----------+-----------+----------+-> failed
    pending -> skipped

每次阶段转换记录 started_at、finished_at、attempt、错误分类和输入/输出产物哈希。

## 缓存

- Candidate Claim 缓存键包含 report_hash、extractor model、prompt、参数和 suite version。
- 对齐缓存键包含 Eval Spec hash、Candidate Claim Graph hash、Judge model、prompt、参数和 suite version。
- 确定性评分通常不缓存，直接由已冻结对齐产物快速重算。
- 缓存命中仍在 Case Run 中记录引用的产物 ID，不复制和静默复用未知来源数据。

## 重试和幂等

- 模型调用按 llm-contracts.md 的错误分类重试。
- Worker 使用 job_id + attempt 的提交令牌，过期 Worker 不能覆盖新 attempt。
- retry-failed 只为失败 Case 创建新 attempt，成功结果保持不变。
- 如果重试改变模型、Prompt、参数、Spec 或策略，必须创建新 Run，不能修改原 Manifest。

## 取消

取消是协作式的：停止领取新 Case；正在进行的模型请求完成或超时后不再进入下一阶段。已写入的成功 Case 结果保留。MVP 不保证强制终止第三方模型请求。

## 汇总

Run 汇总只基于 succeeded Case，同时必须展示 total、succeeded、failed、skipped 和 coverage_rate。默认不把 failed/skipped 当作零分混入平均数，以免模型服务故障伪装为质量退化；对比时覆盖集合不一致必须显著标记。

## A/B 对比

- 先依据 Manifest 判断 direct 或 uncontrolled。
- direct 对比默认使用两个 Run 都 succeeded 的 Case 交集，并同时报告各自缺失 Case。
- 输出平均分变化、通过率变化、各维度变化、提升/退化 Case、新增/消失冲突和根因命中变化。
- 提升/退化阈值推荐为 total_score 绝对变化至少 5 分，阈值属于对比配置而非评分策略。
