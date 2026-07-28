# Design QA — Case 详情卡片一致性

## Comparison target

- Source visual truth: `/mnt/c/Users/jiqi/AppData/Local/Temp/codex-clipboard-f1cafd52-ed84-4636-819d-d2a4dbcd3287.png`
- Source pixels: 1346 × 695, RGB PNG.
- Intended implementation: `http://127.0.0.1:5173/datasets`
- Implementation screenshot: unavailable.
- Intended viewport/state: desktop、暗色主题、测试集 Case 详情已选中。
- Density normalization: source为单张桌面截图；因实现截图不可用，未执行像素密度归一化。

## Findings

### Iteration 1

- [P1] Case 详情内部使用了三套视觉层级。
  - Location: 基础信息、原始日志、Case JSON。
  - Evidence: 源截图中基础信息格、日志卡和 JSON 区域的标题字号、内边距、背景、边框与圆角明显不同；“原始日志”标题尤其显著偏大。
  - Impact: 同级内容看起来像来自不同页面，削弱 Case 详情的信息层级。
  - Fix: 三块内容统一为 `.case-detail-card`，共用 12px 圆角、主题边框和背景；标题统一为 52px 高的 `.case-detail-section-head`，正文统一采用 16px 内边距。

- [P2] 文件选择控件与页面主题不一致。
  - Location: 原始日志上传区。
  - Evidence: 源截图显示浏览器原生灰色文件按钮，与右侧 AnalystBench 按钮风格不一致。
  - Impact: 上传区在统一卡片内仍显得突兀。
  - Fix: 使用现有主题变量统一文件框、选择按钮、hover、边框和圆角。

### Post-fix evidence

- Vue 开发服务器热更新编译成功。
- 生产构建成功，Sites 测试 4/4 通过。
- 无浏览器渲染截图：浏览器连接在 WSL 工作区因 `sandboxCwd is not a local file URI` 被阻断。

## Required fidelity surfaces

- Fonts and typography: 代码已统一详情卡标题为 14px/630，标签为 11px，内容沿用项目字体变量；缺少浏览器截图，无法确认实际字体光栅化和换行。
- Spacing and layout rhythm: 代码已统一 14px 卡片间距、52px 标题栏、16px 正文边距、12px 圆角，并增加 2 列和 1 列响应式布局；缺少渲染证据。
- Colors and visual tokens: 新样式全部使用现有 `var(--...)` 主题变量，未新增硬编码颜色；缺少暗色/亮色截图验证。
- Image quality and asset fidelity: 本目标没有照片、插图、Logo 或其他新增图片资产。
- Copy and content: 保留现有功能和文案，仅增加“基础信息”“Case JSON”“只读”层级标签。

## Full-view and focused comparison

- Source screenshot已打开并检查。
- Implementation full-view screenshot无法获取，因此不能完成同视口合成对比。
- 原始日志标题、元数据格和 JSON 卡片本应作为 focused regions 复核，但同样受浏览器连接阻断。

## Primary interactions and console

- 页面、API 健康接口和 Vue 热更新均可访问。
- 生产构建与 Sites 路由测试通过。
- 未能通过浏览器点击 Case、切换主题、上传日志或检查浏览器控制台。

## Open Questions

- 暗色和亮色主题下的最终视觉仍需一次浏览器截图确认。

## Implementation Checklist

- [x] 统一三块详情卡结构。
- [x] 统一标题栏、间距、边框、圆角和背景。
- [x] 统一文件选择控件。
- [x] 增加桌面、平板和移动端网格适配。
- [ ] 获取同视口浏览器截图并完成合成视觉对照。
- [ ] 点击验证日志上传、主题切换和滚动状态。

## Follow-up Polish

- 浏览器连接恢复后，复核窄屏下长 Case Key 的省略和 JSON 滚动条密度。

final result: blocked
