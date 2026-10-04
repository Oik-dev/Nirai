import { test } from "node:test";
import assert from "node:assert/strict";
import type { Line } from "./letters.ts";
import { residentPostStatus } from "./status.ts";

const letter = (id: string): Line => ({
  kind: "letter",
  ts: "2026-10-04T00:00:00.000Z",
  id,
  from: "Holo",
  to: "Codex",
  body: "仕事",
});

test("未済手紙がなければ待機", () => {
  assert.deepEqual(residentPostStatus("Codex", [], false), {
    name: "Codex", state: "idle", unfinished: 0, stuck: 0,
  });
});

test("未済手紙があり眠っていれば起こし待ち、起きていれば作業中", () => {
  assert.equal(residentPostStatus("Codex", [letter("A")], false).state, "waiting");
  assert.equal(residentPostStatus("Codex", [letter("A")], true).state, "working");
});

test("Masterへ知らせ済みの未済手紙があれば判断待ちを優先する", () => {
  const lines: Line[] = [
    letter("A"),
    { kind: "tell", ts: "2026-10-04T00:01:00.000Z", letter: "A", how: "Holoへの手紙" },
  ];
  assert.deepEqual(residentPostStatus("Codex", lines, true), {
    name: "Codex", state: "stuck", unfinished: 1, stuck: 1,
  });
});

test("済んだ手紙は未済件数に数えない", () => {
  const lines: Line[] = [
    letter("A"),
    { kind: "done", ts: "2026-10-04T00:01:00.000Z", letter: "A" },
  ];
  assert.equal(residentPostStatus("Codex", lines, false).state, "idle");
  assert.equal(residentPostStatus("Codex", lines, false).unfinished, 0);
});
