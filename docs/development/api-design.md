# REST API 设计草案

状态：Accepted（P0.1 Agent Execution 范围修订，2026-07-21）

## 约定

- 基础路径 /api/v1，JSON 使用 snake_case，时间为 UTC RFC 3339。
- 创建接口支持 Idempotency-Key；分页采用 cursor + limit。
- 更新可变资源使用 PATCH，并通过 ETag/If-Match 防止覆盖并发修改。
- 冻结资源不提供 PATCH；修订通过创建新 Draft 完成。
- Eval Spec 生成和 Benchmark 等长任务必须进入持久化后台队列，返回 202 和 job/run 资源地址；API 请求不等待模型调用完成。

## Dataset 与 Case

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | /datasets | 创建 Dataset |
| GET | /datasets | 列表 |
| GET/PATCH | /datasets/{id} | 查看或修改元数据 |
| POST | /datasets/{id}/cases | 创建 Case Revision |
| GET | /cases/{id} | 查看 Case 与修订历史 |
| POST | /cases/{id}/revisions | 创建新修订 |
| POST | /datasets/{id}/versions | 冻结 Dataset Version |
| GET | /dataset-versions/{id} | 查看快照 |

## Candidate

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | /candidates | 创建 Candidate |
| POST | /candidates/{id}/versions | 创建 Candidate Version |
| POST | /candidate-versions/{id}/reports:batch-import | 批量导入报告 |
| GET | /candidate-versions/{id}/coverage | 查看 Case 覆盖情况 |

## Agent Execution

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | /execution-profiles | 创建 claude/OpenCode Profile 草稿 |
| POST | /execution-profiles/{id}:validate | 检测 CLI、版本与非敏感配置 |
| POST | /execution-profiles/{id}:freeze | 冻结 Profile 版本 |
| POST | /candidate-generation-runs | 创建后台批量生成任务 |
| GET | /candidate-generation-runs/{id} | 查看状态、覆盖率与汇总 |
| POST | /candidate-generation-runs/{id}:cancel | 协作式取消未完成执行 |
| POST | /candidate-generation-runs/{id}:retry-failed | 按策略重试可重试失败 |
| GET | /candidate-generation-runs/{id}/case-runs | Agent Case Run 列表 |
| GET | /agent-case-runs/{id} | 执行状态与非敏感元数据 |
| GET | /agent-case-runs/{id}/artifacts | 列出报告、事件和 stderr 产物 |

## Eval Spec

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | /case-revisions/{id}/eval-specs:generate | 异步生成草稿 |
| POST | /case-revisions/{id}/eval-specs | 手工创建草稿 |
| GET/PATCH | /eval-spec-drafts/{id} | 查看或修改草稿 |
| POST | /eval-spec-drafts/{id}:validate | 执行完整校验 |
| POST | /eval-spec-drafts/{id}:freeze | 冻结版本 |
| GET | /eval-spec-versions/{id} | 查看冻结版本 |

## Benchmark 与结果

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | /benchmark-runs | 创建并排队 Run |
| GET | /benchmark-runs/{id} | 状态与汇总 |
| POST | /benchmark-runs/{id}:cancel | 请求取消未开始的 Case |
| POST | /benchmark-runs/{id}:retry-failed | 重试失败 Case |
| GET | /benchmark-runs/{id}/case-runs | Case Run 列表 |
| GET | /case-runs/{id}/result | 可解释评分结果 |
| POST | /comparisons | 创建两个 Run 的对比 |
| GET | /comparisons/{id} | 对比结果与可比较性说明 |

## 配置与 Suite

- GET /suites、GET /suites/{id}/versions/{version}
- POST/GET /model-profiles；密钥仅通过 secret_ref 引用，不通过 API 回显。
- POST/GET /scoring-policies；冻结策略产生新版本。
- GET /jobs/{id} 获取 Eval Spec 生成等通用异步任务状态。

## 错误模型

    {
      "error": {
        "code": "eval_spec_invalid",
        "message": "Eval Spec cannot be frozen",
        "details": [{"path": "/claims/1/source_ref", "reason": "quote_mismatch"}],
        "request_id": "uuid",
        "retryable": false
      }
    }

稳定错误码至少覆盖 validation_failed、conflict、not_found、immutable_resource、coverage_incomplete、run_not_comparable、model_auth_failed、model_rate_limited、model_output_invalid、runner_unavailable、agent_authentication_required、agent_timeout、agent_exit_nonzero、agent_output_invalid 和 internal_error。

## CLI 对应关系

CLI 是 API/Application Service 的薄封装，至少提供 dataset import/freeze、candidate import、agent profile/probe/run/status、eval-spec generate/validate/freeze、benchmark run/status/export、compare 和 suite list。机器输出支持 --format json；所有命令用非零退出码表示失败。
