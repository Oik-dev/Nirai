import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { HoloSeats } from "./holo.ts";
import { append, type Line, readAll } from "./letters.ts";
import { PostOffice } from "./office.ts";
import { plan, type PlanInput, type PlanSettings } from "./plan.ts";
import { seatWakeText } from "./waker.ts";

const at = (s: number) => new Date(Date.UTC(2026, 9, 10, 0, 0, s)).toISOString();
const MIN = 60;
const SETTINGS: PlanSettings = {
  team: ["Holo", "Codex"], restMs: 60_000, tellMasterAfter: 3, workKeepMs: 60_000, maxConcurrent: { Codex: 1 },
  holo: { seats: 3, seatChars: 1000, seatIdleMs: 30 * 60_000, masterTurnMs: 10 * 60_000 },
};
const URL = "https://chatgpt.com/g/g-p-test/c/11111111-1111-1111-1111-111111111111";
const letter = (id: string, work: string, s = 0, extra: object = {}): Line =>
  ({ kind: "letter", ts: at(s), id, from: "Claude", to: "Holo", body: id, work, ...extra }) as Line;
const open = (seat: number, s: number, extra: object = {}): Line => ({ kind: "seat", ts: at(s), seat, event: "open", ...extra }) as Line;

function decide(holo: Line[], s: number, input: Partial<PlanInput> = {}, codex: Line[] = []) {
  return plan(SETTINGS, {
    now: new Date(at(s)), lines: { Holo: holo, Codex: codex }, folders: [], cli: { Codex: new Set() },
    holo: { inflight: new Set(), chars: () => 0 }, handsBusy: new Set(), handsAwaiting: new Set(), reloadWaiting: false, ...input,
  });
}

test("起こせる手紙のある作業場へ小さい番号の空いた席を開き、席が尽きた作業場は待つ", () => {
  const holo = ["w1", "w2", "w3", "w4"].map((work, i) => letter(`L${i}`, work));
  assert.deepEqual(decide(holo, 1).seatOpens, [{ seat: 1, work: "w1" }, { seat: 2, work: "w2" }, { seat: 3, work: "w3" }]);
  assert.deepEqual(decide([...holo, open(2, 0)], 1).seatOpens, [{ seat: 1, work: "w1" }, { seat: 3, work: "w2" }],
    "Masterの入った席は使わない");
  assert.deepEqual(decide([...holo, open(1, 0, { work: "W1" })], 1).seatOpens.map(o => o.work), ["w2", "w3"],
    "1つの作業場に席は1つ（大文字小文字は同じ作業場）");
  assert.deepEqual(decide([letter("A", "w1"), { kind: "done", ts: at(1), letter: "A" }], 2).seatOpens, [], "済んだ作業場には開かない");
});

test("開いた席へ一言を渡す。会話がなければ新しい会話で、Masterの番と返事の最中は渡さない", () => {
  const holo = [letter("A", "w1"), open(1, 1, { work: "w1" })];
  assert.deepEqual(decide(holo, 2).offers, [{ seat: 1, since: at(1), work: "w1", letters: ["A"], text: seatWakeText(1, "w1") }]);
  assert.equal(decide([letter("A", "w1"), open(1, 1, { work: "w1", url: URL })], 2).offers[0].url, URL);
  const talked = [...holo, { kind: "seat", ts: at(100), seat: 1, event: "talk" } as Line];
  assert.deepEqual(decide(talked, 100 + 10 * MIN - 1).offers, [], "Masterと話した後の10分は、Masterの番");
  assert.equal(decide(talked, 100 + 10 * MIN).offers.length, 1);
  assert.deepEqual(decide(holo, 2, { holo: { inflight: new Set([1]), chars: () => 0 } }).offers, [], "返事の最中");
});

test("決めるだけで何も書かず、渡された生ログも変えない", () => {
  const stuck = [letter("A", "w1")];
  for (const s of [1, 2, 3]) {
    stuck.push({ kind: "wake", ts: at(s * 100), letters: ["A"], how: "holo tab", work: "w1", seat: 1 } as Line);
    stuck.push({ kind: "read", ts: at(s * 100 + 1), work: "w1", seat: 1 } as Line);
    stuck.push({ kind: "stop", ts: at(s * 100 + 2), how: "exit", work: "w1", seat: 1 } as Line);
  }
  const before = JSON.stringify(stuck);
  const decided = decide(stuck, 400);
  assert.deepEqual(decided.tells.map(tell => tell.letter.id), ["A"]);
  assert.deepEqual(decided.seatOpens, [], "Masterに回した手紙では席も開かない");
  assert.equal(JSON.stringify(stuck), before);
});

test("待っている手紙では席を開かず、待つ相手が済んだら開く", () => {
  const holo = [letter("A", "w1"), { kind: "note", ts: at(1), letter: "A", body: "Codexのレビュー待ち", waiting_for: "B" } as Line];
  const codex: Line[] = [{ kind: "letter", ts: at(1), id: "B", from: "Holo", to: "Codex", body: "見て", work: "w1" }];
  assert.deepEqual(decide(holo, 2, {}, codex).seatOpens, []);
  codex.push({ kind: "done", ts: at(3), letter: "B" });
  assert.deepEqual(decide(holo, 4, {}, codex).seatOpens, [{ seat: 1, work: "w1" }]);
});

test("動きのない席：入っただけなら閉じ、結ばれていれば閉じてほしい手紙を出し、その間はその手紙だけを渡す", () => {
  assert.deepEqual(decide([open(1, 0)], 30 * MIN - 1).seatCloses, []);
  assert.deepEqual(decide([open(1, 0)], 30 * MIN).seatCloses, [1]);

  const done = [letter("A", "w1"), open(1, 0, { work: "w1", url: URL }), { kind: "done", ts: at(5), letter: "A" } as Line];
  assert.deepEqual(decide(done, 30 * MIN - 1).closeLetters, []);
  assert.deepEqual(decide(done, 30 * MIN).closeLetters, [{ seat: 1, work: "w1", why: "idle" }]);
  assert.deepEqual(decide(done, 5 + 30 * MIN, { handsBusy: new Set(["w1"]) }).closeLetters, [], "手が動いている間は閉じない");

  const closing = [letter("A", "w1"), open(1, 0, { work: "w1", url: URL }),
    letter("CLOSE", "w1", 10, { from: "郵便局", close: 1 })];
  const decided = decide(closing, 11);
  assert.deepEqual(decided.offers.map(offer => offer.letters), [["CLOSE"]]);
  assert.deepEqual(decided.closeLetters, [], "閉じてほしい手紙は1通だけ");
});

test("会話が長くなった席には、閉じてほしい手紙を出す", () => {
  const holo = [letter("A", "w1"), open(1, 0, { work: "w1", url: URL })];
  assert.deepEqual(decide(holo, 1, { holo: { inflight: new Set(), chars: () => 1000 } }).closeLetters, []);
  assert.deepEqual(decide(holo, 1, { holo: { inflight: new Set(), chars: () => 1001 } }).closeLetters, [{ seat: 1, work: "w1", why: "full" }]);
});

test("新しい会話へ送ったのにURLが分からない席には二度と送らず、静かになったら閉じて次の見回りで開き直す", () => {
  const holo = [letter("A", "w1"), open(1, 0, { work: "w1" }),
    { kind: "wake", ts: at(1), letters: ["A"], how: "holo tab", work: "w1", seat: 1 } as Line];
  const later = decide(holo, 5 * MIN);
  assert.deepEqual([later.offers, later.seatCloses, later.seatOpens], [[], [], []]);
  const quiet = decide(holo, 1 + 30 * MIN);
  assert.deepEqual([quiet.seatCloses, quiet.seatOpens], [[1], []], "閉じる見回りでは同じ作業場を開き直さない");
  holo.push({ kind: "seat", ts: at(1 + 30 * MIN), seat: 1, event: "close" } as Line);
  assert.deepEqual(decide(holo, 2 + 30 * MIN).seatOpens, [{ seat: 1, work: "w1" }]);
});

test("版替えを待つ間は、一言も渡さず、席も開かず、CLIも起こさない", () => {
  const holo = [letter("A", "w1"), open(1, 0, { work: "w1" }), letter("B", "w2")];
  const codex: Line[] = [{ kind: "letter", ts: at(0), id: "C", from: "Holo", to: "Codex", body: "見て", work: "w1" }];
  assert.equal(decide(holo, 1, {}, codex).wakes.length, 1);
  const waiting = decide(holo, 1, { reloadWaiting: true }, codex);
  assert.deepEqual([waiting.offers, waiting.seatOpens, waiting.wakes], [[], [], []]);
});

test("拡張が取りに来ても郵便局は何も書かず、見回りが席を開く。起き直しても同じ席を使う", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-plan-office-"));
  append(root, "Holo", letter("A", "w1"));
  const seats = () => new HoloSeats(root, { restMs: 60_000, busyLimitMs: 30 * 60_000, replyPath: /^\/backend-api\/conversation$/,
    seats: 3, seatChars: 1000, projectId: "g-p-test" });
  const office = (holo: HoloSeats) => new PostOffice({ ...SETTINGS, team: ["Holo"], residentsRoot: root,
    workRoot: mkdtempSync(join(tmpdir(), "nirai-plan-work-")), sweepMs: 60_000, limitWaitMs: 60_000 },
    [], () => new Set(), () => {}, () => false, { seats: holo, awaiting: () => new Set() });
  const files = () => readdirSync(join(root, "Holo", "lifelog", "post")).map(name => readFileSync(join(root, "Holo", "lifelog", "post", name), "utf8")).join("");
  const post = office(seats());
  const before = files();
  assert.equal(post.holoNext(new Date(at(1))), undefined, "席が開くまでは渡さない");
  assert.equal(files(), before);
  post.sweep(new Date(at(1)));
  assert.equal(post.holoNext(new Date(at(2)))?.seat, 1);
  const opened = files();
  post.holoNext(new Date(at(3)));
  assert.equal(files(), opened);
  post.stop();

  const restarted = office(seats());
  restarted.sweep(new Date(at(4)));
  assert.equal(readAll(root, "Holo").filter(line => line.kind === "seat" && line.event === "open").length, 1, "起き直しても2つ目の席を開かない");
  assert.equal(restarted.holoNext(new Date(at(5)))?.seat, 1);
  restarted.stop();
});
