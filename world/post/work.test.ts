import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Line } from "./letters.ts";
import { ensureWork, recycle, toClean, workKey } from "./work.ts";

const at = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
const letter = (id: string, work?: string): Line => ({ kind: "letter", ts: at(0), id, from: "Holo", to: "Codex", body: id, ...(work ? { work } : {}) });
const done = (id: string): Line => ({ kind: "done", ts: at(9), letter: id });

test("その名前の手紙が全部済んだ作業場だけを片付ける", () => {
  const codex = [letter("A", "review"), done("A"), letter("B", "build")];
  const claude = [letter("C", "review")];
  assert.deepEqual(toClean(["review", "build"], [codex, claude]), [], "Claude宛ての review がまだ済んでいない");
  assert.deepEqual(toClean(["review", "build"], [codex, [...claude, done("C")]]), ["review"]);
});

test("コマンドが動いている作業場は、手紙が全部済んでも片付けない", () => {
  const codex = [letter("A", "review"), done("A")];
  assert.deepEqual(toClean(["review"], [codex], new Set(["review"])), []);
  assert.deepEqual(toClean(["review"], [codex], new Set()), ["review"]);
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
  assert.deepEqual(toClean(["Review"], [[letter("A", "REVIEW"), done("A")]]), ["Review"]);
  assert.deepEqual(toClean(["Review"], [[letter("A", "review"), done("A")]], new Set([workKey("REVIEW")])), [], "コマンドが動いている");
});
