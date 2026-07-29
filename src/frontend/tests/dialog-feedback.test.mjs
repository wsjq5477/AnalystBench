import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("toast feedback stays visible above modal dialogs", async () => {
  const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
  const toastLayer = styles.match(/\.toast\s*\{[^}]*z-index:\s*(\d+)/);
  const dialogLayer = styles.match(/\.dialog-overlay\s*\{[^}]*z-index:\s*(\d+)/);

  assert.ok(toastLayer, "toast z-index should be explicit");
  assert.ok(dialogLayer, "dialog z-index should be explicit");
  assert.ok(Number(toastLayer[1]) > Number(dialogLayer[1]));
});
