import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { append, postDir, readAll, unfinished } from "./letters.ts";
import { createMailbox } from "./mcp.ts";

// テストは使い捨てのイデアの置き場で動く。本物の D:\Products\Residents には触れない。
function residentsRoot(): string {
  return mkdtempSync(join(tmpdir(), "nirai-post-"));
}

async function open(resident: string, root: string) {
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
  await createMailbox(resident, root).connect(serverSide);
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

test("届き直した回数は、その手紙を含む wake の数", () => {
  const root = residentsRoot();
  const ts = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
  append(root, "Codex", { kind: "letter", ts: ts(0), id: "A", from: "Holo", to: "Codex", body: "a" });
  append(root, "Codex", { kind: "wake", ts: ts(1), letters: ["A"], how: "codex exec" });
  append(root, "Codex", { kind: "stop", ts: ts(2), how: "timeout" });
  append(root, "Codex", { kind: "letter", ts: ts(3), id: "B", from: "Holo", to: "Codex", body: "b" });
  append(root, "Codex", { kind: "wake", ts: ts(4), letters: ["A", "B"], how: "codex exec" });

  const counts = Object.fromEntries(unfinished(readAll(root, "Codex")).map(l => [l.id, l.deliveries]));
  assert.deepEqual(counts, { A: 2, B: 1 });
});

test("郵便受けの説明に、決まりと本人の人格が入る", async () => {
  const root = residentsRoot();
  const { client } = await open("Codex", root);
  const said = client.getInstructions() ?? "";
  assert.match(said, /郵便の決まり/);
  assert.match(said, /あなたはCodex/);
  assert.equal(readFileSync(new URL("./郵便の決まり.md", import.meta.url), "utf8").length > 0, true);
});
