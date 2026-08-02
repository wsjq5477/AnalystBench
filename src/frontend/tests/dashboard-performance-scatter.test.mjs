import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { paretoFrontier } from "../src/pareto-frontier.js";

test("dashboard plots every timed Harness × Model result independently of comparison filters", async () => {
  const app = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
  const options = await readFile(
    new URL("../src/app-options.js", import.meta.url),
    "utf8",
  );
  const styles = await readFile(
    new URL("../src/styles.css", import.meta.url),
    "utf8",
  );

  assert.match(app, /Score vs\. Duration/);
  assert.match(app, /All Harness × Model · Script Score Baseline/);
  assert.match(app, /kind="scatter"/);
  assert.match(app, /:series="performanceScatterSeries"/);
  assert.match(app, /:reference-line="performanceScatterBaseline"/);
  assert.match(options, /performanceScatterSeries\(\) \{[\s\S]*?this\.activeCandidates\.forEach/);
  assert.match(options, /value: \[duration, score\]/);
  assert.match(options, /if \(target\.model === "-"\) return/);
  assert.match(options, /targetLabel: target\.model/);
  assert.match(options, /target\.harness\.toLowerCase\(\) === "script"/);
  assert.match(options, /performanceScatterBaseline\(\)/);
  assert.match(
    styles,
    /\.chart-grid \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/,
  );
  assert.match(
    styles,
    /@media \(max-width: 1100px\)[\s\S]*?\.chart-grid \{ grid-template-columns: 1fr; \}/,
  );
});

test("scatter chart highlights the fast high-score quadrant", async () => {
  const chart = await readFile(
    new URL("../src/components/ChartCanvas.vue", import.meta.url),
    "utf8",
  );

  assert.match(chart, /ScatterChart/);
  assert.match(chart, /MarkAreaComponent/);
  assert.match(chart, /MarkLineComponent/);
  assert.match(chart, /type: "log"/);
  assert.match(chart, /formatter: "FAST \+ HIGH SCORE"/);
  assert.match(chart, /rgba\(187, 247, 208, \.44\)/);
  assert.match(
    chart,
    /\{ xAxis: xAxisMin, yAxis: yAxisMid \},[\s\S]*?\{ xAxis: xAxisMid, yAxis: yAxisMax \}/,
  );
  assert.match(chart, /data: \[\{ yAxis: referenceScore \}\]/);
  assert.match(chart, /name: "Pareto line"/);
  assert.match(chart, /type: "dotted"/);
});

test("Pareto line connects only progressively better score-duration points", () => {
  assert.deepEqual(
    paretoFrontier([
      { value: [84_000, 72.33] },
      { value: [92_000, 63.5] },
      { value: [96_000, 84.5] },
      { value: [118_000, 82.5] },
      { value: [142_000, 91.33] },
      { value: [198_000, 87.5] },
    ]),
    [
      [84_000, 72.33],
      [96_000, 84.5],
      [142_000, 91.33],
    ],
  );
});
