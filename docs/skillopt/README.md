# Skill 自优化调研文档

本目录只放为 Skill 自优化方法、实验设计和调优方向开展的研究材料。已经确认、可直接指导 Agent 实现的工程规格放在 [`../development/`](../development/README.md)。

## 总体研究与实验方案

- [AnalystBench 面向内核日志分析的 Skill 自优化方法研究与实验方案](AnalystBench-Skill自优化研究与实验方案.md)
- 配套工程规格：[AnalystBench Skill 自优化系统方案设计](../development/AnalystBench-Skill自优化系统方案设计-Codex.md)

## 外部项目调研

### SkillLearnBench

- [完整调研报告](skilllearnbench/skilllearnbench-comparison.md)
- [HTML 版本](skilllearnbench/skilllearnbench-comparison.html)

该调研面向 Skill 自优化闭环，重点分析 Skill Coverage、teacher-feedback、Skill 生成与评估解耦等可借鉴方法，因此归入 `skillopt`，不归入 Benchmark 工程规格。
