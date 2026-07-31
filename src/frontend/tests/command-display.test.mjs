import assert from "node:assert/strict";
import test from "node:test";

import {
  formatCommand,
  formatCommandArgument,
} from "../src/command-display.js";

test("shows non-ASCII prompt arguments with explicit double-quote boundaries", () => {
  assert.equal(
    formatCommand([
      "/home/jiqi/.vscode-server/extensions/anthropic.claude/resources/native-binary/claude",
      "-p",
      "帮我分析日志/home/jiqi/LLM/AnalystBench/data/test.md",
    ]),
    '/home/jiqi/.vscode-server/extensions/anthropic.claude/resources/native-binary/claude -p "帮我分析日志/home/jiqi/LLM/AnalystBench/data/test.md"',
  );
});

test("quotes whitespace and escapes embedded double quotes", () => {
  assert.equal(formatCommandArgument("two words"), '"two words"');
  assert.equal(formatCommandArgument('say "hello"'), '"say \\"hello\\""');
});

test("keeps ordinary executable paths and flags unquoted", () => {
  assert.equal(formatCommand(["/usr/local/bin/claude", "-p"]), "/usr/local/bin/claude -p");
  assert.equal(formatCommand(null), "");
});
