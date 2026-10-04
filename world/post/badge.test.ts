import { test } from "node:test";
import assert from "node:assert/strict";
import { badgeText } from "../holo-extension/badge.js";

test("郵便局につながらなければ ×、判断待ちがあれば !、なければ印なし", () => {
  assert.equal(badgeText(undefined), "×");
  assert.equal(badgeText({ residents: [] }), "");
  assert.equal(badgeText({ residents: [{ name: "Holo", stuck: 0 }, { name: "Codex", stuck: 1 }] }), "!");
  assert.equal(badgeText({ residents: [{ name: "Holo", stuck: 0 }, { name: "Codex", stuck: 0 }] }), "");
});

test("不正なresidentやstuckは例外にせず × にする", () => {
  assert.equal(badgeText({ residents: [{ name: "Holo" }] }), "×");
  assert.equal(badgeText({ residents: [{ name: "Holo", stuck: "invalid" }] }), "×");
  assert.equal(badgeText({ residents: [null] }), "×");
  assert.doesNotThrow(() => badgeText({ residents: [{ name: "Holo", stuck: { toString: null, valueOf: null } }] }));
  assert.equal(badgeText({ residents: [{ name: "Holo", stuck: { toString: null, valueOf: null } }] }), "×");
});
