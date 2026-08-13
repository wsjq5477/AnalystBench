import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("Skill optimization selects a host combination and keeps drafts until cancel", async () => {
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

  assert.doesNotMatch(app, /注册本地 Skill/);
  assert.doesNotMatch(app, /Skill Key<input/);
  assert.match(
    app,
    /<select v-model="optimizationForm\.combination_key"[^>]*>[\s\S]*?v-for="option in optimizationCombinationOptions"/,
  );
  assert.doesNotMatch(app, /optimizationForm\.source_path/);
  assert.match(app, /源目录为 \{\{ optimizationSourcePath \}\}/);
  assert.match(app, /v-model\.trim="harnessForm\.skill_base_dir"/);

  assert.doesNotMatch(options, /optimizationForm\.invoke_as/);
  assert.doesNotMatch(options, /createSkill\(/);
  assert.match(options, /optimizationCombinationOptions/);
  assert.match(options, /adoptHostSkill\(/);
  assert.match(options, /combination\.target\.id/);
  assert.match(options, /combination\.skill\.key/);
});
