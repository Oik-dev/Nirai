import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { HoloRoom } from "./holo.ts";
import { append, readAll } from "./letters.ts";

const t = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s));
const PROJECT_ID = "g-p-6ac239a30bc0819186c12150b8208fe0-nirai";
const ROOM_URL = `https://chatgpt.com/g/${PROJECT_ID}/c/11111111-1111-1111-1111-111111111111`;
const NEW_ROOM_URL = `https://chatgpt.com/g/${PROJECT_ID}/project`;
const settings = {
  restMs: 60_000,
  busyLimitMs: 30 * 60_000,
  replyPath: /^\/backend-api\/(f\/)?conversation(?:\/resume)?$/,
  roomChars: 100,
  projectId: PROJECT_ID,
};
const reply = (phase: "start" | "end" | "error", id = "r1", path = "/backend-api/f/conversation") => ({ phase, id, method: "POST", path });

function room() {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-"));
  append(root, "Holo", { kind: "room", ts: t(0).toISOString(), url: ROOM_URL });
  append(root, "Holo", { kind: "letter", ts: t(0).toISOString(), id: "A", from: "Codex", to: "Holo", body: "届いた" });
  return { root, holo: new HoloRoom(root, settings) };
}

function hands(root: string, rows: { ts: Date; output: string }[]) {
  const dir = join(root, "Holo", "lifelog", "hands");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "2026-10-04.jsonl"), rows.map(row => JSON.stringify({ kind: "run", ts: row.ts.toISOString(), output: row.output })).join("\n") + "\n");
}

test("手紙があって、返事の最中でなければ、起こす一言を渡す", () => {
  const { holo } = room();
  const next = holo.next(t(1));
  assert.deepEqual(next?.letters, ["A"]);
  assert.match(next?.text ?? "", /read_mailbox/);
  assert.equal(holo.awake(t(2)), true, "一言を渡した時点からrestMsは起きている");
  assert.equal(holo.next(t(2)), undefined, "送信確認前でも二重に渡さない");
});

test("返事の通信が続いている間は起こさない", () => {
  const { holo } = room();
  holo.net(reply("start"), t(1));
  assert.equal(holo.next(t(2)), undefined);
});

test("errorの直後にresumeしても、最後のresumeが終わってからrestMsの間は忙しい", () => {
  const { holo } = room();
  holo.net(reply("start", "conversation"), t(1));
  holo.net(reply("error", "conversation"), t(2));
  assert.equal(holo.awake(t(3)), true, "error直後の休み");
  holo.net(reply("start", "resume", "/backend-api/f/conversation/resume"), t(4));
  assert.equal(holo.awake(t(65)), true, "元のerror後restを越えてもresume中");
  holo.net(reply("end", "resume", "/backend-api/f/conversation/resume"), t(90));
  assert.equal(holo.awake(t(91)), true, "resume終了直後");
  assert.equal(holo.awake(t(151)), false, "最後の終了からrestMsを過ぎたら暇");
});

test("Masterとの会話中と、その最後の通信が終わってrestMsの間も起こさない", () => {
  const { holo } = room();
  holo.net(reply("start", "master"), t(1));
  assert.equal(holo.next(t(2)), undefined);
  holo.net(reply("end", "master"), t(5));
  assert.equal(holo.next(t(6)), undefined, "会話直後");
  assert.deepEqual(holo.next(t(66))?.letters, ["A"], "最後の通信からrestMsを過ぎたら起こせる");
});

test("返事と関係ない通信は数えない", () => {
  const { holo } = room();
  holo.net({ phase: "start", id: "x", method: "POST", path: "/backend-api/lat/r" }, t(1));
  holo.net({ phase: "start", id: "y", method: "GET", path: "/backend-api/f/conversation" }, t(1));
  assert.equal(holo.awake(t(2)), false);
});

test("起こしたあとの返事が終わったら、どう終わっても止まったと書き、終わり方を残す", () => {
  const { root, holo } = room();
  holo.sent({ ok: true, letters: ["A"] }, t(1));
  holo.net(reply("start"), t(2));
  holo.net({ ...reply("error"), error: "net::ERR_FAILED" }, t(1500));
  const last = readAll(root, "Holo").at(-1);
  assert.equal(last?.kind, "stop");
  assert.equal(last?.kind === "stop" && last.detail, "net::ERR_FAILED");
});

test("Masterと話しただけの返事では、止まったと書かない", () => {
  const { root, holo } = room();
  holo.net(reply("start"), t(1));
  holo.net(reply("end"), t(5));
  assert.equal(readAll(root, "Holo").some(l => l.kind === "stop"), false);
});

test("送れなかった一言は、起こしたことにしない", () => {
  const { root, holo } = room();
  holo.sent({ ok: false, letters: ["A"], reason: "draft" }, t(1));
  assert.equal(readAll(root, "Holo").some(l => l.kind === "wake"), false);
  assert.deepEqual(holo.next(t(2))?.letters, ["A"]);
});

test("終わりの知らせが来なくても、上限を過ぎたら止まったとみなす", () => {
  const { holo } = room();
  holo.net(reply("start"), t(0));
  assert.equal(holo.awake(t(60)), true);
  assert.equal(holo.awake(new Date(t(0).getTime() + 31 * 60_000)), false);
});

test("古い返事の終了通知を取りこぼしても、新しい返事が終われば暇になる", () => {
  const { holo } = room();
  holo.net(reply("start", "old"), t(0));
  holo.net(reply("start", "new"), t(120));
  holo.net(reply("end", "new"), t(125));
  assert.equal(holo.awake(t(126)), true, "新しい返事の直後は休む");
  assert.equal(holo.awake(t(186)), false, "古い通信を30分抱えず、新しい返事基準で暇になる");
});

test("部屋の長さは最後のroom行より後の手のoutputだけを数え、超えたらそのroomに1通だけ引っ越し手紙を出す", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-"));
  append(root, "Holo", { kind: "room", ts: t(0).toISOString(), url: "https://chatgpt.com/c/00000000-0000-0000-0000-000000000000" });
  append(root, "Holo", { kind: "room", ts: t(10).toISOString(), url: ROOM_URL });
  hands(root, [
    { ts: t(5), output: "x".repeat(500) },
    { ts: t(11), output: "a".repeat(60) },
    { ts: t(12), output: "b".repeat(50) },
  ]);
  const holo = new HoloRoom(root, settings);

  assert.equal(holo.status().chars, 110, "前のroomのぶんは数えない");
  const first = holo.next(t(20));
  const moves = readAll(root, "Holo").filter(line => line.kind === "letter" && line.move);
  assert.equal(moves.length, 1);
  assert.deepEqual(first?.letters, [moves[0].id]);
  assert.equal(first?.createRoom, false);
  assert.equal(first?.url, ROOM_URL);

  holo.next(t(21));
  assert.equal(readAll(root, "Holo").filter(line => line.kind === "letter" && line.move).length, 1, "同じroomでは増やさない");
});

test("引っ越し手紙が未済の間は前の部屋でその手紙だけを進め、済んだら次の起床は新しい部屋", () => {
  const { root, holo } = room();
  assert.equal(holo.move(t(2)), true);
  assert.equal(holo.move(t(2)), false, "同じroomに2通は出さない");
  const move = readAll(root, "Holo").find(line => line.kind === "letter" && line.move);
  assert.ok(move && move.kind === "letter");

  const before = holo.next(t(3));
  assert.deepEqual(before?.letters, [move.id], "ほかの未済手紙Aは前の部屋で進めない");
  assert.equal(before?.url, ROOM_URL);
  assert.equal(before?.createRoom, false);

  append(root, "Holo", { kind: "done", ts: t(4).toISOString(), letter: move.id });
  append(root, "Holo", { kind: "letter", ts: t(5).toISOString(), id: "CONT", from: "Holo", to: "Holo", body: "続き" });
  assert.equal(holo.next(t(6)), undefined, "前の部屋へ一言を渡した直後は、新旧を同時に起こさない");
  const after = holo.next(t(64));
  assert.equal(after?.createRoom, true);
  assert.equal(after?.url, NEW_ROOM_URL);
  assert.deepEqual(after?.letters, ["A", "CONT"]);
  assert.equal(after?.roomMarker, `[Nirai-room:${move.id}]`);

  append(root, "Holo", { kind: "letter", ts: t(65).toISOString(), id: "LATER", from: "Codex", to: "Holo", body: "あとから届いた" });
  const retry = new HoloRoom(root, settings).next(t(66));
  assert.equal(retry?.roomMarker, after?.roomMarker, "引っ越し固有の印は未済件数が変わっても同じ");
});

test("room行がなければ、既存会話を勝手に新しい部屋として扱わない", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-"));
  append(root, "Holo", { kind: "letter", ts: t(0).toISOString(), id: "A", from: "Codex", to: "Holo", body: "届いた" });
  const holo = new HoloRoom(root, settings);
  assert.equal(holo.next(t(1)), undefined);
  assert.equal(holo.status().url, undefined);
});

test("初回登録はNirai Projectの会話だけをroomにできる", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-"));
  append(root, "Holo", { kind: "letter", ts: t(0).toISOString(), id: "A", from: "Codex", to: "Holo", body: "届いた" });
  const holo = new HoloRoom(root, settings);

  assert.equal(holo.register("https://example.com/c/not-chatgpt", t(1)), false);
  assert.equal(holo.register("https://chatgpt.com/c/22222222-2222-2222-2222-222222222222", t(1)), false, "Project外は不可");
  assert.equal(holo.register(`https://chatgpt.com/g/other-project/c/22222222-2222-2222-2222-222222222222`, t(1)), false, "別Projectは不可");
  assert.equal(holo.register(ROOM_URL + "/", t(2)), true);
  assert.equal(holo.register(`https://chatgpt.com/g/${PROJECT_ID}/c/22222222-2222-2222-2222-222222222222`, t(3)), false, "S1では登録し直さない");
  assert.equal(holo.status().url, ROOM_URL);
  assert.equal(holo.status().state, "ready");
  assert.equal(holo.move(t(4)), true, "登録後は既存の引っ越し経路へ流せる");
});

test("move済みのS3では、同じProjectの別会話を手動で新roomとして登録できる", () => {
  const { root, holo } = room();
  holo.move(t(2));
  const move = readAll(root, "Holo").find(line => line.kind === "letter" && line.move);
  assert.ok(move && move.kind === "letter");
  append(root, "Holo", { kind: "done", ts: t(3).toISOString(), letter: move.id });
  assert.equal(holo.status().state, "new-room");

  const nextUrl = `https://chatgpt.com/g/${PROJECT_ID}/c/22222222-2222-2222-2222-222222222222`;
  assert.equal(holo.register(ROOM_URL, t(4)), false, "旧部屋そのものは新部屋にしない");
  assert.equal(holo.register(nextUrl, t(5)), true);
  assert.equal(holo.status().state, "ready");
  assert.equal(holo.status().url, nextUrl);
});

test("新部屋作成で画面に触れてから失敗したら自動を止め、Masterの再試行でだけ再開する", () => {
  const { root, holo } = room();
  holo.move(t(2));
  const move = readAll(root, "Holo").find(line => line.kind === "letter" && line.move);
  assert.ok(move && move.kind === "letter");
  append(root, "Holo", { kind: "done", ts: t(3).toISOString(), letter: move.id });
  append(root, "Holo", { kind: "letter", ts: t(4).toISOString(), id: "CONT", from: "Holo", to: "Holo", body: "続き" });

  const first = holo.next(t(65));
  assert.equal(first?.createRoom, true);
  holo.sent({ ok: false, letters: first?.letters ?? [], reason: "入力後に失敗", touched: true }, t(66));
  assert.equal(holo.status().failure, "入力後に失敗");
  assert.equal(holo.next(t(130)), undefined, "自動では再試行しない");

  holo.retryRoom();
  assert.equal(holo.status().failure, undefined);
  assert.equal(holo.next(t(131))?.createRoom, true);
});

test("新部屋作成が画面に触れる前に失敗しただけなら次の見直しで再試行できる", () => {
  const { root, holo } = room();
  holo.move(t(2));
  const move = readAll(root, "Holo").find(line => line.kind === "letter" && line.move);
  assert.ok(move && move.kind === "letter");
  append(root, "Holo", { kind: "done", ts: t(3).toISOString(), letter: move.id });
  append(root, "Holo", { kind: "letter", ts: t(4).toISOString(), id: "CONT", from: "Holo", to: "Holo", body: "続き" });
  const first = holo.next(t(65));
  holo.sent({ ok: false, letters: first?.letters ?? [], reason: "まだ入力欄がない", touched: false }, t(66));
  assert.equal(holo.status().failure, undefined);
  assert.equal(holo.next(t(130))?.createRoom, true);
});

test("新部屋へ送れたらURL未確定でも同じ引っ越しで二つ目を作らない", () => {
  const { root, holo } = room();
  holo.move(t(2));
  const move = readAll(root, "Holo").find(line => line.kind === "letter" && line.move);
  assert.ok(move && move.kind === "letter");
  append(root, "Holo", { kind: "done", ts: t(3).toISOString(), letter: move.id });
  append(root, "Holo", { kind: "letter", ts: t(4).toISOString(), id: "CONT", from: "Holo", to: "Holo", body: "続き" });

  const first = holo.next(t(65));
  assert.equal(first?.createRoom, true);
  holo.sent({ ok: true, letters: first?.letters ?? [] }, t(66));
  assert.match(holo.status().failure ?? "", /送信済み/);
  assert.equal(holo.next(t(130)), undefined, "同じプロセスでは作り直さない");

  const restarted = new HoloRoom(root, settings);
  assert.equal(restarted.next(t(131)), undefined, "郵便局を起こし直しても作り直さない");
  assert.match(restarted.status().failure ?? "", /送信済み/);

  append(root, "Holo", { kind: "letter", ts: t(132).toISOString(), id: "MORE", from: "Claude", to: "Holo", body: "追加" });
  assert.equal(restarted.next(t(200)), undefined, "新しい手紙が来ても未確定のまま二部屋目を作らない");

  const nextUrl = `https://chatgpt.com/g/${PROJECT_ID}/c/22222222-2222-2222-2222-222222222222`;
  assert.equal(restarted.register(nextUrl, t(201)), true);
  assert.equal(restarted.status().state, "ready");
  assert.equal(restarted.status().failure, undefined);
});

test("room未登録のsentはroomもwakeも作らない", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-"));
  append(root, "Holo", { kind: "letter", ts: t(0).toISOString(), id: "A", from: "Codex", to: "Holo", body: "届いた" });
  const holo = new HoloRoom(root, settings);

  holo.sent({ ok: true, letters: ["A"], url: ROOM_URL }, t(1));
  assert.deepEqual(readAll(root, "Holo").map(line => line.kind), ["letter"]);
});

test("引っ越し後に旧roomと同じURLが返っても送信事実は残し、二つ目を作らない", () => {
  const { root, holo } = room();
  holo.move(t(2));
  const move = readAll(root, "Holo").find(line => line.kind === "letter" && line.move);
  assert.ok(move && move.kind === "letter");
  append(root, "Holo", { kind: "done", ts: t(3).toISOString(), letter: move.id });
  const before = readAll(root, "Holo").length;
  holo.sent({ ok: true, letters: ["A"], url: ROOM_URL }, t(4));
  assert.equal(readAll(root, "Holo").length, before + 1);
  assert.equal(readAll(root, "Holo").at(-1)?.kind, "wake");
  assert.match(holo.status().failure ?? "", /送信済み/);
  assert.equal(holo.next(t(70)), undefined);
});
