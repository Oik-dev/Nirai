import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { append, readAll, scopeLines, type Stop } from "./letters.ts";
import { PostOffice } from "./office.ts";
import { residentPostStatus } from "./status.ts";
import { toWake } from "./waker.ts";
import type { CliResident } from "./cli.ts";

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

test("新版待ちならCLIを起こさず、拒否後は未済の手紙をそのまま起こす", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-reload-wake-"));
  const work = mkdtempSync(join(tmpdir(), "nirai-reload-work-"));
  let waiting = true;
  const sent: string[][] = [];
  const fakeCli = {
    name: "Codex", awake: () => false,
    wake: (letters: string[]) => sent.push(letters),
  } as unknown as CliResident;
  append(root, "Codex", { kind: "letter", id: "WAIT", from: "Holo", to: "Codex",
    body: "レビュー", ts: "2026-10-05T06:00:00.000Z" });
  const post = new PostOffice({
    residentsRoot: root, workRoot: work, team: ["Holo", "Codex"],
    tellMasterAfter: 3, sweepMs: 60_000, restMs: 60_000,
    workKeepMs: 60_000, limitWaitMs: 60_000,
  }, [fakeCli], () => new Set(), () => {}, () => waiting);
  try {
    post.sweep(new Date("2026-10-05T06:05:00.000Z"));
    assert.deepEqual(sent, []);
    assert.equal(readAll(root, "Codex").filter(line => line.kind === "wake").length, 0);
    waiting = false;
    post.sweep(new Date("2026-10-05T06:06:00.000Z"));
    assert.deepEqual(sent, [["WAIT"]]);
  } finally {
    post.stop();
  }
});

test("別の作業場は最大2筋まで同時に起こし、空いたら古い未済筋を先に起こす", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-parallel-root-"));
  const workRoot = mkdtempSync(join(tmpdir(), "nirai-parallel-work-"));
  const started: { work?: string; letters: string[] }[] = [];
  const active = new Set<string>();
  const fakeCli = {
    name: "Codex",
    awake: () => active.size > 0,
    awakeWork: (work?: string) => active.has(work ?? ""),
    awakeCount: () => active.size,
    wake: (letters: string[], _text: string, _now: Date, work?: string) => {
      active.add(work ?? "");
      started.push({ work, letters });
      append(root, "Codex", { kind: "wake", ts: _now.toISOString(), letters, how: "codex cli", ...(work ? { work } : {}) });
    },
  } as unknown as CliResident;
  for (const [id, work, ts] of [["C", "third", 3], ["B", "second", 2], ["A", "first", 1]] as const) {
    append(root, "Codex", { kind: "letter", ts: `2026-10-05T06:00:0${ts}.000Z`, id, from: "Holo", to: "Codex", body: id, work });
  }
  const post = new PostOffice({
    residentsRoot: root, workRoot, team: ["Holo", "Codex"],
    tellMasterAfter: 3, sweepMs: 60_000, restMs: 60_000,
    workKeepMs: 60_000, limitWaitMs: 60_000, maxConcurrent: { Codex: 2 },
  }, [fakeCli]);
  post.sweep(new Date("2026-10-05T06:05:00.000Z"));
  assert.deepEqual(started, [{ work: "first", letters: ["A"] }, { work: "second", letters: ["B"] }]);
  active.delete("first");
  append(root, "Codex", { kind: "stop", ts: "2026-10-05T06:06:00.000Z", how: "exit", work: "first" });
  post.sweep(new Date("2026-10-05T06:06:01.000Z"));
  assert.deepEqual(started.at(-1), { work: "third", letters: ["C"] });
  post.stop();
});

test("別筋のnoteで滞留判定を帳消しにせず、3回の筋だけMasterへ伝える", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-parallel-tell-"));
  const workRoot = mkdtempSync(join(tmpdir(), "nirai-parallel-tell-work-"));
  append(root, "Codex", { kind: "letter", ts: "2026-10-05T06:00:00Z", id: "X", from: "Claude", to: "Codex", body: "X", work: "X" });
  append(root, "Codex", { kind: "letter", ts: "2026-10-05T06:00:00Z", id: "Y", from: "Claude", to: "Codex", body: "Y", work: "Y" });
  for (let i = 1; i <= 3; i++) {
    append(root, "Codex", { kind: "wake", ts: `2026-10-05T06:00:0${i}.000Z`, letters: ["X"], how: "codex cli", work: "X" });
    append(root, "Codex", { kind: "read", ts: `2026-10-05T06:00:0${i}.100Z`, work: "X" });
    append(root, "Codex", { kind: "note", ts: `2026-10-05T06:00:1${i}Z`, letter: "Y", body: "Yは作業中" });
  }
  const post = new PostOffice({
    residentsRoot: root, workRoot, team: ["Holo", "Codex", "Claude"],
    tellMasterAfter: 3, sweepMs: 60_000, restMs: 60_000,
    workKeepMs: 60_000, limitWaitMs: 60_000, maxConcurrent: { Codex: 2 },
  });
  post.sweep(new Date("2026-10-05T06:05:00.000Z"));
  assert.deepEqual(readAll(root, "Codex").filter(l => l.kind === "tell").map(l => l.kind === "tell" && l.letter), ["X"]);
  post.stop();
});

test("HoloのB室の3回停滞はA室で進捗しても見え続け、B室は勝手に再起床しない", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-room-tell-"));
  append(root, "Holo", { kind: "letter", ts: "2026-10-05T06:00:00Z", id: "B", from: "Claude", to: "Holo", body: "B", work: "B" });
  append(root, "Holo", { kind: "letter", ts: "2026-10-05T06:00:00Z", id: "A", from: "Claude", to: "Holo", body: "A", work: "A" });
  for (let i = 1; i <= 3; i++) {
    append(root, "Holo", { kind: "wake", ts: `2026-10-05T06:00:0${i}.000Z`, letters: ["B"], how: "holo tab", work: "B" });
    append(root, "Holo", { kind: "read", ts: `2026-10-05T06:00:0${i}.100Z`, work: "B" });
  }
  const post = office(root);
  post.sweep(new Date("2026-10-05T06:05:00Z"));
  assert.deepEqual(readAll(root, "Holo").filter(l => l.kind === "tell").map(l => l.kind === "tell" && l.letter), ["B"]);
  append(root, "Holo", { kind: "note", ts: "2026-10-05T06:06:00Z", letter: "A", body: "A進捗" });
  append(root, "Holo", { kind: "done", ts: "2026-10-05T06:07:00Z", letter: "A" });
  const lines = readAll(root, "Holo");
  assert.equal(residentPostStatus("Holo", lines, false).stuck, 1);
  assert.equal(readAll(root, "Holo").filter(l => l.kind === "letter" && l.from === "郵便局" && !l.work).length, 1,
    "作業場のHoloの滞留を受付へ1回だけ知らせる");
  assert.deepEqual(toWake(scopeLines(lines, "B"), false, new Date("2026-10-05T06:10:00Z"), 60_000), []);
  post.sweep(new Date("2026-10-05T06:10:00Z"));
  assert.equal(residentPostStatus("Holo", readAll(root, "Holo"), false).state, "stuck");
  post.stop();
});

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
