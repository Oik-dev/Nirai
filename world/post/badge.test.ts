import { test } from "node:test";
import assert from "node:assert/strict";
import { badgeText } from "../holo-extension/badge.js";

const fine = { name: "Holo", stuck: 0, unreachable: 0 };

test("郵便局を見られなければ ×、要確認があれば !、部屋がなくてもMaster待ちなら ?、なければ印なし", () => {
  assert.equal(badgeText(undefined), "×");
  assert.equal(badgeText({ residents: [], seats: [], masterAsks: 0 }), "");
  assert.equal(badgeText({ residents: [fine, { name: "Codex", stuck: 1, unreachable: 0 }], seats: [], masterAsks: 1 }), "!");
  assert.equal(badgeText({ residents: [{ ...fine, unreachable: 1 }], seats: [], masterAsks: 0 }), "!");
  assert.equal(badgeText({ residents: [fine], seats: [{ seat: 1, problem: "会話のURLが分からない" }], masterAsks: 0 }), "!");
  assert.equal(badgeText({ residents: [fine], seats: [], masterAsks: 1 }), "?");
  assert.equal(badgeText({ residents: [fine], seats: [{ seat: 1, masterWaiting: true }, { seat: 2 }], masterAsks: 0 }), "");
  assert.equal(badgeText({ residents: [fine], seats: [{ seat: 1, problem: "x" }], masterAsks: 1 }), "!");
});

test("不正な住人や席は例外にせず × にする", () => {
  assert.equal(badgeText({ residents: [] }), "×");
  assert.equal(badgeText({ residents: [{ name: "Holo" }], seats: [] }), "×");
  assert.equal(badgeText({ residents: [{ name: "Holo", stuck: "invalid" }], seats: [] }), "×");
  assert.equal(badgeText({ residents: [null], seats: [] }), "×");
  assert.equal(badgeText({ residents: [fine], seats: [null] }), "×");
  assert.doesNotThrow(() => badgeText({ residents: [{ name: "Holo", stuck: { toString: null, valueOf: null } }], seats: [] }));
});
