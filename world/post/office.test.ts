import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { append, readAll, type Stop } from "./letters.ts";
import { PostOffice } from "./office.ts";

function office(root: string) {
  return new PostOffice({
    residentsRoot: root,
    workRoot: mkdtempSync(join(tmpdir(), "nirai-work-")),
    team: ["Holo", "Codex", "Claude"],
    tellMasterAfter: 3,
    sweepMs: 60_000,
    restMs: 60_000,
    workKeepMs: 50 * 60_000,
    limitWaitMs: 60 * 60_000,
  });
}

test("上限で眠ったら未済手紙ごとにHoloへ1通だけ知らせ、同じstopを見直しても増やさない", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-limit-"));
  append(root, "Codex", {
    kind: "letter", ts: "2026-10-05T06:00:00.000Z", id: "A", from: "Holo", to: "Codex", body: "レビューして",
  });
  const stop: Stop = {
    kind: "stop", ts: "2026-10-05T06:05:00.000Z", how: "limit", until: "2026-10-05T07:30:00.000Z", untilKnown: true,
  };
  const post = office(root);
  post.onResidentStop("Codex", stop);
  post.onResidentStop("Codex", stop);
  post.stop();

  const notices = readAll(root, "Holo").filter(line => line.kind === "letter");
  assert.equal(notices.length, 1);
  assert.match(notices[0].kind === "letter" ? notices[0].body : "", /Codexは上限/);
  assert.match(notices[0].kind === "letter" ? notices[0].body : "", /A/);
  assert.equal(notices[0].kind === "letter" && notices[0].based_on, `limit:Codex:${stop.ts}:A`);
});

test("CodexやClaudeが出した手紙の上限の知らせも、出した人ではなくHoloへ届く", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-limit-"));
  append(root, "Codex", {
    kind: "letter", ts: "2026-10-05T06:00:00.000Z", id: "B", from: "Claude", to: "Codex", body: "確かめて",
  });
  const post = office(root);
  post.onResidentStop("Codex", {
    kind: "stop", ts: "2026-10-05T06:05:00.000Z", how: "limit", until: "2026-10-05T07:30:00.000Z", untilKnown: true,
  });
  post.stop();
  assert.equal(readAll(root, "Claude").filter(line => line.kind === "letter").length, 0);
  const notice = readAll(root, "Holo").find(line => line.kind === "letter");
  assert.ok(notice?.kind === "letter");
  assert.match(notice.body, /Claudeの手紙 B/);
});

test("起きる時刻を読めない上限は、1時間後に再試行すると知らせる", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-limit-"));
  append(root, "Claude", {
    kind: "letter", ts: "2026-10-05T06:00:00.000Z", id: "A", from: "Holo", to: "Claude", body: "設計して",
  });
  const post = office(root);
  post.onResidentStop("Claude", {
    kind: "stop", ts: "2026-10-05T06:05:00.000Z", how: "limit", until: "2026-10-05T07:05:00.000Z", untilKnown: false,
  });
  post.stop();
  const notice = readAll(root, "Holo").find(line => line.kind === "letter");
  assert.ok(notice?.kind === "letter");
  assert.match(notice.body, /起きる時刻は分からない/);
  assert.match(notice.body, /60分後/);
  assert.match(notice.body, /代わりに頼んで/);
});
