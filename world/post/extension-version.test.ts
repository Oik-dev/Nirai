import { test } from "node:test";
import assert from "node:assert/strict";
import { shouldReloadExtension } from "../holo-extension/version.js";

test("拡張は初回の版を記憶し、同じ版では読み直さず、版の変更時だけ読み直す", () => {
  assert.equal(shouldReloadExtension(undefined, "rev-a"), false, "初回は読み直さず記録する");
  assert.equal(shouldReloadExtension("rev-a", "rev-a"), false, "service worker再起動では同じ版");
  assert.equal(shouldReloadExtension("rev-a", "rev-b"), true, "入れ替わった版だけ読み直す");
  assert.equal(shouldReloadExtension("rev-a", undefined), false, "郵便局に接続できない間は読み直さない");
  assert.equal(shouldReloadExtension("rev-a", ""), false, "不明な版で読み直さない");
});
