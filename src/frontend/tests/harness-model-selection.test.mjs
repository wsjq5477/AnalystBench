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
