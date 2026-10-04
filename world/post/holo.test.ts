import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { HoloRoom } from "./holo.ts";
import { append, readAll } from "./letters.ts";

const t = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s));
const settings = { restMs: 60_000, busyLimitMs: 30 * 60_000, replyPath: /^\/backend-api\/(f\/)?conversation(?:\/resume)?$/ };
const reply = (phase: "start" | "end" | "error", id = "r1", path = "/backend-api/f/conversation") => ({ phase, id, method: "POST", path });

function room() {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-"));
  append(root, "Holo", { kind: "letter", ts: t(0).toISOString(), id: "A", from: "Codex", to: "Holo", body: "届いた" });
  return { root, holo: new HoloRoom(root, settings) };
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
