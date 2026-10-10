import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { append, readAll, track, unfinished } from "./letters.ts";
import { PostOffice } from "./office.ts";
import { plan } from "./plan.ts";
import { residentPostStatus } from "./status.ts";
import { toTellMaster, toWake } from "./waker.ts";
import { Hands } from "./hands.ts";

const at = (s: number) => new Date(Date.UTC(2026, 9, 10, 0, 0, s)).toISOString();
const now = (s: number) => new Date(at(s));
const work = "stall-lab";
const HOLO = { seats: 3, seatChars: 300_000, seatIdleMs: 1_800_000, masterTurnMs: 600_000 };
const letter = (id = "SELF", scope = work) => ({
  kind: "letter" as const, ts: at(0), id, from: "Holo", to: "Holo", body: "続きの実装", ...(scope ? { work: scope } : {}),
});
function attempt(root: string, s: number, id = "SELF", scope = work, read = true) {
  const scoped = scope ? { work: scope } : {};
  append(root, "Holo", { kind: "wake", ts: at(s), letters: [id], how: "holo tab", ...scoped });
  if (read) append(root, "Holo", { kind: "read", ts: at(s + 1), ...scoped });
  append(root, "Holo", { kind: "stop", ts: at(s + 2), how: "exit", ...scoped });
}
function office(root: string) {
  return new PostOffice({ residentsRoot: root, workRoot: mkdtempSync(join(tmpdir(), "nirai-stall-work-")),
    team: ["Holo", "Codex", "Claude"], tellMasterAfter: 3, sweepMs: 60_000,
    restMs: 1000, workKeepMs: 60_000, limitWaitMs: 60_000, maxConcurrent: {}, holo: HOLO });
}

test("D1再現：自分宛てを3回読み進まなければ、拡張の印で一度だけ知らせて止め、Holoへ言付けを増やさない", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-stall-self-"));
  append(root, "Holo", letter());
  append(root, "Holo", { kind: "note", ts: at(3), letter: "SELF", body: "Chrome関門の担当からの回答を待つ" });
  for (const s of [10, 20, 30]) attempt(root, s);
  const post = office(root);
  post.sweep(now(40));
  post.sweep(now(41));
  const logs = readAll(root, "Holo");
  assert.equal(unfinished(track(logs, work))[0].deliveries, 3);
  assert.deepEqual(logs.filter(l => l.kind === "tell").map(l => l.kind === "tell" && [l.letter, l.how]), [["SELF", "拡張の印"]]);
  assert.equal(logs.filter(l => l.kind === "letter" && l.from === "郵便局").length, 0);
  assert.equal(residentPostStatus("Holo", logs, false).stuck, 1);
  assert.deepEqual(toWake(track(logs, work), false, now(1000), 1000), []);
  post.stop();
});

test("noteによる復帰と新しい手紙による復帰は筋を跨がず、次の滞留もまた一度だけ知らせる", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-stall-recover-"));
  append(root, "Holo", letter());
  for (const s of [10, 20, 30]) attempt(root, s);
  const post = office(root);
  post.sweep(now(40));
  append(root, "Holo", { kind: "letter", ts: at(42), id: "NEW", from: "Claude", to: "Holo", body: "次の知らせ", work });
  assert.deepEqual(toWake(track(readAll(root, "Holo"), work), false, now(50), 1000), ["NEW"]);
  append(root, "Holo", { kind: "note", ts: at(60), letter: "SELF", body: "進んだ" });
  assert.equal(residentPostStatus("Holo", readAll(root, "Holo"), false).stuck, 0);
  assert.deepEqual(toWake(track(readAll(root, "Holo"), work), false, now(65), 1000), ["SELF", "NEW"]);
  for (const s of [70, 80, 90]) attempt(root, s);
  post.sweep(now(100));
  assert.equal(readAll(root, "Holo").filter(l => l.kind === "tell" && l.letter === "SELF").length, 2);
  post.stop();
});

test("不通は3回の未読で判定し、tellは出さず15分後に再送し、読めば通常へ戻る", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-stall-unreachable-"));
  append(root, "Holo", letter());
  for (const s of [10, 20, 30]) attempt(root, s, "SELF", work, false);
  const post = office(root);
  post.sweep(now(40));
  const lines = track(readAll(root, "Holo"), work);
  assert.equal(unfinished(lines)[0].deliveries, 0);
  assert.deepEqual(toTellMaster(lines, 3), []);
  assert.equal(readAll(root, "Holo").filter(l => l.kind === "tell" || l.kind === "letter" && l.from === "郵便局").length, 0);
  assert.equal(residentPostStatus("Holo", readAll(root, "Holo"), false).unreachable, 1);
  assert.deepEqual(toWake(lines, false, now(900), 1000), []);
  assert.deepEqual(toWake(lines, false, now(1000), 1000), ["SELF"]);
  append(root, "Holo", { kind: "wake", ts: at(1000), letters: ["SELF"], how: "holo tab", work });
  assert.equal(residentPostStatus("Holo", readAll(root, "Holo"), true).unreachable, 1,
    "再送中に不通の警告は消えない");
  append(root, "Holo", { kind: "read", ts: at(1001), work });
  assert.equal(residentPostStatus("Holo", readAll(root, "Holo"), true).unreachable, 0,
    "再送が読めた時点で不通が解ける");
  append(root, "Holo", { kind: "stop", ts: at(1002), how: "exit", work });
  assert.equal(residentPostStatus("Holo", readAll(root, "Holo"), false).unreachable, 0);
  assert.deepEqual(toWake(track(readAll(root, "Holo"), work), false, now(1004), 1000), ["SELF"]);
  post.stop();
});

test("毎回noteする長期作業は10回起きても滞留しない。作業場のない古い手紙の滞留も言付けを増やさない", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-stall-progress-"));
  append(root, "Holo", letter());
  for (let i = 0; i < 10; i++) {
    attempt(root, 10 + 10 * i);
    append(root, "Holo", { kind: "note", ts: at(13 + 10 * i), letter: "SELF", body: `作業${i}` });
  }
  assert.deepEqual(toTellMaster(track(readAll(root, "Holo"), work), 3), []);
  append(root, "Holo", letter("RECEPTION", ""));
  for (const s of [120, 130, 140]) attempt(root, s, "RECEPTION", "");
  const post = office(root);
  post.sweep(now(150));
  assert.equal(readAll(root, "Holo").filter(l => l.kind === "tell" && l.letter === "RECEPTION").length, 1);
  assert.equal(readAll(root, "Holo").filter(l => l.kind === "letter" && l.from === "郵便局").length, 0);
  post.stop();
});

test("長いコマンドの結果を待つ席には一言を送らず、別の席には届く", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-stall-hand-"));
  append(root, "Holo", letter());
  append(root, "Holo", { ...letter("SECOND", "another-room"), ts: at(1) });
  append(root, "Holo", { kind: "seat", ts: at(1), seat: 1, event: "open", work });
  append(root, "Holo", { kind: "seat", ts: at(1), seat: 2, event: "open", work: "another-room" });
  const offered = (awaiting: string[]) => plan(
    { team: ["Holo"], restMs: 1000, tellMasterAfter: 3, workKeepMs: 60_000, maxConcurrent: {}, holo: HOLO },
    { now: now(2), lines: { Holo: readAll(root, "Holo") }, folders: [], cli: {}, holo: { inflight: new Set(), chars: () => 0 },
      handsBusy: new Set(), handsAwaiting: new Set(awaiting), reloadWaiting: false },
  ).offers.map(offer => offer.work);
  assert.deepEqual(offered([work]), ["another-room"]);
  assert.deepEqual(offered([work, "another-room"]), []);
  assert.deepEqual(offered([]), [work, "another-room"]);
});

test("手の実行場所と結果の宛先が違っても、結果を待つ印は呼んだ席の作業場に付く", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-stall-hands-root-"));
  const workRoot = mkdtempSync(join(tmpdir(), "nirai-stall-hands-work-"));
  mkdirSync(join(workRoot, "source"));
  let notified!: () => void;
  const delivered = new Promise<void>(resolve => { notified = resolve; });
  const hands = new Hands(root, workRoot, { waitMs: 10, limitMs: 10_000 }, () => notified());
  const answer = await hands.run("Holo", "source", "Start-Sleep -Milliseconds 400", { seat: 1, work });
  assert.match(answer, /まだ続いている/);
  assert.deepEqual([...hands.busy()], ["source"]);
  assert.deepEqual([...hands.awaiting()], [work]);
  await delivered;
  await new Promise<void>(resolve => setImmediate(resolve));
  assert.deepEqual([...hands.awaiting()], []);
  const result = readAll(root, "Holo").find(line => line.kind === "letter");
  assert.equal(result?.kind === "letter" && result.work, work);
});

function thirdReadRunning() {
  const root = mkdtempSync(join(tmpdir(), "nirai-stall-limit-review-"));
  const who = "Codex";
  append(root, who, { kind: "letter", id: "SELF", ts: at(0), from: who, to: who, body: "長い作業", work });
  const wake = (s: number) => append(root, who, { kind: "wake", ts: at(s), letters: ["SELF"], how: "codex cli", work });
  const read = (s: number) => append(root, who, { kind: "read", ts: at(s), work });
  const stop = (s: number, how: "exit" | "limit") => append(root, who,
    { kind: "stop", ts: at(s), how, work, ...(how === "limit" ? { until: at(180) } : {}) });
  for (const s of [10, 20]) { wake(s); read(s + 1); stop(s + 2, "exit"); }
  wake(30); read(31);
  return { root, stop };
}

test("P1：3回目が作業中なら滞留を確定せず、後でlimitになっても上限明けに再開する", () => {
  const { root, stop } = thirdReadRunning();
  const post = office(root);
  const inProgress = track(readAll(root, "Codex"), work);
  assert.equal(unfinished(inProgress)[0].deliveries, 3, "読めた回数は表示できる");
  assert.deepEqual(toTellMaster(inProgress, 3), [], "未終了のreadではtellを確定しない");
  post.sweep(now(31));
  assert.equal(readAll(root, "Codex").filter(l => l.kind === "tell").length, 0);
  stop(32, "limit");
  post.sweep(now(40));
  const afterLimit = track(readAll(root, "Codex"), work);
  assert.equal(unfinished(afterLimit)[0].deliveries, 2, "上限で終わった回は数えない");
  assert.deepEqual(toTellMaster(afterLimit, 3), []);
  assert.equal(readAll(root, "Codex").filter(l => l.kind === "tell").length, 0);
  assert.equal(residentPostStatus("Codex", readAll(root, "Codex"), false, now(50)).stuck, 0);
  assert.deepEqual(toWake(afterLimit, false, now(100), 1000), [], "上限の間は起こさない");
  assert.deepEqual(toWake(afterLimit, false, now(181), 1000), ["SELF"], "上限明けは自動で続く");
  post.stop();
});

test("P1：3回目の非limit終了後は一度だけtellし、作業中のnoteでは早すぎる通知を出さない", () => {
  const ended = thirdReadRunning();
  const post = office(ended.root);
  post.sweep(now(31));
  assert.equal(readAll(ended.root, "Codex").filter(l => l.kind === "tell").length, 0);
  ended.stop(32, "exit");
  post.sweep(now(40));
  post.sweep(now(45));
  assert.equal(readAll(ended.root, "Codex").filter(l => l.kind === "tell").length, 1);
  assert.equal(readAll(ended.root, "Holo").filter(l => l.kind === "letter" && l.from === "郵便局").length, 1);
  post.stop();

  const advancing = thirdReadRunning();
  const postAdvancing = office(advancing.root);
  postAdvancing.sweep(now(31));
  append(advancing.root, "Codex", { kind: "note", ts: at(32), letter: "SELF", body: "進捗あり" });
  postAdvancing.sweep(now(33));
  advancing.stop(34, "exit");
  postAdvancing.sweep(now(40));
  assert.equal(readAll(advancing.root, "Codex").filter(l => l.kind === "tell").length, 0);
  assert.equal(readAll(advancing.root, "Holo").filter(l => l.kind === "letter" && l.from === "郵便局").length, 0);
  postAdvancing.stop();
});

test("P2：不通後のreadは、終了がlimitでも到達の証拠として保持する", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-stall-limit-read-"));
  append(root, "Holo", letter());
  for (const s of [10, 20, 30]) attempt(root, s, "SELF", work, false);
  assert.equal(residentPostStatus("Holo", readAll(root, "Holo"), false).unreachable, 1);
  append(root, "Holo", { kind: "wake", ts: at(1000), letters: ["SELF"], how: "holo tab", work });
  append(root, "Holo", { kind: "read", ts: at(1001), work });
  assert.equal(residentPostStatus("Holo", readAll(root, "Holo"), true).unreachable, 0);
  append(root, "Holo", { kind: "stop", ts: at(1002), how: "limit", work, until: at(1100) });
  const after = track(readAll(root, "Holo"), work);
  assert.equal(unfinished(after)[0].deliveries, 0, "limitで止まった回は滞留回数に数えない");
  assert.equal(residentPostStatus("Holo", readAll(root, "Holo"), false).unreachable, 0);
  assert.deepEqual(toWake(after, false, now(1101), 1000), ["SELF"], "回復済みなら15分待ちに戻らない");
});
