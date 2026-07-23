# Benchmark Suite 设计

AnalystBench Core 负责通用的自然语言标准答案结构化、Claim 对齐、因果关系评分和版本对比。

具体领域通过 Benchmark Suite 扩展。

## 第一阶段官方 Suite

### KDiag Suite

面向内核、驱动和操作系统问题分析，提供：

- 内核问题 Eval Spec 模板
- trigger、symptom、localization、root cause、mechanism 等推荐 Claim 类型
- 函数名、线程、调用栈和日志证据检查
- 内核问题评分策略示例
- 示例数据集

## 后续可扩展 Suite

- Incident RCA Suite
- Code Analysis Suite
- Security Analysis Suite
- Quality Analysis Suite
- Custom Suite

Suite 不改变 AnalystBench Core，只提供领域模板、规则和示例数据集。
