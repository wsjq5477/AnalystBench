# Design QA — AnalystBench dashboard

## Comparison target

- Source visual truth: `design-reference.png`
- Source pixels: 1672 × 941.
- Implementation screenshot: `src/frontend/dashboard-implementation.png`
- Implementation pixels / browser viewport: 1920 × 1080 CSS px at device scale factor 1.
- Normalization: both renders were bicubically scaled to 960 × 540 in `src/frontend/dashboard-comparison.png`; this is the full-view comparison evidence.
- State: dashboard, dark theme, populated benchmark example data, desktop 1K layout.

## Findings

### Iteration 1

- [P1] Score typography was obscured by solid fills.
  - Location: summary score cells and the three dimension metrics.
  - Evidence: the first browser capture showed `88.4`, `79.2`, and the metric values covered by the progress-fill background; the source uses exposed numeric typography with only a thin progress bar or sparkline.
  - Fix: separated `.score-item` and `.metric-panel` numeric text colour from the fill selectors, leaving only the progress element with the scheme colour.
  - Post-fix evidence: `dashboard-implementation.png` and the lower-right quadrant of `dashboard-comparison.png` show exposed gold/blue/gray values and thin chart lines.

## Required fidelity surfaces

- Fonts and typography: Chinese UI uses PingFang SC / Microsoft YaHei fallbacks, with Inter first for Latin text and JetBrains Mono-style numeric fallbacks. Header, metric, chart, and dense matrix hierarchy align with the reference. No clipping or unintended wrapping was visible at 1920 × 1080.
- Spacing and layout rhythm: 200px sidebar, 94px header, score/metric first row, two chart panels, and full-width matrix preserve the reference hierarchy. The implementation intentionally uses the 1K layout at the 1920px target width; the matrix remains horizontally scroll-safe below that width.
- Colors and visual tokens: base black, raised charcoal surfaces, 1px dark dividers, Agent gold, Skill blue-gray, Native gray, and restrained green connection status are present. No rainbow palette, large gradient, or 3D treatment is used.
- Image quality and asset fidelity: the source contains no photo, illustration, or product-image asset. The brand mark and interface controls use the Tabler icon library rather than custom SVG/CSS approximations. ECharts renders the charts as canvas-based data visuals.
- Copy and content: Chinese labels, benchmark/version/date controls, three engineering approaches, trend chart, category bar chart, and Case matrix all match the visual target’s information hierarchy. The additional API connection state is intentional and only appears in the sidebar footer.

## Focused-region comparison

The score/metric row and Case matrix were inspected in the shared composite. A separate crop was not necessary: all critical type, spacing, and colour surfaces are legible at the normalized scale. The first-row score issue found in iteration 1 was corrected and rechecked.

## Interaction and API verification

- In-app browser rendered the dashboard at 1920 × 1080.
- Sidebar navigation to `测试集` and `评测分析` was exercised successfully.
- Empty Benchmark Run submission displays the expected validation toast without sending a request.
- `GET /api/v1/datasets` returned 200 through the Vite proxy; the browser shows `API 已连接`.
- Browser console errors: none.

## Follow-up polish

- [P3] If a product logo asset is later supplied, replace the generic icon-library brand mark with that exact asset.
- [P3] Add a backend list/tree endpoint for Cases and Benchmark Runs to remove the need for entering Run IDs manually.

final result: passed
