import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { activeLimit, LEGACY_TRACK, type Line, MASTER, postDir, readAll, track, waitRefusal, waits } from "./letters.ts";
import { POST_OFFICE, toTellMaster, toWake } from "./waker.ts";

const at = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
const now = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s));
const REST = 60_000;
const letter = (id: string, s: number): Line => ({ kind: "letter", ts: at(s), id, from: "Holo", to: "Codex", body: id, work: "W" });
const withReads = (lines: Line[]): Line[] => lines.flatMap(line =>
  line.kind === "wake" ? [line,
    { kind: "read", ts: new Date(Date.parse(line.ts) + 100).toISOString(), work: line.work },
    { kind: "stop", ts: new Date(Date.parse(line.ts) + 200).toISOString(), how: "exit", work: line.work },
  ] : [line]);

test("済んでいない手紙があり、起きていなければ起こす", () => {
  assert.deepEqual(toWake([letter("A", 0)], false, now(1), REST), ["A"]);
});

test("筋ごとに進捗と届き直しを数える。他の作業場のnoteでは未済回数を戻さない", () => {
  const lines: Line[] = [
    { ...letter("A", 0), work: "X" },
    { ...letter("B", 0), work: "Y" },
    { kind: "wake", ts: at(1), letters: ["A"], how: "codex cli", work: "X" },
    { kind: "wake", ts: at(2), letters: ["A"], how: "codex cli", work: "X" },
    { kind: "wake", ts: at(3), letters: ["A"], how: "codex cli", work: "X" },
    { kind: "note", ts: at(4), letter: "B", body: "別の筋の進捗" },
    { kind: "wake", ts: at(5), letters: ["B"], how: "codex cli", work: "Y" },
  ];
  assert.deepEqual(toTellMaster(track(withReads(lines), "X"), 3).map(l => l.id), ["A"]);
  assert.equal(toTellMaster(track(lines, "Y"), 3).length, 0);
  assert.deepEqual(toWake(track(lines, "X"), false, now(90), REST), ["A"]);
  assert.deepEqual(toWake(track(lines, "Y"), false, now(90), REST), ["B"]);
});

test("workのない古い行は受付の筋として読み、部屋の行は読まない。作業場の筋からは見えない", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-legacy-"));
  const dir = postDir(root, "Codex");
  mkdirSync(dir, { recursive: true });
  const raw: object[] = [
    { ...letter("OLD", 0), work: undefined },
    { kind: "room", ts: at(0), url: "https://chatgpt.com/c/1" },
    { ...letter("WORK", 0), work: "X" },
    { kind: "wake", ts: at(1), how: "codex cli", letters: ["OLD"] },
    { kind: "stop", ts: at(2), how: "exit" },
    { kind: "done", ts: at(3), letter: "WORK" },
  ];
  writeFileSync(join(dir, "2026-10-04.jsonl"), raw.map(line => JSON.stringify(line)).join("\n") + "\n");
  const lines = readAll(root, "Codex");
  assert.equal(lines.some(l => (l as { kind: string }).kind === "room"), false);
  assert.deepEqual(track(lines, LEGACY_TRACK).map(l => l.kind), ["letter", "wake", "stop"]);
  assert.deepEqual(track(lines, "X").filter(l => l.kind === "letter").map(l => l.id), ["WORK"]);
  assert.equal(track(lines, "X").some(l => l.kind === "wake" || l.kind === "stop"), false);
  assert.equal(toWake(track(lines, "X"), false, now(90), REST).length, 0);
});

test("待っている手紙では起こさず、滞留にも数えない。待つ相手が済んだら起こす", () => {
  const holo: Line[] = [{ ...letter("A", 0), to: "Holo" }];
  holo.push({ kind: "note", ts: at(5), letter: "A", body: "Bの返事を待つ", waiting_for: "B" });
  for (const s of [1, 2, 3]) holo.push({ kind: "wake", ts: at(s * 10), letters: ["A"], how: "holo tab", work: "W" });
  const codex: Line[] = [{ ...letter("B", 4), from: "Holo", to: "Codex" }];
  const waiting = waits([holo, codex]);
  assert.deepEqual([...waiting], [["A", "B"]]);
  assert.deepEqual(toWake(holo, false, now(600), REST, waiting), []);
  assert.deepEqual(toTellMaster(withReads(holo), 3, waiting), [], "待っている間の目覚めは滞留に数えない");

  codex.push({ kind: "done", ts: at(700), letter: "B" });
  assert.equal(waits([holo, codex]).size, 0);
  assert.deepEqual(toWake(holo, false, now(800), REST, waits([holo, codex])), ["A"]);
});

test("Masterを待つ手紙は、Masterの返事（次の書き残しか済み）まで待つ", () => {
  const holo: Line[] = [letter("A", 0), { kind: "note", ts: at(1), letter: "A", body: "Masterに聞いた", waiting_for: MASTER }];
  assert.deepEqual([...waits([holo])], [["A", MASTER]]);
  holo.push({ kind: "note", ts: at(2), letter: "A", body: "Masterの答えで続ける" });
  assert.equal(waits([holo]).size, 0);
});

test("待てない相手：ない手紙・済んだ手紙・輪になる待ち", () => {
  const holo: Line[] = [{ ...letter("A", 0), to: "Holo" }, { ...letter("C", 0), to: "Holo" }, { kind: "done", ts: at(1), letter: "C" }];
  const codex: Line[] = [{ ...letter("B", 0), from: "Holo" }, { kind: "note", ts: at(2), letter: "B", body: "Aを待つ", waiting_for: "A" }];
  const team = [holo, codex];
  assert.match(waitRefusal(team, "A", "Z") ?? "", /という手紙はない/);
  assert.match(waitRefusal(team, "A", "C") ?? "", /もう済んでいる/);
  assert.match(waitRefusal(team, "A", "B") ?? "", /輪になる/);
  assert.equal(waitRefusal(team, "A", MASTER), undefined);
});

test("Windowsで名前の大文字小文字が違っても同じ筋として数える", () => {
  const lines: Line[] = [
    { ...letter("A", 0), work: "Work-X" },
    { ...letter("B", 0), work: "work-x" },
    { kind: "wake", ts: at(1), letters: ["A", "B"], how: "codex cli", work: "WORK-X" },
  ];
  const scoped = track(lines, "work-x");
  assert.deepEqual(toWake(scoped, false, now(90), REST), ["A", "B"]);
  assert.deepEqual(scoped.filter(l => l.kind === "letter").map(l => l.id), ["A", "B"]);
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

test("上限のuntilまでは起こさず、過ぎたら起こす。上限の目覚めは届き直し回数に数えない", () => {
  const lines: Line[] = [letter("A", 0)];
  for (const s of [1, 2, 3]) {
    lines.push({ kind: "wake", ts: at(s * 10), letters: ["A"], how: "codex cli" });
    lines.push({ kind: "stop", ts: at(s * 10 + 1), how: "limit", until: at(300), untilKnown: true });
  }
  assert.deepEqual(toWake(lines, false, now(299), REST), []);
  assert.deepEqual(toTellMaster(lines, 3), [], "上限で起きられなかった3回はMaster行きに数えない");
  assert.deepEqual(toWake(lines, false, now(301), REST), ["A"]);
});

test("筋Aで利用上限になったら、後から筋Bのwakeがあっても住人全体の眠りは解除されない", () => {
  const lines: Line[] = [
    { ...letter("A", 0), work: "A" },
    { ...letter("B", 0), work: "B" },
    { kind: "wake", ts: at(1), letters: ["A"], how: "codex cli", work: "A" },
    { kind: "stop", ts: at(2), how: "limit", until: at(300), work: "A" },
    { kind: "wake", ts: at(3), letters: ["B"], how: "codex cli", work: "B" },
  ];
  assert.equal(activeLimit(lines, now(100))?.work, "A");
});

test("起こした直後は、起きたと分かる前でも2度起こさない", () => {
  const lines: Line[] = [letter("A", 0), { kind: "wake", ts: at(1), letters: ["A"], how: "holo tab" }];
  assert.deepEqual(toWake(lines, false, now(5), REST), []);
});

test("何度起こしても済まない手紙は、一度だけMasterに知らせる", () => {
  const wake = (s: number): Line => ({ kind: "wake", ts: at(s), letters: ["A"], how: "codex cli" });
  const codex: Line[] = [letter("A", 0), wake(1), wake(2), wake(3), wake(4)];
  assert.deepEqual(toTellMaster(codex, 5), [], "まだ4回");
  const five = [...codex, wake(5)];
  assert.deepEqual(toTellMaster(withReads(five), 5).map(l => l.id), ["A"]);
  assert.deepEqual(toTellMaster(withReads([...five, { kind: "tell", ts: at(6), letter: "A", how: "Holoへの手紙" }, wake(7)]), 5), [], "もう知らせた");
});

test("別の依頼を進めてnoteを書いている間は、待つ手紙を滞留にしない", () => {
  const a: Line = { kind: "letter", ts: at(0), id: "A", from: "Claude", to: "Holo", body: "段1" };
  const b: Line = { kind: "letter", ts: at(0), id: "B", from: "Holo", to: "Holo", body: "C2" };
  const lines: Line[] = [a, b];
  for (const [n, start] of [1, 5, 9].entries()) {
    lines.push({ kind: "wake", ts: at(start), letters: ["A", "B"], how: "holo tab" });
    lines.push({ kind: "note", ts: at(start + 1), letter: "B", body: `C2進捗${n}` });
  }
  assert.deepEqual(toTellMaster(lines, 3), []);
  assert.deepEqual(toWake(lines, false, now(90), REST), ["A", "B"]);
});

test("tell後のnoteで復帰し、再び空起床3回なら再度tellになる", () => {
  const a = letter("A", 0);
  const wake = (s: number): Line => ({ kind: "wake", ts: at(s), letters: ["A"], how: "holo tab" });
  const told: Line = { kind: "tell", ts: at(4), letter: "A", how: "拡張の印" };
  const old = [a, wake(1), wake(2), wake(3), told];
  assert.deepEqual(toWake(old, false, now(70), REST), []);
  const progressed: Line[] = [...old, { kind: "note", ts: at(6), letter: "A", body: "確認した" }];
  assert.deepEqual(toWake(progressed, false, now(70), REST), ["A"]);
  const stale = [...progressed, wake(7), wake(8), wake(9)];
  assert.deepEqual(toTellMaster(withReads(stale), 3).map(l => l.id), ["A"]);
  assert.deepEqual(toTellMaster([...stale, { kind: "tell", ts: at(10), letter: "A", how: "拡張の印" }], 3), []);
});

test("Masterに回した手紙では、もう起こさない（届き直しの上限）", () => {
  const wakes = [1, 2, 3, 4, 5].map((s): Line => ({ kind: "wake", ts: at(s), letters: ["A"], how: "holo tab" }));
  const told: Line = { kind: "tell", ts: at(6), letter: "A", how: "拡張の印" };
  assert.deepEqual(toWake(withReads([letter("A", 0), ...wakes, told]), false, now(600), REST), []);
  assert.deepEqual(toWake(withReads([letter("A", 0), ...wakes, told, letter("B", 300)]), false, now(600), REST), ["B"], "新しい手紙では起こす");
});

test("自分宛ての長期タスクもMasterへ知らせ、同じ手紙では起こさない", () => {
  const self: Line = { kind: "letter", ts: at(0), id: "SELF", from: "Holo", to: "Holo", body: "D0の続き" };
  const wakes = [1, 2, 3].map((s): Line => ({
    kind: "wake", ts: at(s), letters: ["SELF"], how: "holo tab",
  }));
  const lines: Line[] = [...[self, ...wakes], { kind: "tell", ts: at(4), letter: "SELF", how: "拡張の印" }];
  assert.deepEqual(toTellMaster(withReads([self, ...wakes]), 3).map(l => l.id), ["SELF"]);
  assert.deepEqual(toWake(lines, false, now(600), REST), [], "知らせた手紙は起こさない");
  assert.deepEqual(toWake(lines, false, now(905), REST), [], "長い時間がたっても再起床しない");
  const added: Line = { kind: "letter", ts: at(5), id: "NEW", from: "Codex", to: "Holo", body: "レビュー結果" };
  assert.deepEqual(toWake([...lines, added], false, now(90), REST), ["NEW"],
    "新しい依頼があれば通常の起床間隔を優先する");
});

test("言付けの手紙でHolo自身が詰まっても、知らせは1度で、連なって増えない", () => {
  const relay: Line = { kind: "letter", ts: at(0), id: "T", from: POST_OFFICE, to: "Holo", body: "伝えて", based_on: "A" };
  const wakes = [1, 2, 3, 4, 5].map((s): Line => ({ kind: "wake", ts: at(s), letters: ["T"], how: "holo tab" }));
  assert.deepEqual(toTellMaster(withReads([relay, ...wakes]), 5).map(l => l.id), ["T"]);
  const told: Line = { kind: "tell", ts: at(6), letter: "T", how: "拡張の印" };
  const more = [6, 7, 8, 9, 10].map((s): Line => ({ kind: "wake", ts: at(s + 1), letters: ["T"], how: "holo tab" }));
  assert.deepEqual(toTellMaster([relay, ...wakes, told, ...more], 5), []);
});
