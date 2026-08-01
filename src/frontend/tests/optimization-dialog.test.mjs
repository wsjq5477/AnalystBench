import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("Skill optimization dialog keeps drafts until cancel and derives Skill identity", async () => {
  const app = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
  const options = await readFile(
    new URL("../src/app-options.js", import.meta.url),
    "utf8",
  );

  const overlay = app.match(
    /<div v-if="showOptimizationDialog" class="dialog-overlay"[^>]*>/,
  )?.[0];
  assert.ok(overlay, "optimization dialog overlay should exist");
  assert.doesNotMatch(overlay, /@click\.self/);
  assert.match(app, /@click="showOptimizationDialog = false">取消/);

  assert.doesNotMatch(app, /调用名称<input/);
  assert.doesNotMatch(app, /显示名称<input/);
  assert.match(app, /调用名称自动使用 \{\{ optimizationInvokeAs \}\}/);
  assert.match(
    app,
    /<select v-model="optimizationForm\.harness_key"[^>]*>[\s\S]*?v-for="harness in skillOptimizationHarnesses"/,
  );
  assert.doesNotMatch(app, /optimizationForm\.source_path/);
  assert.match(app, /本地 Skill 目录自动使用 \{\{ optimizationSourcePath/);
  assert.match(app, /v-model\.trim="harnessForm\.skill_base_dir"/);

  assert.doesNotMatch(options, /optimizationForm\.invoke_as/);
  assert.doesNotMatch(options, /skill_name:/);
  assert.doesNotMatch(options, /source_path: form\.source_path/);
  assert.match(options, /name: form\.skill_key/);
  assert.match(options, /invoke_as: `\/\$\{form\.skill_key\}`/);
  assert.match(options, /source_path: this\.optimizationSourcePath/);
  assert.match(options, /skill_base_dir\.replace\(\/\[\\\\\/\]\+\$\//);
  assert.match(options, /skill_base_dir_not_found/);
  assert.match(options, /compatibleOptimizationTargets/);
});
