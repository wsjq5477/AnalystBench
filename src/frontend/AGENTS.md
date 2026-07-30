# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## Durable product decisions

- The interface served from the WSL workspace on port 5173 is the product baseline.
- Preserve all four primary views: 总览、测试集、评测结果、设置.
- Technical-stack migration must not replace the result workflow with a generic benchmark-run form or remove local Case evaluation, result promotion/move/delete, or settings persistence.
- Case 详情内的基础信息、原始日志和 JSON 区域必须使用一致的卡片标题栏、内边距、圆角、边框和主题变量；新增详情区块沿用同一组件层级。
- Case 详情的日志上传使用单一入口；用户选择文件后立即上传，不再额外显示含义重复的提交按钮。
- 提交测评时若只有一个已冻结测评方式则默认选中；步骤校验失败必须显示原因，不能仅依赖无反馈的禁用按钮。
- 提交测评第一步允许逐个选择 Case，默认勾选全部日志就绪项；缺日志 Case 置灰并自动跳过，不能阻塞其他已选 Case。
- 提交测评生成的结果直接属于正式结果；批次结束后自动切换到正式结果并打开本批次结果，已结束批次保留“查看正式结果”入口。
- 测评方式只要求填写一个 Key，不再单独填写显示名称；界面统一显示 Key，且 Key 支持 `codeAgent(glm5.1)-native`、`agent(deepseek)` 这类可安全作为文件名的字符，包括以右括号结尾。
- 测评方式的垃圾桶表示彻底删除：用户确认后删除该版本、引用它的完整测评批次、正式结果目录和定时执行历史；若同一批次包含其他方式，该批次也整体删除。正在运行的任务必须先停止。
- Frozen 测评方式允许从界面修改；修改创建同 Key 的新草稿版本并重新检测，旧版本在新版本冻结前继续可用。
- 总览右侧“综合得分”图按日期使用折线图展示得分趋势，并继续跟随测试集筛选。
- 总览顶部提供共享对比维度筛选：按 Harness 时先选择一个 Model，再比较该 Model 下的各 Harness；按 Model 时先选择一个 Harness，再比较该 Harness 下的各 Model；无模型的 script 基线在两种模式中都保留。该筛选同步作用于综合得分、问题种类得分和日期得分三个区域，不影响下方完整组合排行榜。
- 总览统计展示平均运行时间：综合得分卡片直接显示，图表在悬停提示中显示，完整组合排行榜提供独立运行时间列。
- 总览内的运行耗时标签统一使用英文 `DURATION`；综合得分卡片使用 `AVG DURATION`。
- 耗时数值统一使用英文单位格式，例如 `2 min 22 sec`；时间折线图需为首尾数据点保留明显的左右留白。
- 总览及结果详情中的评分区描述统一使用英文：`Overall Score`、`Score by Issue Type`、`Score Over Time`、`Higher Is Better`。
- 总览“按问题种类对比”使用纵向排行榜：按 SCORE 降序，前三名显示奖牌；将 `harness@model` 拆为 HARNESS 与 MODEL，无模型项显示 `-`；兼容旧结果 `harness(model)-suffix`，括号内作为 MODEL，括号外前后部分拼成 HARNESS；SCORE 位于身份列之后并使用主文字色，右侧各问题种类分数使用弱化文字色。
