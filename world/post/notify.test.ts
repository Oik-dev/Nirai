import { test } from "node:test";
import assert from "node:assert/strict";
import { noticeText } from "./notify.ts";

// 本物の通知は出さない（Masterの画面に出てしまう）。入口で文字を整えるところだけ確かめる。
test("通知の文字から制御文字を除く（NULが入ると、通知を出す前に郵便局ごと落ちていた。2026-10-04、Codexのレビュー）", () => {
  assert.equal(noticeText("Codexが\u0000済ませて\r\nいない\t"), "Codexが 済ませて いない");
  assert.equal(noticeText("<&\"'$()>"), "<&\"'$()>", "XMLのエスケープとPowerShellの扱いは通知の中でする");
});
