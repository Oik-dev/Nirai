import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { HoloSeats, projectConversationUrl } from "./holo.ts";
import { append, MASTER, readAll, unfinished, waits } from "./letters.ts";

const t = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s));
const PROJECT_ID = "g-p-6ac239a30bc0819186c12150b8208fe0-nirai";
const CHAT = (n: number) => `https://chatgpt.com/g/${PROJECT_ID}/c/${String(n).repeat(8)}-1111-1111-1111-111111111111`;
const settings = {
  restMs: 60_000, busyLimitMs: 30 * 60_000, replyPath: /^\/backend-api\/(f\/)?conversation(?:\/resume)?$/,
  seats: 3, seatChars: 100, projectId: PROJECT_ID,
};
const since = t(0).toISOString();
const reply = (phase: "start" | "end" | "error", id = "r1", extra: object = {}) =>
  ({ phase, id, method: "POST", path: "/backend-api/f/conversation", seat: 1, since, ...extra });

/** 席1を作業場Wで開き、Wの手紙Aを置く */
function seated(url: string | null = CHAT(1)) {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-"));
  append(root, "Holo", { kind: "seat", ts: since, seat: 1, event: "open", work: "W", ...(url ? { url } : {}) });
  append(root, "Holo", { kind: "letter", ts: since, id: "A", from: "Codex", to: "Holo", body: "届いた", work: "W" });
  return { root, holo: new HoloSeats(root, settings) };
}
const kinds = (root: string, kind: string) => readAll(root, "Holo").filter(line => line.kind === kind);

test("返事の通信は、今の居場所の席から来たものだけを数える", () => {
  const { holo } = seated();
  assert.equal(holo.net(reply("start", "r1", { since: t(-1).toISOString() }), t(1)), false);
  assert.equal(holo.net({ ...reply("start"), seat: undefined }, t(1)), false);
  assert.equal(holo.net({ ...reply("start"), path: "/backend-api/me" }, t(1)), false);
  assert.equal(holo.net({ ...reply("start"), method: "GET" }, t(1)), false);
  assert.deepEqual([...holo.inflightSeats(t(1))], [], "閉じた席の古いタブ・席のないタブ・関係ない通信は数えない");
  holo.net(reply("start"), t(2));
  assert.deepEqual([...holo.inflightSeats(t(3))], [1]);
  assert.equal(holo.awake(t(3)), true);
});

test("郵便局が起こした返事が終わったら止まったと書き、Masterとの返事なら話したと書く", () => {
  const { root, holo } = seated();
  holo.net(reply("start", "talk"), t(1));
  assert.equal(holo.net(reply("end", "talk"), t(2)), true, "返事が終わったら拡張が次を取りに来る");
  assert.deepEqual(kinds(root, "stop"), [], "Masterと話しただけでは止まったと書かない");
  assert.equal(holo.seats()[0].lastTalk, t(2).toISOString());

  assert.equal(holo.sent({ ok: true, seat: 1, since, letters: ["A"] }, t(3)), true);
  holo.net(reply("start", "post"), t(4));
  holo.net(reply("error", "post", { error: "net::ERR_HTTP2_PROTOCOL_ERROR" }), t(5));
  assert.deepEqual(kinds(root, "stop").map(line => line.kind === "stop" && [line.work, line.seat, line.detail]),
    [["W", 1, "net::ERR_HTTP2_PROTOCOL_ERROR"]], "どう終わっても止まったと書き、終わり方を残す");
});

test("終わりの知らせが来なくても上限を過ぎたら止まったとみなし、新しい返事の始まりが古い通信を置き換える", () => {
  const { holo } = seated();
  holo.net(reply("start", "old"), t(0));
  assert.deepEqual([...holo.inflightSeats(t(30 * 60 + 1))], []);
  holo.net(reply("start", "lost"), t(2000));
  holo.net(reply("start", "new"), t(2001));
  assert.equal(holo.net(reply("end", "new"), t(2002)), true, "取りこぼした古い返事の終わりを待たない");
  assert.deepEqual([...holo.inflightSeats(t(2003))], []);
});

test("送れた一言だけを起こしたと書く。席が動いた・結ばれていない・ほかの作業場の手紙なら書かない", () => {
  const { root, holo } = seated();
  append(root, "Holo", { kind: "letter", ts: since, id: "X", from: "Codex", to: "Holo", body: "別", work: "other" });
  assert.equal(holo.sent({ ok: false, seat: 1, since, letters: ["A"] }, t(1)), true, "送れなかった知らせは受け取る");
  assert.equal(holo.sent({ ok: true, seat: 1, since: t(-5).toISOString(), letters: ["A"] }, t(1)), false);
  assert.equal(holo.sent({ ok: true, seat: 1, since, letters: ["A", "X"] }, t(1)), false);
  assert.equal(holo.sent({ ok: true, seat: 1, since, letters: ["NOPE"] }, t(1)), false);
  assert.equal(holo.sent({ ok: true, seat: 1, since, letters: [] }, t(1)), false);
  append(root, "Holo", { kind: "seat", ts: t(1).toISOString(), seat: 2, event: "open" });
  assert.equal(holo.sent({ ok: true, seat: 2, since: t(1).toISOString(), letters: ["X"] }, t(2)), false, "結ばれていない席へは起こさない");
  assert.deepEqual(kinds(root, "wake"), []);
  assert.equal(holo.sent({ ok: true, seat: 1, since, letters: ["A"] }, t(3)), true);
  assert.deepEqual(kinds(root, "wake").map(line => line.kind === "wake" && [line.letters, line.work, line.seat, line.how]),
    [[["A"], "W", 1, "holo tab"]]);
});

test("新しい会話へ送れたら、そのURLで席を結ぶ。Projectの会話だけを受け、ほかの席の会話とは重ねない", () => {
  const { root, holo } = seated(null);
  assert.equal(holo.sent({ ok: true, seat: 1, since, letters: ["A"], url: `${CHAT(2)}?model=x` }, t(1)), true);
  assert.equal(holo.seats()[0].url, CHAT(2), "余計な部分を落とす");
  assert.equal(holo.url(1, since, CHAT(3), t(2)), false, "1つの席に会話は1つ");

  append(root, "Holo", { kind: "seat", ts: t(3).toISOString(), seat: 2, event: "open" });
  const second = t(3).toISOString();
  assert.equal(holo.url(2, second, `https://chatgpt.com/g/${PROJECT_ID}/project`, t(4)), false, "入口は会話ではない");
  assert.equal(holo.url(2, second, "https://chatgpt.com/c/22222222-1111-1111-1111-111111111111", t(4)), false, "Projectの外の会話");
  assert.equal(holo.url(2, second, "https://chatgpt.com/g/g-p-other/c/22222222-1111-1111-1111-111111111111", t(4)), false, "別のProject");
  assert.equal(holo.url(2, second, CHAT(2), t(4)), false, "ほかの席の会話");
  assert.equal(holo.url(2, since, CHAT(4), t(4)), false, "前の居場所への知らせ");
  assert.equal(holo.url(2, second, CHAT(4), t(4)), true);
  assert.equal(projectConversationUrl("http://chatgpt.com/g/x/c/1", "x"), undefined);
});

test("Masterは小さい番号の空いた席から入り、満席なら入れない", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-enter-"));
  const holo = new HoloSeats(root, settings);
  append(root, "Holo", { kind: "seat", ts: t(0).toISOString(), seat: 2, event: "open", work: "W" });
  assert.deepEqual(holo.enter(t(1)), { seat: 1, since: t(1).toISOString() });
  assert.deepEqual(holo.enter(t(2)), { seat: 3, since: t(2).toISOString() });
  assert.equal(holo.enter(t(3)), undefined);
  assert.deepEqual(holo.seats().map(seat => [seat.seat, seat.work ?? null]), [[1, null], [2, "W"], [3, null]]);
});

test("Masterが席を空ける：入ったばかりならそのまま閉じ、結ばれていればHoloへ閉じてほしい手紙を1通だけ出す", () => {
  const { root, holo } = seated();
  assert.equal(holo.leave(2, t(1)), undefined, "空いた席");
  append(root, "Holo", { kind: "seat", ts: t(1).toISOString(), seat: 2, event: "open" });
  assert.deepEqual(holo.leave(2, t(2)), { closed: true });
  assert.equal(holo.seats()[1].since, undefined);

  const first = holo.leave(1, t(3));
  assert.ok(first && "letter" in first);
  assert.deepEqual(holo.leave(1, t(4)), first, "同じ居場所へ2通目を出さない");
  const [letter] = unfinished(readAll(root, "Holo")).filter(l => l.id === first.letter);
  assert.equal(letter.close, 1);
  assert.equal(letter.work, "W");
  assert.equal(letter.from, "郵便局");
  assert.equal(holo.seats()[0].work, "W", "Holoが引き継ぎを書くまで席は開いたまま");
});

test("席の字数は、会話が始まってから手がこの席へ返した出力だけを数え、作業場を移っても続けて数える", () => {
  const { root, holo } = seated();
  const dir = join(root, "Holo", "lifelog", "hands");
  mkdirSync(dir, { recursive: true });
  const row = (s: number, seat: number | undefined, output: string) =>
    JSON.stringify({ kind: "run", ts: t(s).toISOString(), ...(seat ? { seat } : {}), output });
  writeFileSync(join(dir, "2026-10-04.jsonl"), [
    row(-10, 1, "x".repeat(50)), row(1, 1, "abc"), row(2, 2, "zz"), row(3, undefined, "yy"), "{壊れた行", row(4, 1, "de"),
  ].join("\n") + "\n");
  assert.equal(holo.charsOf(holo.seats()[0]), 5, "前の会話・ほかの席・席のない実行・壊れた行は数えない");
  append(root, "Holo", { kind: "seat", ts: t(5).toISOString(), seat: 1, event: "close", work: "W", handover: "続き" });
  append(root, "Holo", { kind: "seat", ts: t(5).toISOString(), seat: 1, event: "open", work: "V", url: CHAT(1), started: since });
  assert.equal(holo.charsOf(holo.seats()[0]), 5, "同じ会話のまま移った席は続けて数える");
  writeFileSync(join(dir, "2026-10-04.jsonl"), [row(1, 1, "abc"), row(6, 1, "fgh")].join("\n") + "\n", { flag: "a" });
  assert.equal(holo.charsOf(holo.seats()[0]), 11, "追記された分だけ読み足す");
});

test("ポップアップの席の一覧：空き・入っただけ・結ばれた席の待ちと閉じる途中と問題", () => {
  const { root, holo } = seated();
  append(root, "Holo", { kind: "note", ts: t(1).toISOString(), letter: "A", body: "Masterに色を聞いた", waiting_for: MASTER });
  append(root, "Holo", { kind: "seat", ts: t(2).toISOString(), seat: 2, event: "open" });
  append(root, "Holo", { kind: "wake", ts: t(3).toISOString(), letters: ["A"], how: "holo tab", work: "W", seat: 2 });
  holo.leave(1, t(4));
  const status = holo.status(t(5), waits([readAll(root, "Holo")]));
  assert.deepEqual(status.map(seat => seat.state), ["bound", "entered", "empty"]);
  assert.deepEqual(status[0].waiting, [{ letter: "A", for: MASTER }]);
  assert.equal(status[0].masterWaiting, true);
  assert.equal(status[0].closing, true);
  assert.equal(status[0].lastNote, "Masterに色を聞いた");
  assert.equal(status[0].url, CHAT(1));
  assert.match(status[1].problem ?? "", /URLがまだ届いていない/);
  assert.equal(status[2].since, undefined);
});

test("郵便局が起き直しても席は生ログから同じに読め、続いていた通信だけを忘れる", () => {
  const { root, holo } = seated();
  holo.net(reply("start"), t(1));
  holo.handed(t(1));
  const again = new HoloSeats(root, settings);
  assert.deepEqual(again.seats(), holo.seats());
  assert.deepEqual([...again.inflightSeats(t(2))], []);
  assert.equal(again.awake(t(2)), false);
  assert.equal(again.net(reply("end"), t(3)), true, "始まりを知らない終わりも、返事の終わりとして受ける");
});
