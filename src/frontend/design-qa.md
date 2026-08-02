# Design QA — Score vs. Duration scatter plot

## Comparison target

- Source visual truth: `/mnt/c/Users/jiqi/AppData/Local/Temp/codex-clipboard-cf34ca92-5f31-4814-b4b0-b7fdca02f7c5.png`.
- Source pixels: 1447 × 745 PNG.
- Intended implementation: `http://localhost:5173/dashboard`.
- Implementation screenshot: unavailable because the Codex in-app browser rejected the WSL workspace mapping with `sandboxCwd is not a local file URI: file:///home/jiqi/LLM/AnalystBench`.
- Intended viewport/state: desktop dashboard, dark theme, `demo-benchmark`, Harness/Model filters at `Average`.
- CSS viewport and device density: unavailable; no browser-rendered capture was produced.
- Density normalization: not performed because the implementation screenshot is unavailable.

## Findings

- [P1] Browser-rendered visual comparison is blocked.
  - Location: dashboard `Score vs. Duration` panel.
  - Evidence: the source reference opened successfully, and the WSL frontend/API both respond successfully, but the required browser connection fails before a screenshot can be captured.
  - Impact: label overlap, exact quadrant coverage, pale-green contrast, Script-line placement, hover state, and light-theme rendering cannot be accepted visually.
  - Fix: repeat this QA pass when the Codex in-app browser can map the WSL workspace, capture the dashboard at the intended viewport, and compare it with the source in one combined image input.

## Comparison history

### Iteration 1

- Earlier implementation represented Script as a tenth scatter point.
- User correction: Script must not be a point; its score must appear as a horizontal dashed reference line.
- Fix applied: Harness × Model combinations remain as nine points; Script is resolved explicitly by name and rendered as a `markLine` at its average score.
- Post-fix non-visual evidence: `demo-benchmark` returns nine timed Harness × Model combinations and `script` score `48.5`; frontend tests and production build pass.

### Iteration 2

- Code-level review found that full `Harness × Model` point labels would be denser than the reference, which groups colors by provider and labels points by model.
- Fix applied: legend colors group by Harness, point labels show Model only, and the full combination remains available in the tooltip. The horizontal axis title was corrected to `AVERAGE DURATION (LOG SCALE)` without a misleading direction arrow.
- Post-fix non-visual evidence: hot-update compilation and production build pass.

### Iteration 3

- User feedback: the two overview charts had unequal widths at full-screen size.
- Fix applied: the dashboard chart grid now uses two equal `minmax(0, 1fr)` columns while retaining the existing single-column layout below 1100px.
- Post-fix non-visual evidence: frontend regression test checks both equal desktop columns and the responsive single-column override.

### Iteration 4

- User feedback: the reference chart includes a dotted line connecting the efficient points along the upper-left frontier.
- Fix applied: the chart now computes the duration-minimizing, score-maximizing Pareto frontier and renders it as a dotted line below the scatter markers; dominated combinations are excluded from the line.
- Post-fix non-visual evidence: a deterministic unit test verifies that only progressively higher-scoring points remain when sorted by duration.

## Required fidelity surfaces

- Fonts and typography: uses the existing Inter chart font, 10–12px chart hierarchy, and existing theme text colors; browser rasterization, truncation, and overlap remain unverified.
- Spacing and layout rhythm: both overview charts use equal-width columns and stack below 1100px; exact rendered padding and label clearance remain unverified.
- Colors and visual tokens: Harness groups use the existing theme palettes; the upper-left quadrant uses pale green (`rgba(187, 247, 208, .11)` in dark and `.44` in light); rendered contrast remains unverified.
- Image quality and asset fidelity: no raster or custom image assets are introduced; the visualization uses ECharts canvas rendering and existing application typography.
- Copy and content: `Score vs. Duration`, nine-combination count, log-duration axis, average-score axis, `FAST + HIGH SCORE`, and Script score label are implemented.

## Full-view and focused comparison

- Source full view: opened and inspected.
- Implementation full view: unavailable; therefore no same-viewport combined comparison exists.
- Focused chart comparison: unavailable for the same blocker. The required focused checks are point-label collisions, the exact upper-left quarter fill, Script dashed-line label, and tooltip content.

## Primary interactions and console

- WSL frontend is listening on `0.0.0.0:5173`; WSL API is listening on `0.0.0.0:8000`.
- Frontend proxy request to `/api/v1/health/ready` returns `{"status":"ok","database":"ready"}`.
- The in-app browser could not be initialized, so hover behavior, theme switching, filter independence, and browser console errors were not inspected.

## Implementation checklist

- [x] Replace the issue-type bar chart with a duration × score scatter plot.
- [x] Use all nine Harness × Model combinations independently of Harness/Model comparison filters.
- [x] Keep the selected test-set filter applicable.
- [x] Group colors by Harness and label points by Model.
- [x] Render Script as a horizontal dashed score baseline rather than a point.
- [x] Shade the upper-left quarter with very pale green.
- [x] Connect non-dominated score-duration points with a dotted Pareto line.
- [x] Add duration/score tooltip details and responsive chart sizing.
- [x] Pass all frontend tests and the WSL production build.
- [ ] Capture dark/light browser screenshots and complete the combined visual comparison.
- [ ] Verify hover, theme switching, filter independence, and browser console state.

## Follow-up polish

- After browser capture is available, adjust label positions only if the nine model labels visibly collide at the real dashboard width.

final result: blocked
