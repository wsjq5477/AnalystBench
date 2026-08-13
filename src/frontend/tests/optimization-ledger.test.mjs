import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("Skill optimization exposes score-backed epoch summaries and safe lifecycle actions", async () => {
  const app = await readFile(new URL("../src/App.vue", import.meta.url), "utf8");
  const options = await readFile(
    new URL("../src/app-options.js", import.meta.url),
    "utf8",
  );
  const api = await readFile(new URL("../src/api.js", import.meta.url), "utf8");
  const request = await readFile(
    new URL("../src/utils/request.js", import.meta.url),
    "utf8",
  );

  assert.match(app, /本轮做了什么，分数如何变化/);
  assert.match(app, /epoch\.summary\.baseline_score/);
  assert.match(app, /epoch\.summary\.candidate_score/);
  assert.match(app, /epoch\.summary\.epoch_delta/);
  assert.match(app, /epoch\.summary\.cumulative_delta/);
  assert.match(app, /optimizationTokenChanges\(candidate\.change_stats\)/);
  assert.match(app, /candidate\.intent\.change_type/);
  assert.match(app, /导出 JSON/);
  assert.match(app, /环境预检/);
  assert.match(app, /原始来源目录不会被修改/);
  assert.match(app, /显式回滚/);
  assert.match(app, /更早 Epoch/);
  assert.match(app, /较新 Epoch/);
  assert.match(app, /Case 变化/);
  assert.match(app, /Failure Family 变化/);
  assert.match(app, /Dimension 变化/);
  assert.match(app, /Promotion Gate/);
  assert.match(app, /拒绝记录/);
  assert.match(app, /Independent Validation（正式验证推荐）/);
  assert.match(app, /Validation.*不回流后续 Prompt/);
  assert.match(app, /独立 Validation 固定只运行一个 Epoch/);
  assert.match(app, /仅冻结进快照.*不会自动执行/);
  assert.match(app, /option value="train_case_paths"/);
  assert.match(app, /option value="validation_case_paths"/);
  assert.match(app, /option value="hidden_test_case_paths"/);
  assert.match(app, /option value="prospective_holdout_case_paths"/);

  assert.match(options, /expected_lock_version: binding\.lock_version/);
  assert.match(options, /manual_ui_rollback/);
  assert.match(options, /window\.URL\.createObjectURL/);
  assert.match(options, /epoch_offset: this\.optimizationEpochOffset/);
  assert.match(options, /epoch_limit: this\.optimizationEpochLimit/);
  assert.match(options, /optimizationRollbackVersionIds/);
  assert.match(options, /selectedOptimizationBindingHistory/);
  assert.match(options, /form\.data_mode === "independent_validation"/);
  assert.match(options, /syncOptimizationDataMode/);
  assert.match(options, /mode: form\.data_mode/);
  assert.match(options, /train_case_paths:\s*form\.data_mode/);
  assert.match(options, /validation_case_paths:\s*form\.data_mode/);
  assert.match(options, /hidden_test_case_paths:\s*form\.data_mode/);
  assert.match(options, /prospective_holdout_case_paths:\s*form\.data_mode/);

  assert.match(api, /skill-optimization\/experiments\/\$\{experimentId\}\/export/);
  assert.match(api, /responseType: "blob"/);
  assert.match(api, /skills\/\$\{skillId\}\/versions\/\$\{versionId\}\/export/);
  assert.match(api, /bindings\/\$\{targetId\}\/rollback/);
  assert.match(api, /skills\/\$\{skillId\}\/binding-history/);
  assert.match(request, /payload instanceof Blob/);
  assert.match(request, /JSON\.parse\(await payload\.text\(\)\)/);
});
