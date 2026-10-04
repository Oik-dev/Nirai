import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Line } from "./letters.ts";
import { ensureWork, toClean } from "./work.ts";

const at = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
const letter = (id: string, work?: string): Line => ({ kind: "letter", ts: at(0), id, from: "Holo", to: "Codex", body: id, ...(work ? { work } : {}) });
const done = (id: string): Line => ({ kind: "done", ts: at(9), letter: id });

test("その名前の手紙が全部済んだ作業場だけを片付ける", () => {
  const codex = [letter("A", "review"), done("A"), letter("B", "build")];
  const claude = [letter("C", "review")];
  assert.deepEqual(toClean(["review", "build"], [codex, claude]), [], "Claude宛ての review がまだ済んでいない");
  assert.deepEqual(toClean(["review", "build"], [codex, [...claude, done("C")]]), ["review"]);
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
