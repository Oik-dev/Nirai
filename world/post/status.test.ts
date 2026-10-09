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

test("昔のtell記録があっても、自分宛ての長期タスクをMaster判断待ちと誤表示しない", () => {
  const lines: Line[] = [
    { kind: "letter", ts: "2026-10-04T00:00:00.000Z", id: "SELF", from: "Holo", to: "Holo", body: "D0" },
    { kind: "tell", ts: "2026-10-04T00:01:00.000Z", letter: "SELF", how: "拡張の印" },
  ];
  assert.deepEqual(residentPostStatus("Holo", lines, false), {
    name: "Holo", state: "waiting", unfinished: 1, stuck: 0,
  });
});

test("別室のnote/doneは止まった部屋のstuckを解除しない", () => {
  const lines: Line[] = [
    { ...letter("B"), from: "Claude", to: "Holo", work: "B" },
    { ...letter("A"), from: "Claude", to: "Holo", work: "A" },
    { kind: "tell", ts: "2026-10-04T00:01:00.000Z", letter: "B", how: "拡張の印" },
    { kind: "note", ts: "2026-10-04T00:02:00.000Z", letter: "A", body: "A進捗" },
    { kind: "done", ts: "2026-10-04T00:03:00.000Z", letter: "A" },
  ];
  assert.deepEqual(residentPostStatus("Holo", lines, false), {
    name: "Holo", state: "stuck", unfinished: 1, stuck: 1,
  });
  assert.equal(residentPostStatus("Holo", [...lines, { kind: "note", ts: "2026-10-04T00:04:00.000Z", letter: "B", body: "B進捗" }], false).stuck, 0);
});

test("済んだ手紙は未済件数に数えない", () => {
  const lines: Line[] = [
    letter("A"),
    { kind: "done", ts: "2026-10-04T00:01:00.000Z", letter: "A" },
  ];
  assert.equal(residentPostStatus("Codex", lines, false).state, "idle");
  assert.equal(residentPostStatus("Codex", lines, false).unfinished, 0);
});

test("上限で眠っている住人は、起きる時刻と一緒にlimitedで見える", () => {
  const lines: Line[] = [
    letter("A"),
    { kind: "wake", ts: "2026-10-04T00:01:00.000Z", letters: ["A"], how: "codex cli" },
    { kind: "stop", ts: "2026-10-04T00:02:00.000Z", how: "limit", until: "2026-10-04T02:00:00.000Z", untilKnown: true },
  ];
  assert.deepEqual(residentPostStatus("Codex", lines, false, new Date("2026-10-04T01:00:00.000Z")), {
    name: "Codex", state: "limited", unfinished: 1, stuck: 0, limitUntil: "2026-10-04T02:00:00.000Z", limitKnown: true,
  });
  assert.equal(residentPostStatus("Codex", lines, false, new Date("2026-10-04T02:00:01.000Z")).state, "waiting");
});
