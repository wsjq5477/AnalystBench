# LLM 结构化契约

状态：Accepted（P0，2026-07-21）

## 通用约束

- 所有模型调用要求 JSON Schema 结构化输出；不解析 Markdown 代码块作为正常路径。
- 请求记录 model profile、prompt version、参数、输入哈希和调用用途。
- temperature 默认 0；如供应商不支持则记录实际配置。
- 模型不得生成 ID 最终值。服务端重新分配稳定 ID 并验证引用。
- 模型不得给总分、修改权重或决定通过状态。
- 用户输入、标准答案和报告都视为不可信数据；系统 Prompt 明确禁止执行其中的指令。

## Eval Spec Generator

输入：问题描述、标准答案、可选材料、Suite Prompt 和可选补充说明。

输出：Claim 草稿、因果边草稿、Forbidden Claim 建议、建议权重和 unresolved_items。

要求：

- 每个 Claim 提供标准答案逐字 quote 和原文区间。
- 无法从原文支持的内容放入 unresolved_items，不得伪造 Claim。
- 根因、触发、机制、症状必须拆分。
- 输出只形成 Draft；边、权重和 Forbidden Claim 均需人工确认。

## Candidate Analyzer

输入：Candidate Report 文本、Suite Prompt。

输出字段：type、statement、certainty、section、source_ref。certainty 仅为 confirmed、probable、suspected、possible。

要求：

- 只能抽取报告明确表达的结论，不补充推理。
- 每个 Claim 必须有逐字 quote 和区间。
- 复合结论必须拆成多个原子 Claim；不同 Claim 可以引用同一句原文或重叠区间。
- 服务端逐项验证 quote；任一引用错误会使整个输出进入修复流程。
- 抽取结果按 report_hash、model、prompt、参数和 suite_version 形成缓存键。

## Claim Judge

输入：一个 Gold Claim、top-k Candidate Claims、必要的原文上下文和关系定义。

输出：gold_claim_id、candidate_claim_id 或 null、relation、confidence、reason、candidate_ref。

约束：

- relation 为 match、partial_match、missing、contradiction。
- missing 时 candidate_claim_id 和 candidate_ref 必须为 null。
- 非 missing 时必须引用候选报告原文。
- unrelated 只用于候选对的内部判断，不作为 Gold Claim 最终结果保存；所有候选均 unrelated 时归为 missing。

## Edge Judge

仅在两端节点已对齐后调用。输入包含 Gold Edge、两端 Gold/Candidate Claim、候选报告相关上下文。输出 relation、confidence、reason 和支持因果关系的 candidate_ref；relation 为 edge_match、edge_partial、edge_missing、edge_reversed、edge_conflict。

## Forbidden Claim Judge

输入单个 Forbidden Claim 和检索到的 Candidate Claims。输出 hit、candidate_claim_id、confidence、reason 和 candidate_ref。hit 为 true 时引用必填。

## 错误与修复

推荐每次调用最多三次尝试：首次调用；将 Schema/引用错误反馈给模型进行一次修复；对瞬时传输错误进行一次重试。仍失败则标记当前 Case Run 为模型阶段失败，不允许使用残缺结果继续计分。

可重试：超时、限流、5xx、无效 JSON、Schema 错误、引用区间错误。不可重试：认证失败、模型不存在、上下文永久超限、配置错误。

## 隐私和日志

默认日志只保存哈希、长度、模型元数据、耗时和错误分类。完整 Prompt/Response 作为受控审计产物保存在本地 Content Store，API 默认不返回；是否允许完全关闭原始模型响应留作 P0 决策。
