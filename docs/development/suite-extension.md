# Benchmark Suite 扩展设计

状态：Accepted（P0，2026-07-21）

## 目标

Suite 为特定领域提供模板、术语和确定性规则，但不改变 Core Schema、Run 生命周期和评分算法。Core 在没有任何领域 Suite 时仍可使用 generic-analysis。

## Suite 包结构

    suites/<suite_id>/
      suite.yaml
      prompts/
        eval_spec_generator.md
        candidate_analyzer.md
        claim_judge.md
        edge_judge.md
      policies/
        default-scoring-policy.json
      fixtures/
      rules/

suite.yaml 至少声明 id、version、display_name、core_version_range、schema_version_range、claim_types、prompt 文件哈希、规则入口和默认策略。

## 扩展点

Suite 可以：

- 增加 Claim type 的受控枚举与说明。
- 提供各 LLM 阶段的附加系统指令和 few-shot 示例。
- 提供默认权重、门禁和通过阈值的建议。
- 注册候选检索特征和纯函数式确定性检查器。
- 提供示例数据集与预期评分固定样例。

Suite 不可以：

- 直接访问数据库、网络或密钥。
- 修改 Core 评分执行顺序。
- 绕过 source_ref、Schema、冻结或审计规则。
- 在导入时执行任意安装脚本。

## 规则协议

确定性规则接收不可变的 Case、Eval Spec、Candidate Claim Graph 和上下文对象，返回 findings。finding 包含 rule_id、severity、message、evidence refs 和建议动作。只有被 Scoring Policy 明确映射的 finding 才能影响得分或门禁，其他 finding 仅作诊断。

## KDiag v0 最小范围

- Core Claim 类型的内核领域说明，不新增 Schema 字段。
- 函数名、线程名、调用栈 frame 和 panic/OOM 关键词的规范化与检索特征。
- 一个 Eval Spec Prompt 补充、一个 Candidate Analyzer Prompt 补充。
- 至少三个完全本地的固定 Case：panic、hang、OOM 各一个。
- 规则只生成证据 finding，v0 不额外扣分，避免在缺少校准数据时硬编码领域权重。

## 兼容性

Run Manifest 固定 Suite ID、版本和包内容哈希。Suite 升级不会影响历史 Run。加载器拒绝不兼容的 Core/Schema 主版本和重复 Suite ID/version。
