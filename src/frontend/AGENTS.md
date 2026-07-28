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
- 测评方式只要求填写一个 Key，不再单独填写显示名称；界面统一显示 Key，且 Key 支持 `codeAgent(glm5.1)-native` 这类可安全作为文件名的字符。
- 测评方式的垃圾桶表示删除：未被批次使用的记录物理删除；已有历史批次引用的记录只能在用户确认后停用，并从设置列表和提交选项中隐藏。
- 总览右侧“综合得分”图按日期展示各方案的得分趋势，并继续跟随测试集筛选。
