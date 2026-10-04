import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { append, type Line } from "./letters.ts";
import { PostOffice } from "./office.ts";
import { ensureWork, recycle, toClean, workKey } from "./work.ts";

const at = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
const letter = (id: string, work?: string): Line => ({ kind: "letter", ts: at(0), id, from: "Holo", to: "Codex", body: id, ...(work ? { work } : {}) });
const done = (id: string, s = 9): Line => ({ kind: "done", ts: at(s), letter: id });
const KEEP = 50 * 60_000;
const after = (line: Line, ms: number) => new Date(Date.parse(line.ts) + ms);

test("その名前の手紙が全部済んだ作業場だけを片付ける", () => {
  const codex = [letter("A", "review"), done("A"), letter("B", "build")];
  const claude = [letter("C", "review"), done("C", 20)];
  assert.deepEqual(toClean(["review", "build"], [codex, [letter("C", "review")]], new Set(), after(done("A"), KEEP + 1), KEEP), [], "Claude宛ての review がまだ済んでいない");
  assert.deepEqual(toClean(["review", "build"], [codex, claude], new Set(), after(done("C", 20), KEEP - 1), KEEP), [], "最新doneからkeepMs未満");
  assert.deepEqual(toClean(["review", "build"], [codex, claude], new Set(), after(done("C", 20), KEEP), KEEP), ["review"]);
});

test("コマンドが動いている作業場は、手紙が全部済んでも片付けない", () => {
  const codex = [letter("A", "review"), done("A")];
  const later = after(done("A"), KEEP + 1);
  assert.deepEqual(toClean(["review"], [codex], new Set(["review"]), later, KEEP), []);
  assert.deepEqual(toClean(["review"], [codex], new Set(), later, KEEP), ["review"]);
});

test("郵便局を起こし直しても、最後のdoneからkeepMsまでは同じ作業場を残す", () => {
  const lines = [letter("A", "review"), done("A")];
  assert.deepEqual(toClean(["review"], [lines], new Set(), after(done("A"), 61_000), KEEP), [], "再起動61秒後でも残す");
  assert.deepEqual(toClean(["review"], [lines], new Set(), after(done("A"), KEEP), KEEP), ["review"], "keepMs後は片付ける");
});

test("新しいPostOfficeに起こし直しても、61秒後は作業場を残し、keepMs後に片付ける", () => {
  const base = mkdtempSync(join(tmpdir(), "nirai-work-restart-"));
  const residentsRoot = join(base, "residents");
  const workRoot = join(base, "Work");
  ensureWork(workRoot, "review");
  append(residentsRoot, "Holo", letter("A", "review"));
  append(residentsRoot, "Holo", done("A"));
  const officeAfterRestart = new PostOffice({
    residentsRoot, workRoot, team: ["Holo"], tellMasterAfter: 3,
    sweepMs: 60_000, restMs: 60_000, workKeepMs: KEEP,
  });

  officeAfterRestart.sweep(after(done("A"), 61_000));
  assert.equal(existsSync(join(workRoot, "review")), true, "再起動61秒後でも残る");
  officeAfterRestart.sweep(after(done("A"), KEEP));
  assert.equal(existsSync(join(workRoot, "review")), false, "keepMs後に片付く");
});

test("同じ作業場にdoneが複数あるときは、いちばん新しいdoneからkeepMsを数える", () => {
  const lines = [letter("A", "review"), done("A", 9), letter("B", "review"), done("B", 30)];
  assert.deepEqual(toClean(["review"], [lines], new Set(), after(done("A", 9), KEEP + 1), KEEP), [], "古いdone基準では消さない");
  assert.deepEqual(toClean(["review"], [lines], new Set(), after(done("B", 30), KEEP), KEEP), ["review"]);
});

test("手紙に出てこないフォルダーには触れない（Masterが置いたものなど）", () => {
  assert.deepEqual(toClean(["mine"], [[letter("A", "review"), done("A")]]), []);
});

test("作業場は Work の直下にだけ作る", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-work-"));
  ensureWork(root, "review");
  assert.equal(existsSync(join(root, "review")), true);
  for (const name of ["..", "..\\x", "a\\b", "a/b"]) assert.throws(() => ensureWork(root, name), name);
});

test("中にリンク（ジャンクション）がある作業場は片付けず、リンク先は無事", () => {
  const base = mkdtempSync(join(tmpdir(), "nirai-links-"));
  const outside = join(base, "outside");
  mkdirSync(outside);
  writeFileSync(join(outside, "sentinel.txt"), "外の本物");
  const root = join(base, "Work");
  ensureWork(root, "inner");
  symlinkSync(outside, join(root, "inner", "link"), "junction");
  symlinkSync(outside, join(root, "itself"), "junction");

  assert.equal(recycle(root, "inner"), "has-links");
  assert.equal(recycle(root, "itself"), "has-links");
  assert.equal(readFileSync(join(outside, "sentinel.txt"), "utf8"), "外の本物");
  assert.equal(existsSync(join(root, "inner", "link")), true);
});

test("作業場の名前は、大文字と小文字を区別しない（Windowsのフォルダーと同じ）", () => {
  assert.deepEqual(toClean(["Review"], [[letter("A", "review")]]), [], "済んでいない手紙がある");
  const later = after(done("A"), KEEP + 1);
  assert.deepEqual(toClean(["Review"], [[letter("A", "REVIEW"), done("A")]], new Set(), later, KEEP), ["Review"]);
  assert.deepEqual(toClean(["Review"], [[letter("A", "review"), done("A")]], new Set([workKey("REVIEW")]), later, KEEP), [], "コマンドが動いている");
});
