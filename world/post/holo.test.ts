import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { HoloRoom } from "./holo.ts";
import { append, readAll } from "./letters.ts";

const t = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s));
const ROOM_URL = "https://chatgpt.com/c/11111111-1111-1111-1111-111111111111";
const NEW_ROOM_URL = "https://chatgpt.com/g/g-p-6ac239a30bc0819186c12150b8208fe0-nirai/project";
const settings = {
  restMs: 60_000,
  busyLimitMs: 30 * 60_000,
  replyPath: /^\/backend-api\/(f\/)?conversation(?:\/resume)?$/,
  roomChars: 100,
  newRoomUrl: NEW_ROOM_URL,
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
  assert.notEqual(retry?.text, after?.text, "未済件数が変われば起床文は変わる");
  assert.equal(retry?.roomMarker, after?.roomMarker, "引っ越し固有の印は未済件数が変わっても同じ");
});

test("room行がなければ、次の起床は新しい部屋を作るURLを返す", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-"));
  append(root, "Holo", { kind: "letter", ts: t(0).toISOString(), id: "A", from: "Codex", to: "Holo", body: "届いた" });
  const next = new HoloRoom(root, settings).next(t(1));
  assert.equal(next?.createRoom, true);
  assert.equal(next?.url, NEW_ROOM_URL);
  assert.deepEqual(next?.letters, ["A"]);
  assert.equal(next?.roomMarker, "[Nirai-room:A]");
});

test("新しい部屋はsentで会話URLを受け取れたときだけroom→wakeの順に書く", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-"));
  append(root, "Holo", { kind: "letter", ts: t(0).toISOString(), id: "A", from: "Codex", to: "Holo", body: "届いた" });
  const holo = new HoloRoom(root, settings);

  holo.sent({ ok: false, letters: ["A"], url: ROOM_URL, reason: "送れない" }, t(1));
  holo.sent({ ok: true, letters: ["A"] }, t(2));
  assert.deepEqual(readAll(root, "Holo").map(line => line.kind), ["letter"], "失敗とURLなしは何も書かない");

  holo.sent({ ok: true, letters: ["A"], url: ROOM_URL }, t(3));
  const lines = readAll(root, "Holo");
  assert.deepEqual(lines.slice(-2).map(line => line.kind), ["room", "wake"]);
  assert.equal(lines.at(-2)?.kind === "room" && lines.at(-2).url, ROOM_URL);
});

test("引っ越し後に旧roomと同じURLが返ったらroomもwakeも書かない", () => {
  const { root, holo } = room();
  holo.move(t(2));
  const move = readAll(root, "Holo").find(line => line.kind === "letter" && line.move);
  assert.ok(move && move.kind === "letter");
  append(root, "Holo", { kind: "done", ts: t(3).toISOString(), letter: move.id });
  const before = readAll(root, "Holo").length;
  holo.sent({ ok: true, letters: ["A"], url: ROOM_URL }, t(4));
  assert.equal(readAll(root, "Holo").length, before);
});
