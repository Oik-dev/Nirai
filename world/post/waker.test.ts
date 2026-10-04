import { test } from "node:test";
import assert from "node:assert/strict";
import type { Line } from "./letters.ts";
import { POST_OFFICE, toTellMaster, toWake } from "./waker.ts";

const at = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
const now = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s));
const REST = 60_000;
const letter = (id: string, s: number): Line => ({ kind: "letter", ts: at(s), id, from: "Holo", to: "Codex", body: id });

test("済んでいない手紙があり、起きていなければ起こす", () => {
  assert.deepEqual(toWake([letter("A", 0)], false, now(1), REST), ["A"]);
});

test("起きている間は起こさない", () => {
  assert.deepEqual(toWake([letter("A", 0)], true, now(1), REST), []);
});

test("済んだ手紙では起こさない", () => {
  const lines: Line[] = [letter("A", 0), { kind: "done", ts: at(5), letter: "A" }];
  assert.deepEqual(toWake(lines, false, now(10), REST), []);
});

test("止まってから rest の間は待ち、過ぎたら届き直す", () => {
  const lines: Line[] = [
    letter("A", 0),
    { kind: "wake", ts: at(1), letters: ["A"], how: "codex exec" },
    { kind: "stop", ts: at(10), how: "timeout" },
  ];
  assert.deepEqual(toWake(lines, false, now(30), REST), []);
  assert.deepEqual(toWake(lines, false, now(71), REST), ["A"]);
});

test("起こした直後は、起きたと分かる前でも2度起こさない", () => {
  const lines: Line[] = [letter("A", 0), { kind: "wake", ts: at(1), letters: ["A"], how: "holo tab" }];
  assert.deepEqual(toWake(lines, false, now(5), REST), []);
});

test("何度起こしても済まない手紙は、一度だけMasterに知らせる", () => {
  const wake = (s: number): Line => ({ kind: "wake", ts: at(s), letters: ["A"], how: "codex cli" });
  const codex: Line[] = [letter("A", 0), wake(1), wake(2), wake(3), wake(4)];
  assert.deepEqual(toTellMaster({ Codex: codex, Holo: [] }, 5), [], "まだ4回");
  const five = [...codex, wake(5)];
  assert.deepEqual(toTellMaster({ Codex: five, Holo: [] }, 5).map(l => l.id), ["A"]);
  const told: Line[] = [{ kind: "letter", ts: at(6), id: "T", from: POST_OFFICE, to: "Holo", body: "伝えて", based_on: "A" }];
  assert.deepEqual(toTellMaster({ Codex: five, Holo: told }, 5), [], "もう知らせた");
});
