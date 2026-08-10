# Benchmark 调研文档

本目录只放用于设计、校准和扩展 AnalystBench Benchmark 的调研材料，不放用户操作指南，也不放已确认的工程实现契约。

## 当前调研

### OpenRCA

- [完整调研报告](openrca/analystbench-openrca-report.md)
- [HTML 版本](openrca/analystbench-openrca-report.html)
- [两页汇报 PPT](openrca/analystbench-openrca-report.pptx)

结论：OpenRCA 保留为外部数据集和对照实现，不迁移其 Controller/Executor、轨迹保存或精确字符串评分；可验证的启发是把问题分类与责任组件目录建模为可冻结的评测信息条件。
