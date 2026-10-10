import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { append, postDir, readAll, unfinished } from "./letters.ts";
import { createMailbox, rulesFor } from "./mcp.ts";
import { seatsOf } from "./seats.ts";
import { toClean } from "./work.ts";

// テストは使い捨てのイデアの置き場で動く。本物の D:\Products\Residents には触れない。
function residentsRoot(): string {
  return mkdtempSync(join(tmpdir(), "nirai-post-"));
}

async function open(resident: string, root: string, scope?: string) {
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
  await createMailbox(resident, root, undefined, undefined, scope).connect(serverSide);
  const client = new Client({ name: "test", version: "0" });
  await client.connect(clientSide);
  const call = async (name: string, args: Record<string, unknown> = {}) => {
    const result = (await client.callTool({ name, arguments: args })) as {
      content: { text: string }[];
      isError?: boolean;
    };
    return { text: result.content[0].text, isError: result.isError === true };
  };
  return { client, call };
}

test("Holoは席の番号で居場所が決まる。空いた席では使えず、結ぶまでは手紙を読めず、ほかの作業場の手紙には触れない", async () => {
  const root = residentsRoot();
  const ts = () => new Date().toISOString();
  append(root, "Holo", { kind: "letter", ts: ts(), id: "A", from: "Claude", to: "Holo", body: "作業A", work: "job-a" });
  append(root, "Holo", { kind: "letter", ts: ts(), id: "B", from: "Claude", to: "Holo", body: "作業B", work: "job-b" });
  const holo = await open("Holo", root);
  assert.equal((await holo.call("read_mailbox")).isError, true, "席の番号なしは使えない");
  assert.match((await holo.call("read_mailbox", { seat: 1 })).text, /空いている/);

  append(root, "Holo", { kind: "seat", ts: ts(), seat: 1, event: "open" }); // Masterが入った
  const unbound = await holo.call("read_mailbox", { seat: 1 });
  assert.match(unbound.text, /bind_seat/);
  assert.match(unbound.text, /job-a・job-b/, "済んでいない手紙のある作業場を示す");
  assert.equal((await holo.call("send_letter", { seat: 1, to: "Codex", body: "作業場なし" })).isError, true);
  assert.equal((await holo.call("bind_seat", { seat: 2, work: "job-a" })).isError, true, "空いた席は結べない");
  assert.equal((await holo.call("bind_seat", { seat: 1, work: "job-a" })).isError, false);
  assert.equal((await holo.call("bind_seat", { seat: 1, work: "job-b" })).isError, true, "結んだ席は結び直さない");

  assert.deepEqual(JSON.parse((await holo.call("read_mailbox", { seat: 1 })).text).map((x: { id: string }) => x.id), ["A"]);
  assert.deepEqual(readAll(root, "Holo").filter(l => l.kind === "read").map(l => l.kind === "read" && [l.work, l.seat]), [["job-a", 1]],
    "読んだ記録に作業場と席を残す");
  assert.equal((await holo.call("write_note", { seat: 1, letter: "B", body: "ほかの作業場" })).isError, true);
  assert.equal((await holo.call("mark_done", { seat: 1, letter: "B" })).isError, true);
  assert.equal((await holo.call("send_letter", { seat: 1, to: "Claude", body: "ほかの作業場への返事", reply_to: "B" })).isError, true);
  assert.equal((await holo.call("send_letter", { seat: 1, to: "Codex", body: "レビュー" })).isError, false);
  assert.equal(unfinished(readAll(root, "Codex"))[0].work, "job-a", "席の作業場を引き継ぐ");

  append(root, "Holo", { kind: "seat", ts: ts(), seat: 2, event: "open" });
  assert.match((await holo.call("bind_seat", { seat: 2, work: "JOB-A" })).text, /席1で開いている/, "1つの作業場に席は1つ");
  assert.equal((await holo.call("bind_seat", { seat: 2, work: "job-b" })).isError, false);
  assert.equal((await holo.call("mark_done", { seat: 1, letter: "A" })).isError, false);
  assert.equal((await holo.call("mark_done", { seat: 2, letter: "B" })).isError, false);
});

test("結ばれた席は引き継ぎを書いて閉じ、次にその作業場で入った席の最初の読みに引き継ぎが添う。閉じてほしい手紙は閉じると済む", async () => {
  const root = residentsRoot();
  const ts = () => new Date().toISOString();
  const holo = await open("Holo", root);
  append(root, "Holo", { kind: "letter", ts: ts(), id: "A", from: "Claude", to: "Holo", body: "作業A", work: "job-a" });
  append(root, "Holo", { kind: "seat", ts: ts(), seat: 1, event: "open", work: "job-a", url: "https://chatgpt.com/g/g-p-1/c/1" });
  append(root, "Holo", { kind: "letter", ts: ts(), id: "CLOSE", from: "郵便局", to: "Holo", body: "席を閉じて", work: "job-a", close: 1 });
  assert.deepEqual(JSON.parse((await holo.call("read_mailbox", { seat: 1 })).text).map((x: { id: string }) => x.id), ["CLOSE"],
    "閉じてほしい間は、その手紙だけを読む");
  assert.match((await holo.call("mark_done", { seat: 1, letter: "CLOSE" })).text, /leave_seat/);
  assert.equal((await holo.call("leave_seat", { seat: 1 })).isError, true, "結ばれた席は引き継ぎなしで閉じない");
  assert.equal((await holo.call("leave_seat", { seat: 1, handover: "Aは半分。Masterは短い報告が好き" })).isError, false);
  assert.deepEqual(unfinished(readAll(root, "Holo")).map(l => l.id), ["A"], "閉じてほしい手紙は済んだ");
  assert.equal(seatsOf(readAll(root, "Holo"), 3)[0].since, undefined);
  assert.equal((await holo.call("read_mailbox", { seat: 1 })).isError, true, "閉じた席では郵便受けに触れない");

  append(root, "Holo", { kind: "seat", ts: ts(), seat: 2, event: "open", work: "job-a" });
  const first = (await holo.call("read_mailbox", { seat: 2 })).text;
  assert.match(first, /^前の席からの引き継ぎ（.+）：\nAは半分。Masterは短い報告が好き/);
  assert.doesNotMatch((await holo.call("read_mailbox", { seat: 2 })).text, /引き継ぎ/, "引き継ぎは最初の読みにだけ添う");

  assert.equal((await holo.call("leave_seat", { seat: 2, handover: "Aはjob-bの後で", next_work: "job-b" })).isError, false);
  const moved = seatsOf(readAll(root, "Holo"), 3)[1];
  assert.equal(moved.work, "job-b", "同じ会話のまま別の作業場へ移る");
  const opened = readAll(root, "Holo").find(l => l.kind === "seat" && l.seat === 2 && l.event === "open")!;
  assert.equal(moved.started, opened.ts, "会話の始まりは変わらない（字数を続けて数える）");

  append(root, "Holo", { kind: "seat", ts: ts(), seat: 3, event: "open" });
  assert.equal((await holo.call("leave_seat", { seat: 3, next_work: "job-c" })).isError, true);
  assert.equal((await holo.call("leave_seat", { seat: 3 })).isError, false, "入ったばかりの席は引き継ぎなしで空ける");
});

test("作業場の入口で起こしたCLIの住人は、その作業場の手紙だけを扱い、作業場を引き継いで出す。入口のない郵便受けは全部を見る", async () => {
  const root = residentsRoot();
  for (const [id, work] of [["X", "A"], ["Y", "B"]] as const) {
    append(root, "Claude", { kind: "letter", ts: new Date().toISOString(), id, from: "Holo", to: "Claude", body: id, work });
  }
  const a = await open("Claude", root, "A");
  const b = await open("Claude", root, "B");
  const all = await open("Claude", root);
  assert.deepEqual(JSON.parse((await a.call("read_mailbox")).text).map((x: { id: string }) => x.id), ["X"]);
  assert.deepEqual(JSON.parse((await b.call("read_mailbox")).text).map((x: { id: string }) => x.id), ["Y"]);
  assert.deepEqual(JSON.parse((await all.call("read_mailbox")).text).map((x: { id: string }) => x.id), ["X", "Y"]);
  assert.equal((await a.call("write_note", { letter: "Y", body: "侵入" })).isError, true);
  assert.equal((await a.call("mark_done", { letter: "Y" })).isError, true);
  assert.equal((await a.call("send_letter", { to: "Holo", body: "質問" })).isError, false);
  const [sent] = unfinished(readAll(root, "Holo"));
  assert.equal(sent.work, "A");
  assert.equal((await a.call("mark_done", { letter: "X" })).isError, false);
  assert.equal((await a.call("send_letter", { to: "Holo", body: "返事", reply_to: "Y" })).isError, true);
  assert.equal((await all.call("send_letter", { to: "Holo", body: "作業場なし" })).isError, true, "入口のない郵便受けでは work が要る");
});


test("手紙は受取人の生ログに入り、差出人は入口で決まる", async () => {
  const root = residentsRoot();
  const claude = await open("Claude", root);
  const sent = await claude.call("send_letter", { to: "codex", body: "レビューして", work: "post-review" });
  assert.equal(sent.isError, false);

  assert.deepEqual(readAll(root, "Claude"), []);
  const [letter] = readAll(root, "Codex");
  assert.equal(letter.kind, "letter");
  assert.equal(letter.kind === "letter" && letter.from, "Claude");
  assert.equal(letter.kind === "letter" && letter.work, "post-review");
  assert.match(readdirSync(postDir(root, "Codex"))[0], /^\d{4}-\d{2}-\d{2}\.jsonl$/);
});

test("返事はworkを引き継ぎ、済みにしてから返す往復でも、最後のdoneからkeepMsは作業場を残す", async () => {
  const root = residentsRoot();
  const claude = await open("Claude", root);
  const codex = await open("Codex", root);
  const keepMs = 50 * 60_000;

  await claude.call("send_letter", { to: "Codex", body: "レビューして", work: "review-job" });
  const [request] = unfinished(readAll(root, "Codex"));
  assert.equal((await codex.call("mark_done", { letter: request.id })).isError, false, "普通に済みにできる");
  assert.deepEqual(toClean(["review-job"], [readAll(root, "Claude"), readAll(root, "Codex")], new Set(), new Date(), keepMs), [], "done直後なので片付けない");

  await codex.call("send_letter", { to: "Claude", body: "ここを直して", reply_to: request.id });
  const [firstReview] = unfinished(readAll(root, "Claude"));
  assert.equal(firstReview.work, "review-job");
  assert.deepEqual(toClean(["review-job"], [readAll(root, "Claude"), readAll(root, "Codex")], new Set(), new Date(), keepMs), []);

  assert.equal((await claude.call("mark_done", { letter: firstReview.id })).isError, false);
  assert.deepEqual(toClean(["review-job"], [readAll(root, "Claude"), readAll(root, "Codex")], new Set(), new Date(), keepMs), [], "再レビュー依頼を書く途中もdone直後なので片付けない");
  await claude.call("send_letter", { to: "Codex", body: "直したので再レビューして", reply_to: firstReview.id });
  const [secondRequest] = unfinished(readAll(root, "Codex"));
  assert.equal(secondRequest.work, "review-job");
  assert.deepEqual(toClean(["review-job"], [readAll(root, "Claude"), readAll(root, "Codex")], new Set(), new Date(), keepMs), []);

  assert.equal((await codex.call("mark_done", { letter: secondRequest.id })).isError, false);
  await codex.call("send_letter", { to: "Claude", body: "レビューOK", reply_to: secondRequest.id });
  const [finalReview] = unfinished(readAll(root, "Claude"));
  assert.equal(finalReview.work, "review-job");
  assert.deepEqual(toClean(["review-job"], [readAll(root, "Claude"), readAll(root, "Codex")], new Set(), new Date(), keepMs), []);

  assert.equal((await claude.call("mark_done", { letter: finalReview.id })).isError, false);
  const all = [readAll(root, "Claude"), readAll(root, "Codex")];
  const lastDoneAt = Math.max(...all.flat().filter(line => line.kind === "done").map(line => Date.parse(line.ts)));
  assert.deepEqual(toClean(["review-job"], all, new Set(), new Date(lastDoneAt + keepMs - 1), keepMs), [], "keepMs未満は残す");
  assert.deepEqual(toClean(["review-job"], all, new Set(), new Date(lastDoneAt + keepMs), keepMs), ["review-job"], "keepMsで片付ける");

  await claude.call("send_letter", { to: "Codex", body: "別作業場へ", reply_to: finalReview.id, work: "other-job" });
  const explicit = unfinished(readAll(root, "Codex")).at(-1);
  assert.equal(explicit?.work, "other-job");
});

test("済んだ手紙は郵便受けから消え、生ログには残る", async () => {
  const root = residentsRoot();
  const claude = await open("Claude", root);
  const codex = await open("Codex", root);
  await claude.call("send_letter", { to: "Codex", body: "1通目", work: "W" });
  await claude.call("send_letter", { to: "Codex", body: "2通目", work: "W" });
  const [first, second] = unfinished(readAll(root, "Codex"));

  assert.equal((await codex.call("write_note", { letter: first.id, body: "半分やった" })).isError, false);
  assert.equal((await codex.call("mark_done", { letter: first.id, note: "済み" })).isError, false);
  assert.equal((await codex.call("mark_done", { letter: first.id })).isError, false, "2回目の印は害がない");

  const left = unfinished(readAll(root, "Codex"));
  assert.deepEqual(left.map(l => l.id), [second.id]);
  assert.equal(readAll(root, "Codex").filter(l => l.kind === "done").length, 1);
  assert.match((await codex.call("read_mailbox")).text, /2通目/);
});

test("書き残しは、次に読むときに手紙と一緒に返る", async () => {
  const root = residentsRoot();
  const claude = await open("Claude", root);
  const codex = await open("Codex", root);
  await claude.call("send_letter", { to: "Codex", body: "長い仕事", work: "W" });
  const [letter] = unfinished(readAll(root, "Codex"));
  await codex.call("write_note", { letter: letter.id, body: "テストを3本書いた" });

  const [again] = JSON.parse((await codex.call("read_mailbox")).text);
  assert.deepEqual(again.notes.map((n: { body: string }) => n.body), ["テストを3本書いた"]);
});

test("ほかの住人の手紙には、書き残しも印も付けられない", async () => {
  const root = residentsRoot();
  const claude = await open("Claude", root);
  await claude.call("send_letter", { to: "Codex", body: "Codexへ", work: "W" });
  const [letter] = unfinished(readAll(root, "Codex"));

  assert.equal((await claude.call("write_note", { letter: letter.id, body: "横から" })).isError, true);
  assert.equal((await claude.call("mark_done", { letter: letter.id })).isError, true);
  assert.equal(unfinished(readAll(root, "Codex")).length, 1);
});

test("作業場の名前で、作業場の外を指せない", async () => {
  const root = residentsRoot();
  const claude = await open("Claude", root);
  for (const work of ["..", "..\\Residents", "a/b", "C:x", ".hidden", ""]) {
    const result = await claude.call("send_letter", { to: "Codex", body: "x", work });
    assert.equal(result.isError, true, work);
  }
  assert.deepEqual(readAll(root, "Codex"), []);
});

test("チームにいない宛先には届けない", async () => {
  const root = residentsRoot();
  const claude = await open("Claude", root);
  assert.equal((await claude.call("send_letter", { to: "Serina", body: "x" })).isError, true);
  assert.equal((await claude.call("send_letter", { to: "../Codex", body: "x" })).isError, true);
});

test("届き直した回数は、最後に何かを済ませてから、その手紙を含む wake の数", () => {
  const root = residentsRoot();
  const ts = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
  append(root, "Codex", { kind: "letter", ts: ts(0), id: "A", from: "Holo", to: "Codex", body: "a" });
  append(root, "Codex", { kind: "letter", ts: ts(0), id: "B", from: "Holo", to: "Codex", body: "b" });
  append(root, "Codex", { kind: "letter", ts: ts(0), id: "C", from: "Holo", to: "Codex", body: "c" });
  append(root, "Codex", { kind: "wake", ts: ts(1), letters: ["A", "B", "C"], how: "codex exec" });
  append(root, "Codex", { kind: "done", ts: ts(2), letter: "A" });
  append(root, "Codex", { kind: "wake", ts: ts(3), letters: ["B", "C"], how: "codex exec" });
  append(root, "Codex", { kind: "read", ts: new Date(Date.parse(ts(3)) + 100).toISOString() });

  let counts = Object.fromEntries(unfinished(readAll(root, "Codex")).map(l => [l.id, l.deliveries]));
  assert.deepEqual(counts, { B: 1, C: 1 }, "Aを済ませたのでB/Cの古いwakeは数えない");

  append(root, "Codex", { kind: "done", ts: ts(4), letter: "B" });
  append(root, "Codex", { kind: "wake", ts: ts(5), letters: ["C"], how: "codex exec" });
  append(root, "Codex", { kind: "read", ts: new Date(Date.parse(ts(5)) + 100).toISOString() });
  counts = Object.fromEntries(unfinished(readAll(root, "Codex")).map(l => [l.id, l.deliveries]));
  assert.deepEqual(counts, { C: 1 }, "Bも済んだのでCもまた1から数える");
});

test("何も済ませなければ、済んでいない手紙は3回で3回と数える", () => {
  const root = residentsRoot();
  const ts = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
  for (const id of ["A", "B"]) append(root, "Codex", { kind: "letter", ts: ts(0), id, from: "Holo", to: "Codex", body: id });
  for (const s of [1, 2, 3]) {
    append(root, "Codex", { kind: "wake", ts: ts(s), letters: ["A", "B"], how: "codex exec" });
    append(root, "Codex", { kind: "read", ts: new Date(Date.parse(ts(s)) + 100).toISOString() });
  }
  const counts = Object.fromEntries(unfinished(readAll(root, "Codex")).map(l => [l.id, l.deliveries]));
  assert.deepEqual(counts, { A: 3, B: 3 });
});

test("1通だけを持ったまま何も済ませなければ、返事待ちでも3回で3回と数える", () => {
  const root = residentsRoot();
  const ts = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
  append(root, "Holo", { kind: "letter", ts: ts(0), id: "A", from: "Codex", to: "Holo", body: "返事待ち" });
  for (const s of [1, 2, 3]) {
    append(root, "Holo", { kind: "wake", ts: ts(s), letters: ["A"], how: "holo tab" });
    append(root, "Holo", { kind: "read", ts: new Date(Date.parse(ts(s)) + 100).toISOString() });
  }
  assert.equal(unfinished(readAll(root, "Holo"))[0].deliveries, 3);
});

test("明示mark_doneから、残った手紙の回数を数え直す", async () => {
  const root = residentsRoot();
  const codex = await open("Codex", root);
  const old = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
  append(root, "Codex", { kind: "letter", ts: old(0), id: "A", from: "Holo", to: "Codex", body: "A", work: "W" });
  append(root, "Codex", { kind: "letter", ts: old(0), id: "B", from: "Holo", to: "Codex", body: "B", work: "W" });
  append(root, "Codex", { kind: "wake", ts: old(1), letters: ["A", "B"], how: "codex exec" });
  append(root, "Codex", { kind: "wake", ts: old(2), letters: ["A", "B"], how: "codex exec" });
  assert.equal((await codex.call("mark_done", { letter: "A" })).isError, false);
  assert.equal((await codex.call("send_letter", { to: "Holo", body: "Aの返事", reply_to: "A" })).isError, false, "返事はdoneとは別");
  append(root, "Codex", { kind: "wake", ts: new Date(Date.now() + 1000).toISOString(), letters: ["B"], how: "codex exec" });
  append(root, "Codex", { kind: "read", ts: new Date(Date.now() + 1100).toISOString() });
  assert.equal(unfinished(readAll(root, "Codex"))[0].deliveries, 1);
});

test("郵便受けの説明に、決まりと本人の人格が入る", async () => {
  const root = residentsRoot();
  const { client } = await open("Codex", root);
  const said = client.getInstructions() ?? "";
  assert.match(said, /郵便の決まり/);
  assert.match(said, /あなたはCodex/);
  assert.equal(readFileSync(new URL("./郵便の決まり.md", import.meta.url), "utf8").length > 0, true);
});

test("「## <住人>だけ」の節は、その住人にだけ渡す", async () => {
  const root = residentsRoot();
  const holo = (await open("Holo", root)).client.getInstructions() ?? "";
  const codex = (await open("Codex", root)).client.getInstructions() ?? "";
  assert.match(holo, /## Holoだけ[\s\S]*land\.ts[\s\S]*apply_patch/, "Holoは取り込みと手の決まりを受け取る");
  assert.doesNotMatch(codex, /## Holoだけ|apply_patch|land\.ts/);
  assert.equal(rulesFor("Codex", "# 決まり\n\n- 皆\n\n## Holoだけ\n\n- 手\n\n## 住人\n\n表\n"), "# 決まり\n\n- 皆\n\n## 住人\n\n表");
});
