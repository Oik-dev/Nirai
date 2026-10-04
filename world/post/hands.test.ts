import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { applyPatch, Hands } from "./hands.ts";
import { type Letter, readAll, unfinished } from "./letters.ts";
import { createMailbox } from "./mcp.ts";

// テストは使い捨ての置き場で動く。本物のイデアと D:\Products\Work には触れない。
function place() {
  const root = mkdtempSync(join(tmpdir(), "nirai-hands-"));
  const residents = join(root, "residents");
  const work = join(root, "work");
  mkdirSync(join(work, "job"), { recursive: true });
  return { residents, work, job: join(work, "job") };
}

function hands(p: ReturnType<typeof place>, waitMs = 20_000, sent: Letter[] = []) {
  return new Hands(p.residents, p.work, { waitMs, limitMs: 60_000 }, letter => sent.push(letter));
}

const patch = (...lines: string[]) => ["*** Begin Patch", ...lines, "*** End Patch"].join("\n");

test("差分で作る・書き換える・名前を変える・消す", () => {
  const { job } = place();
  writeFileSync(join(job, "app.ts"), "function main() {\r\n  console.log(\"old\");\r\n}\r\n");
  writeFileSync(join(job, "old.txt"), "消す\n");
  writeFileSync(join(job, "name.txt"), "a\nb\n");

  const changed = applyPatch(job, patch(
    "*** Add File: notes/hello.md",
    "+# こんにちは",
    "+",
    "+本文",
    "*** Update File: app.ts",
    "@@ function main() {",
    "-  console.log(\"old\");",
    "+  console.log(\"new\");",
    "*** Update File: name.txt",
    "*** Move to: renamed.txt",
    " a",
    "-b",
    "+B",
    "*** Delete File: old.txt",
  ));

  assert.deepEqual(changed, ["A notes/hello.md", "M app.ts", "M name.txt → renamed.txt", "D old.txt"]);
  assert.equal(readFileSync(join(job, "notes", "hello.md"), "utf8"), "# こんにちは\n\n本文\n", "作ったファイルは改行で終わる");
  assert.equal(readFileSync(join(job, "app.ts"), "utf8"), "function main() {\r\n  console.log(\"new\");\r\n}\r\n", "CRLFのまま");
  assert.equal(readFileSync(join(job, "renamed.txt"), "utf8"), "a\nB\n");
  assert.equal(existsSync(join(job, "name.txt")), false);
  assert.equal(existsSync(join(job, "old.txt")), false);
});

test("1か所でも当たらなければ、どのファイルも変えない", () => {
  const { job } = place();
  writeFileSync(join(job, "a.txt"), "one\n");
  writeFileSync(join(job, "b.txt"), "two\n");
  assert.throws(() => applyPatch(job, patch(
    "*** Update File: a.txt",
    "-one",
    "+ONE",
    "*** Update File: b.txt",
    "-ない行",
    "+x",
  )), /b\.txt/);
  assert.equal(readFileSync(join(job, "a.txt"), "utf8"), "one\n");
  assert.throws(() => applyPatch(job, patch("*** Update File: none.txt", "-x", "+y")), /none\.txt/);
  assert.throws(() => applyPatch(job, "*** Add File: c.txt\n+c"), /Begin Patch/);
  assert.equal(existsSync(join(job, "c.txt")), false);
});

test("作業場の外には書けない", () => {
  const { job, work } = place();
  for (const name of ["../escape.txt", "..\\escape.txt", join(work, "escape.txt"), "C:\\Windows\\escape.txt"]) {
    assert.throws(() => applyPatch(job, patch(`*** Add File: ${name}`, "+x")), /作業場の外/, name);
  }
  assert.throws(() => applyPatch(job, patch("*** Update File: a.txt", "*** Move to: ../a.txt")), /作業場の外|ファイルがない/);
  assert.equal(existsSync(join(work, "escape.txt")), false);
});

test("コマンドは作業場で動き、日本語の出力と終了コードが返り、生ログに残る", async () => {
  const p = place();
  const h = hands(p);
  const ok = await h.run("Holo", "job", "'日本語の出力'; (Get-Location).Path");
  assert.match(ok, /^終了コード 0/);
  assert.match(ok, /日本語の出力/);
  assert.ok(ok.includes(p.job), ok);

  const failed = await h.run("Holo", "job", "node -e \"process.exit(3)\"");
  assert.doesNotMatch(failed, /^終了コード 0/);

  const dir = join(p.residents, "Holo", "lifelog", "hands");
  const lines = readFileSync(join(dir, readdirSync(dir)[0]), "utf8").trim().split("\n").map(l => JSON.parse(l));
  assert.deepEqual(lines.map(l => [l.kind, l.work, l.reply]), [["run", "job", "tool"], ["run", "job", "tool"]]);
  assert.match(lines[0].output, /日本語の出力/);
  assert.deepEqual(h.busy(), new Set());
});

test("長いコマンドは「続いている」と返し、終わったら結果を手紙で届ける。動いている間は作業場を片付けない", async () => {
  const p = place();
  const sent: Letter[] = [];
  const h = hands(p, 300, sent);
  const answer = await h.run("Holo", "job", "Start-Sleep -Milliseconds 2500; '終わった'");
  assert.match(answer, /続いている/);
  assert.deepEqual(h.busy(), new Set(["job"]));
  assert.deepEqual(readAll(p.residents, "Holo"), []);

  while (h.busy().size > 0) await new Promise(r => setTimeout(r, 100));
  const [letter] = unfinished(readAll(p.residents, "Holo"));
  assert.equal(letter.from, "郵便局");
  assert.equal(letter.work, "job", "手紙が済むまで作業場が残る");
  assert.match(letter.body, /終了コード 0/);
  assert.match(letter.body, /終わった/);
  assert.match(letter.based_on ?? "", /^R/);
  assert.deepEqual(sent.map(l => l.id), [letter.id], "郵便局がすぐに見直せるように知らせる");
});

test("ない作業場では動かない", async () => {
  const h = hands(place());
  await assert.rejects(h.run("Holo", "nothing", "'x'"), /作業場「nothing」はまだない/);
  await assert.rejects(h.run("Holo", "..", "'x'"), /作業場の外/);
});

test("手の道具は、手を貸す住人の郵便受けにだけある。当たらない差分は道具のエラーで返る", async () => {
  const p = place();
  const tools = async (resident: string, lend: boolean) => {
    const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
    await createMailbox(resident, p.residents, undefined, lend ? hands(p) : undefined).connect(serverSide);
    const client = new Client({ name: "test", version: "0" });
    await client.connect(clientSide);
    return client;
  };
  const holo = await tools("Holo", true);
  assert.deepEqual((await holo.listTools()).tools.map(t => t.name).sort(),
    ["apply_patch", "mark_done", "read_mailbox", "run", "send_letter", "write_note"]);
  const codex = await tools("Codex", false);
  assert.deepEqual((await codex.listTools()).tools.map(t => t.name).sort(), ["mark_done", "read_mailbox", "send_letter", "write_note"]);

  const bad = (await holo.callTool({ name: "apply_patch", arguments: { work: "job", patch: patch("*** Update File: x", "-a") } })) as {
    content: { text: string }[]; isError?: boolean;
  };
  assert.equal(bad.isError, true);
  assert.match(bad.content[0].text, /どのファイルも変えていない/);
});
