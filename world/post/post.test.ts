import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { append, postDir, readAll, unfinished } from "./letters.ts";
import { createMailbox, rulesFor } from "./mcp.ts";
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

test("Holoの受付と作業場でroomを必須にし、別室の手紙を読む・済みにするのを拒む", async () => {
  const root = residentsRoot();
  append(root, "Holo", { kind: "letter", ts: new Date().toISOString(), id: "RECEPTION", from: "Claude", to: "Holo", body: "受付" });
  append(root, "Holo", { kind: "letter", ts: new Date().toISOString(), id: "A", from: "Claude", to: "Holo", body: "作業A", work: "job-a" });
  append(root, "Holo", { kind: "letter", ts: new Date().toISOString(), id: "B", from: "Claude", to: "Holo", body: "作業B", work: "job-b" });
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
  await createMailbox("Holo", root, undefined, undefined, undefined, true).connect(serverSide);
  const client = new Client({ name: "room-test", version: "0" });
  await client.connect(clientSide);
  const call = async (name: string, args: Record<string, unknown> = {}) => client.callTool({ name, arguments: args });
  const without = await call("read_mailbox");
  assert.equal(without.isError, true, "roomなしの呼び出しは禁止");
  const a = await call("read_mailbox", { room: "job-a" });
  assert.deepEqual(JSON.parse((a.content[0] as { text: string }).text).map((x: { id: string }) => x.id), ["A"]);
  const reception = await call("read_mailbox", { room: "受付" });
  assert.deepEqual(JSON.parse((reception.content[0] as { text: string }).text).map((x: { id: string }) => x.id), ["RECEPTION"]);
  assert.deepEqual(readAll(root, "Holo").filter(l => l.kind === "read").map(l => l.work ?? ""), ["job-a", ""],
    "読めた記録を部屋の筋に分けて残す");
  assert.equal((await call("write_note", { room: "job-a", letter: "B", body: "異なる部屋" })).isError, true);
  assert.equal((await call("mark_done", { room: "job-a", letter: "B" })).isError, true);
  assert.equal((await call("send_letter", { room: "job-a", to: "Claude", body: "別室への返事は禁止", reply_to: "B" })).isError, true);
  assert.notEqual((await call("send_letter", { room: "job-a", to: "Codex", body: "レビュー" })).isError, true);
  assert.equal(unfinished(readAll(root, "Codex"))[0].work, "job-a");
  assert.notEqual((await call("mark_done", { room: "job-a", letter: "A" })).isError, true);
});

test("筋の郵便受けはその仕事の手紙だけを読み、他の筋へのnote・doneを拒み、返事以外は筋を引き継ぐ", async () => {
  const root = residentsRoot();
  for (const [id, work] of [["X", "A"], ["Y", "B"], ["RECEPTION", undefined]] as const) {
    append(root, "Claude", { kind: "letter", ts: new Date().toISOString(), id, from: "Holo", to: "Claude", body: id, ...(work ? { work } : {}) });
  }
  const a = await open("Claude", root, "A");
  const b = await open("Claude", root, "B");
  const reception = await open("Claude", root, "");
  assert.deepEqual(JSON.parse((await a.call("read_mailbox")).text).map((x: { id: string }) => x.id), ["X"]);
  assert.deepEqual(JSON.parse((await b.call("read_mailbox")).text).map((x: { id: string }) => x.id), ["Y"]);
  assert.deepEqual(JSON.parse((await reception.call("read_mailbox")).text).map((x: { id: string }) => x.id), ["RECEPTION"]);
  assert.equal((await a.call("write_note", { letter: "Y", body: "侵入" })).isError, true);
  assert.equal((await a.call("mark_done", { letter: "Y" })).isError, true);
  assert.equal((await a.call("send_letter", { to: "Holo", body: "質問" })).isError, false);
  const [sent] = unfinished(readAll(root, "Holo"));
  assert.equal(sent.work, "A");
  assert.equal((await a.call("mark_done", { letter: "X" })).isError, false);
  assert.equal((await a.call("send_letter", { to: "Holo", body: "返事", reply_to: "Y" })).isError, true);
});

test("手紙は受取人の生ログに入り、差出人は入口で決まる", async () => {
  const root = residentsRoot();
  const holo = await open("Holo", root);
  const sent = await holo.call("send_letter", { to: "codex", body: "レビューして", work: "post-review" });
  assert.equal(sent.isError, false);

  assert.deepEqual(readAll(root, "Holo"), []);
  const [letter] = readAll(root, "Codex");
  assert.equal(letter.kind, "letter");
  assert.equal(letter.kind === "letter" && letter.from, "Holo");
  assert.equal(letter.kind === "letter" && letter.work, "post-review");
  assert.match(readdirSync(postDir(root, "Codex"))[0], /^\d{4}-\d{2}-\d{2}\.jsonl$/);
});

test("返事はworkを引き継ぎ、済みにしてから返す往復でも、最後のdoneからkeepMsは作業場を残す", async () => {
  const root = residentsRoot();
  const holo = await open("Holo", root);
  const codex = await open("Codex", root);
  const keepMs = 50 * 60_000;

  await holo.call("send_letter", { to: "Codex", body: "レビューして", work: "review-job" });
  const [request] = unfinished(readAll(root, "Codex"));
  assert.equal((await codex.call("mark_done", { letter: request.id })).isError, false, "普通に済みにできる");
  assert.deepEqual(toClean(["review-job"], [readAll(root, "Holo"), readAll(root, "Codex")], new Set(), new Date(), keepMs), [], "done直後なので片付けない");

  await codex.call("send_letter", { to: "Holo", body: "ここを直して", reply_to: request.id });
  const [firstReview] = unfinished(readAll(root, "Holo"));
  assert.equal(firstReview.work, "review-job");
  assert.deepEqual(toClean(["review-job"], [readAll(root, "Holo"), readAll(root, "Codex")], new Set(), new Date(), keepMs), []);

  assert.equal((await holo.call("mark_done", { letter: firstReview.id })).isError, false);
  assert.deepEqual(toClean(["review-job"], [readAll(root, "Holo"), readAll(root, "Codex")], new Set(), new Date(), keepMs), [], "再レビュー依頼を書く途中もdone直後なので片付けない");
  await holo.call("send_letter", { to: "Codex", body: "直したので再レビューして", reply_to: firstReview.id });
  const [secondRequest] = unfinished(readAll(root, "Codex"));
  assert.equal(secondRequest.work, "review-job");
  assert.deepEqual(toClean(["review-job"], [readAll(root, "Holo"), readAll(root, "Codex")], new Set(), new Date(), keepMs), []);

  assert.equal((await codex.call("mark_done", { letter: secondRequest.id })).isError, false);
  await codex.call("send_letter", { to: "Holo", body: "レビューOK", reply_to: secondRequest.id });
  const [finalReview] = unfinished(readAll(root, "Holo"));
  assert.equal(finalReview.work, "review-job");
  assert.deepEqual(toClean(["review-job"], [readAll(root, "Holo"), readAll(root, "Codex")], new Set(), new Date(), keepMs), []);

  assert.equal((await holo.call("mark_done", { letter: finalReview.id })).isError, false);
  const all = [readAll(root, "Holo"), readAll(root, "Codex")];
  const lastDoneAt = Math.max(...all.flat().filter(line => line.kind === "done").map(line => Date.parse(line.ts)));
  assert.deepEqual(toClean(["review-job"], all, new Set(), new Date(lastDoneAt + keepMs - 1), keepMs), [], "keepMs未満は残す");
  assert.deepEqual(toClean(["review-job"], all, new Set(), new Date(lastDoneAt + keepMs), keepMs), ["review-job"], "keepMsで片付ける");

  await holo.call("send_letter", { to: "Codex", body: "別作業場へ", reply_to: finalReview.id, work: "other-job" });
  const explicit = unfinished(readAll(root, "Codex")).at(-1);
  assert.equal(explicit?.work, "other-job");
});

test("済んだ手紙は郵便受けから消え、生ログには残る", async () => {
  const root = residentsRoot();
  const holo = await open("Holo", root);
  const codex = await open("Codex", root);
  await holo.call("send_letter", { to: "Codex", body: "1通目" });
  await holo.call("send_letter", { to: "Codex", body: "2通目" });
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
  const holo = await open("Holo", root);
  const codex = await open("Codex", root);
  await holo.call("send_letter", { to: "Codex", body: "長い仕事" });
  const [letter] = unfinished(readAll(root, "Codex"));
  await codex.call("write_note", { letter: letter.id, body: "テストを3本書いた" });

  const [again] = JSON.parse((await codex.call("read_mailbox")).text);
  assert.deepEqual(again.notes.map((n: { body: string }) => n.body), ["テストを3本書いた"]);
});

test("ほかの住人の手紙には、書き残しも印も付けられない", async () => {
  const root = residentsRoot();
  const holo = await open("Holo", root);
  await holo.call("send_letter", { to: "Codex", body: "Codexへ" });
  const [letter] = unfinished(readAll(root, "Codex"));

  assert.equal((await holo.call("write_note", { letter: letter.id, body: "横から" })).isError, true);
  assert.equal((await holo.call("mark_done", { letter: letter.id })).isError, true);
  assert.equal(unfinished(readAll(root, "Codex")).length, 1);
});

test("作業場の名前で、作業場の外を指せない", async () => {
  const root = residentsRoot();
  const holo = await open("Holo", root);
  for (const work of ["..", "..\\Residents", "a/b", "C:x", ".hidden", ""]) {
    const result = await holo.call("send_letter", { to: "Codex", body: "x", work });
    assert.equal(result.isError, true, work);
  }
  assert.deepEqual(readAll(root, "Codex"), []);
});

test("チームにいない宛先には届けない", async () => {
  const root = residentsRoot();
  const holo = await open("Holo", root);
  assert.equal((await holo.call("send_letter", { to: "Serina", body: "x" })).isError, true);
  assert.equal((await holo.call("send_letter", { to: "../Codex", body: "x" })).isError, true);
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
  append(root, "Codex", { kind: "letter", ts: old(0), id: "A", from: "Holo", to: "Codex", body: "A" });
  append(root, "Codex", { kind: "letter", ts: old(0), id: "B", from: "Holo", to: "Codex", body: "B" });
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
