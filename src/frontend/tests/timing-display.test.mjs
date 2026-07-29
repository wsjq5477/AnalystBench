import assert from "node:assert/strict";
import test from "node:test";

import {
  elapsedDurationMs,
  formatDurationMs,
} from "../src/timing-display.js";

test("formats milliseconds as seconds, minutes, and hours", () => {
  assert.equal(formatDurationMs(2400), "2.4 秒");
  assert.equal(formatDurationMs(38000), "38 秒");
  assert.equal(formatDurationMs(77000), "1 分 17 秒");
  assert.equal(formatDurationMs(3_660_000), "1 时 01 分");
});

test("shows an empty marker for missing or invalid durations", () => {
  assert.equal(formatDurationMs(null), "—");
  assert.equal(formatDurationMs(-1), "—");
  assert.equal(formatDurationMs("invalid"), "—");
});

test("recomputes elapsed time from the server start timestamp", () => {
  assert.equal(
    elapsedDurationMs("2026-07-29T00:00:00.000Z", Date.parse("2026-07-29T00:00:38.250Z")),
    38250,
  );
});
