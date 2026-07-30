import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("evaluation UI selects Harness and models without a Target management step", async () => {
  const app = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
  const options = await readFile(
    new URL("../src/app-options.js", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(app, /新建运行组合/);
  assert.match(app, /Harness × 模型（默认全选）/);
  assert.match(app, /@click="deleteEvaluationHarness\(harness\)"/);
  assert.match(app, /@click="deleteEvaluationModel\(model\)"/);
  assert.match(options, /target_selections:/);
  assert.match(options, /allEvaluationSelectionKeys/);
  assert.match(options, /archiveEvaluationHarness\(harness.id\)/);
  assert.match(options, /archiveEvaluationModel\(model.id\)/);
});

test("dashboard defaults both comparison constraints to Average", async () => {
  const app = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
  const options = await readFile(
    new URL("../src/app-options.js", import.meta.url),
    "utf8",
  );
  const styles = await readFile(
    new URL("../src/styles.css", import.meta.url),
    "utf8",
  );

  assert.match(app, /<option value="Average">Average<\/option>/);
  assert.match(options, /dashboardModelFilter: "Average"/);
  assert.match(options, /dashboardHarnessFilter: "Average"/);
  assert.match(options, /selectedFilter === "Average"/);
  assert.match(
    styles,
    /\.dark-theme \.test-set-selector \.comparison-value-selector select option/,
  );
  assert.match(styles, /background-color: var\(--bg-card-solid\)/);
  assert.match(styles, /color: var\(--text-primary\)/);
});

test("formal results are the default and expose a persistent statistics toggle", async () => {
  const app = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
  const options = await readFile(
    new URL("../src/app-options.js", import.meta.url),
    "utf8",
  );
  const api = await readFile(new URL("../src/api.js", import.meta.url), "utf8");
  const styles = await readFile(
    new URL("../src/styles.css", import.meta.url),
    "utf8",
  );

  assert.match(options, /resultSource: "formal"/);
  assert.match(options, /toggleDirectResultVisibility/);
  assert.match(app, /'is-hide-action': item\.included_in_statistics !== false/);
  assert.match(app, /<IconView :size="15" \/>/);
  assert.match(api, /setDirectResultVisibility/);
  assert.match(styles, /grid-template-columns: minmax\(380px, 32%\) minmax\(0, 1fr\)/);
  assert.match(styles, /\.result-tree-leaf-row > \.result-tree-leaf \{[\s\S]*?min-width: 0/);
});
