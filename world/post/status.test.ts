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
  work: "W",
});

test("未済手紙がなければ待機", () => {
  assert.deepEqual(residentPostStatus("Codex", [], false), {
    name: "Codex", state: "idle", unfinished: 0, waiting: 0, stuck: 0, unreachable: 0,
  });
});

test("未済手紙があり眠っていれば順番待ち、起きていれば作業中", () => {
  assert.equal(residentPostStatus("Codex", [letter("A")], false).state, "queued");
  assert.equal(residentPostStatus("Codex", [letter("A")], true).state, "working");
});

test("未済手紙が全部ほかの手紙かMasterを待っていれば返事待ち。1通でも起こせれば順番待ち", () => {
  const lines = [letter("A"), letter("B")];
  const status = residentPostStatus("Codex", lines, false, new Date(), new Map([["A", "Master"], ["B", "X"]]));
  assert.equal(status.state, "waiting");
  assert.equal(status.waiting, 2);
  assert.equal(residentPostStatus("Codex", lines, false, new Date(), new Map([["A", "Master"]])).state, "queued");
});

test("Masterへ知らせ済みの未済手紙があれば判断待ちを優先する", () => {
  const lines: Line[] = [
    letter("A"),
    { kind: "tell", ts: "2026-10-04T00:01:00.000Z", letter: "A", how: "Holoへの手紙" },
  ];
  assert.deepEqual(residentPostStatus("Codex", lines, true), {
    name: "Codex", state: "stuck", unfinished: 1, waiting: 0, stuck: 1, unreachable: 0,
  });
});

test("自分宛ての滞留もstuckになる", () => {
  const lines: Line[] = [
    { kind: "letter", ts: "2026-10-04T00:00:00.000Z", id: "SELF", from: "Holo", to: "Holo", body: "D0", work: "W" },
    { kind: "tell", ts: "2026-10-04T00:01:00.000Z", letter: "SELF", how: "拡張の印" },
  ];
  assert.deepEqual(residentPostStatus("Holo", lines, false), {
    name: "Holo", state: "stuck", unfinished: 1, waiting: 0, stuck: 1, unreachable: 0,
  });
});

test("別の作業場のnote/doneは、止まった作業場のstuckを解除しない", () => {
  const lines: Line[] = [
    { ...letter("B"), from: "Claude", to: "Holo", work: "B" },
    { ...letter("A"), from: "Claude", to: "Holo", work: "A" },
    { kind: "tell", ts: "2026-10-04T00:01:00.000Z", letter: "B", how: "拡張の印" },
    { kind: "note", ts: "2026-10-04T00:02:00.000Z", letter: "A", body: "A進捗" },
    { kind: "done", ts: "2026-10-04T00:03:00.000Z", letter: "A" },
  ];
  assert.deepEqual(residentPostStatus("Holo", lines, false), {
    name: "Holo", state: "stuck", unfinished: 1, waiting: 0, stuck: 1, unreachable: 0,
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
    { kind: "wake", ts: "2026-10-04T00:01:00.000Z", letters: ["A"], how: "codex cli", work: "W" },
    { kind: "stop", ts: "2026-10-04T00:02:00.000Z", how: "limit", until: "2026-10-04T02:00:00.000Z", untilKnown: true, work: "W" },
  ];
  assert.deepEqual(residentPostStatus("Codex", lines, false, new Date("2026-10-04T01:00:00.000Z")), {
    name: "Codex", state: "limited", unfinished: 1, waiting: 0, stuck: 0, unreachable: 0, limitUntil: "2026-10-04T02:00:00.000Z", limitKnown: true,
  });
  assert.equal(residentPostStatus("Codex", lines, false, new Date("2026-10-04T02:00:01.000Z")).state, "queued");
});
