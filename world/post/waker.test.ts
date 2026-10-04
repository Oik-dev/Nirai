import { test } from "node:test";
import assert from "node:assert/strict";
import type { Line } from "./letters.ts";
import { toWake } from "./waker.ts";

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
