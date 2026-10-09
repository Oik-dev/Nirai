import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { applyPatch, Hands } from "./hands.ts";
import { type Letter, type Line, readAll, unfinished } from "./letters.ts";
import { createMailbox } from "./mcp.ts";
import { folders, toClean } from "./work.ts";

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

test("\\ を2つ重ねて書いた差分は、1つに戻すと当たるなら当てる。重ねたのが正しい差分はそのまま", () => {
  const { job } = place();
  writeFileSync(join(job, "doc.md"), "本物は`D:\\Products`にある。\n");
  const changed = applyPatch(job, patch("*** Update File: doc.md", "-本物は`D:\\\\Products`にある。", "+本物は`D:\\\\Products`に書かない。"));
  assert.equal(readFileSync(join(job, "doc.md"), "utf8"), "本物は`D:\\Products`に書かない。\n");
  assert.match(changed.at(-1)!, /1つに戻して/);

  writeFileSync(join(job, "path.ts"), "const root = \"D:\\\\Products\";\n");
  applyPatch(job, patch("*** Update File: path.ts", "-const root = \"D:\\\\Products\";", "+const root = \"D:\\\\Work\";"));
  assert.equal(readFileSync(join(job, "path.ts"), "utf8"), "const root = \"D:\\\\Work\";\n", "当たる差分の \\\\ は変えない");
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

test("進み具合の表示（行の書き直し）は、最後の書き直しだけ返す", async () => {
  const p = place();
  const out = await hands(p).run("Holo", "job", "[Console]::Out.Write(\"files: 50%`rfiles: 100%, done.`n\"); 'next'");
  assert.match(out, /files: 100%, done\.\nnext/);
  assert.doesNotMatch(out, /50%/);
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
  const holoTools = (await holo.listTools()).tools;
  assert.deepEqual(holoTools.map(t => t.name).sort(),
    ["apply_patch", "look", "mark_done", "read_mailbox", "run", "send_letter", "write_note"]);
  for (const name of ["run", "apply_patch"]) {
    const annotations = holoTools.find(tool => tool.name === name)?.annotations;
    assert.equal(annotations?.readOnlyHint, false);
    assert.equal(annotations?.destructiveHint, true, `${name} can overwrite or remove files`);
  }
  assert.equal(holoTools.find(tool => tool.name === "run")?.annotations?.openWorldHint, true);
  assert.equal(holoTools.find(tool => tool.name === "apply_patch")?.annotations?.openWorldHint, false);
  assert.match(holoTools.find(tool => tool.name === "run")?.description ?? "", /作業場内に制限されない/);
  const codex = await tools("Codex", false);
  assert.deepEqual((await codex.listTools()).tools.map(t => t.name).sort(), ["mark_done", "read_mailbox", "send_letter", "write_note"]);

  const bad = (await holo.callTool({ name: "apply_patch", arguments: { work: "job", patch: patch("*** Update File: x", "-a") } })) as {
    content: { text: string }[]; isError?: boolean;
  };
  assert.equal(bad.isError, true);
  assert.match(bad.content[0].text, /どのファイルも変えていない/);
});

// ここから下は、2026-10-04のCodexのレビュー（作業場 b2-hands-review の REVIEW.md）の再現テストを元にしている。

test("リンク（ジャンクション）を通しても、作業場の外には書けない。作業場そのものがリンクなら手は動かない", async t => {
  const p = place();
  const outside = join(p.work, "..", "outside");
  mkdirSync(outside);
  writeFileSync(join(outside, "victim.txt"), "外の元のまま\n");
  symlinkSync(outside, join(p.job, "linked"), "junction");
  assert.throws(() => applyPatch(p.job, patch("*** Update File: linked/victim.txt", "-外の元のまま", "+書き換えた")), /リンクの先が外/);
  assert.throws(() => applyPatch(p.job, patch("*** Add File: linked/new.txt", "+x")), /リンクの先が外/);
  assert.equal(readFileSync(join(outside, "victim.txt"), "utf8"), "外の元のまま\n");
  assert.equal(existsSync(join(outside, "new.txt")), false);

  try {
    symlinkSync(join(outside, "victim.txt"), join(p.job, "file-link.txt"), "file");
    assert.throws(() => applyPatch(p.job, patch("*** Update File: file-link.txt", "-外の元のまま", "+x")), /リンクの先が外/);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EPERM") throw error;
    t.diagnostic("ファイルのシンボリックリンクは権限がなくて作れないので、そこは確かめていない");
  }

  symlinkSync(outside, join(p.work, "linked-job"), "junction");
  const h = hands(p);
  await assert.rejects(h.run("Holo", "linked-job", "'x'"), /リンクになっている/);
  assert.throws(() => h.patch("Holo", "linked-job", patch("*** Add File: a.txt", "+a")), /リンクになっている/);
});

test("名前を変える先に書けなければ、元のファイルを残し、変えていないと正しく言う", async () => {
  const p = place();
  writeFileSync(join(p.job, "original.txt"), "大事な元\n");
  writeFileSync(join(p.job, "blocker"), "フォルダーではなくファイル\n");
  const changes = patch("*** Update File: original.txt", "*** Move to: blocker/moved.txt", "-大事な元", "+書き換えた");
  assert.throws(() => applyPatch(p.job, changes), /元に戻した/);
  assert.equal(readFileSync(join(p.job, "original.txt"), "utf8"), "大事な元\n");

  writeFileSync(join(p.job, "a.txt"), "a\n");
  assert.throws(() => applyPatch(p.job, patch(
    "*** Update File: a.txt", "-a", "+A",
    "*** Add File: blocker/b.txt", "+b",
  )), /元に戻した/);
  assert.equal(readFileSync(join(p.job, "a.txt"), "utf8"), "a\n", "先に書いたファイルも元に戻る");
});

test("@@ に書いた行がファイルになければ、ほかの場所を書き換えずに断る", () => {
  const { job } = place();
  const before = "function innocent() {\n  return false;\n}\n";
  writeFileSync(join(job, "app.ts"), before);
  assert.throws(() => applyPatch(job, patch("*** Update File: app.ts", "@@ function intended() {", "-  return false;", "+  return true;")),
    /function intended\(\) \{」がファイルにない/);
  assert.equal(readFileSync(join(job, "app.ts"), "utf8"), before);
  applyPatch(job, patch("*** Update File: app.ts", "@@ function innocent() {", "-  return false;", "+  return true;"));
  assert.match(readFileSync(join(job, "app.ts"), "utf8"), /return true/);
});

test("空行1行だけのファイルを作れる", () => {
  const { job } = place();
  applyPatch(job, patch("*** Add File: blank.txt", "+"));
  assert.equal(readFileSync(join(job, "blank.txt"), "utf8"), "\n");
});

test("生ログや結果の手紙を書けなくても、郵便局は落ちず、道具の返事は事実どおりで、実行中の印も外れる", async () => {
  const p = place();
  mkdirSync(join(p.residents, "Holo", "lifelog"), { recursive: true });
  writeFileSync(join(p.residents, "Holo", "lifelog", "hands"), "フォルダーの場所を塞ぐファイル");
  writeFileSync(join(p.residents, "Holo", "lifelog", "post"), "フォルダーの場所を塞ぐファイル");
  writeFileSync(join(p.job, "file.txt"), "old\n");

  // 待つ時間は、PowerShellの起動（混んでいると0.5秒を超える）より十分に長く、長いコマンドより短くする
  const h = hands(p, 3_000);
  assert.deepEqual(h.patch("Holo", "job", patch("*** Update File: file.txt", "-old", "+new")), ["M file.txt"]);
  assert.equal(readFileSync(join(p.job, "file.txt"), "utf8"), "new\n");
  assert.match(await h.run("Holo", "job", "'短い'"), /^終了コード 0[\s\S]*短い/);

  assert.match(await h.run("Holo", "job", "Start-Sleep -Milliseconds 6000; '長い'"), /続いている/);
  while (h.busy().size > 0) await new Promise(r => setTimeout(r, 100));
  assert.deepEqual(h.busy(), new Set());
});

test("作業場の名前の大文字と小文字が違っても、動いている間と、結果の手紙が済むまでは片付けない", async () => {
  const p = place();
  const finished: Line[] = [
    { kind: "letter", ts: new Date().toISOString(), id: "A", from: "Claude", to: "Holo", body: "頼む", work: "job" },
    { kind: "done", ts: new Date().toISOString(), letter: "A" },
  ];
  const h = hands(p, 50);
  assert.match(await h.run("Holo", "JOB", "Start-Sleep -Milliseconds 800; 'done'"), /続いている/);
  assert.deepEqual(toClean(folders(p.work), [finished], h.busy()), [], "動いている間");
  while (h.busy().size > 0) await new Promise(r => setTimeout(r, 50));
  assert.deepEqual(toClean(folders(p.work), [[...finished, ...readAll(p.residents, "Holo")]], h.busy()), [], "結果の手紙が済むまで");
});
