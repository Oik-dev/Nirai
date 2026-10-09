import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { show, take, type Suite } from "./land.ts";

function git(cwd: string, ...args: string[]): string {
  const result = spawnSync("git", ["-C", cwd, ...args], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, `git ${args.join(" ")}: ${result.stderr}`);
  return result.stdout.trim();
}

function write(dir: string, file: string, text: string, message?: string): string {
  writeFileSync(join(dir, file), text);
  if (!message) return "";
  git(dir, "add", file);
  git(dir, "commit", "-q", "-m", message);
  return git(dir, "rev-parse", "HEAD");
}

/** pushの先（bare）と、本物（main）と、作業場（本物からworktreeで作った枝）。 */
function repos() {
  const root = mkdtempSync(join(tmpdir(), "nirai-land-test-"));
  const origin = join(root, "origin.git");
  const real = join(root, "real");
  const work = join(root, "work");
  spawnSync("git", ["init", "-q", "--bare", origin]);
  spawnSync("git", ["init", "-q", "-b", "main", real]);
  git(real, "config", "user.name", "test");
  git(real, "config", "user.email", "test@example.com");
  const base = write(real, "a.txt", "a\n", "a");
  git(real, "remote", "add", "origin", origin);
  git(real, "push", "-q", "origin", "main");
  git(real, "worktree", "add", "-q", "-b", "stage", work);
  return { real, origin, work, base };
}

const passes: Suite[] = [{ area: "", run: () => ({ ok: true, out: "" }) }];
const failsOn = (word: string): Suite[] => [{
  area: "",
  run: (tree) => ({ ok: !readFileSync(join(tree, "b.txt"), "utf8").includes(word), out: `${word}が入っている` }),
}];
const worktrees = (real: string) => git(real, "worktree", "list").split("\n").length;

test("確かめた範囲だけを、本物のmainへff-onlyで入れてpushする", () => {
  const { real, origin, work, base } = repos();
  const head = write(work, "b.txt", "b\n", "b");
  assert.match(show(real, `${base}..${head}`).text, /\+b/);
  const landed = take(real, `${base}..${head}`, passes);
  assert.equal(landed.ok, true, landed.text);
  assert.equal(git(real, "rev-parse", "main"), head);
  assert.equal(git(origin, "rev-parse", "main"), head);
  assert.equal(worktrees(real), 2, "試しの置き場は片付ける");
  assert.equal(take(real, `${base}..${head}`, passes).ok, true, "入っている範囲をもう一度渡すと、pushだけする");
});

test("mainが進んでいたら、範囲だけをmainの上へ載せ替えて入れる", () => {
  const { real, work, base } = repos();
  const head = write(work, "b.txt", "b\n", "b");
  const other = write(real, "c.txt", "c\n", "c");
  const landed = take(real, `${base}..${head}`, passes);
  assert.equal(landed.ok, true, landed.text);
  assert.equal(git(real, "merge-base", "--is-ancestor", other, "main"), "");
  assert.equal(git(real, "show", "main:b.txt"), "b");
  assert.equal(git(real, "rev-list", "--count", "main"), "3");
});

test("前の段が載せ替えで入ったあと、その枝に積んだ次の段も取り込める", () => {
  const { real, work, base } = repos();
  const first = write(work, "b.txt", "b\n", "b");
  const second = write(work, "d.txt", "d\n", "d");
  write(real, "c.txt", "c\n", "c");
  assert.equal(take(real, `${base}..${first}`, passes).ok, true);
  const landed = take(real, `${first}..${second}`, passes);
  assert.equal(landed.ok, true, landed.text);
  assert.equal(git(real, "show", "main:d.txt"), "d");
  assert.equal(git(real, "rev-list", "--count", "main"), "4", "前の段を二重に入れない");
});

test("テストが落ちたら、mainもpushの先も動かさない", () => {
  const { real, origin, work, base } = repos();
  const head = write(work, "b.txt", "BROKEN\n", "b");
  const landed = take(real, `${base}..${head}`, failsOn("BROKEN"));
  assert.equal(landed.ok, false);
  assert.match(landed.text, /取り込まなかった/);
  assert.equal(git(real, "rev-parse", "main"), base);
  assert.equal(git(origin, "rev-parse", "main"), base);
  assert.equal(worktrees(real), 2);
});

test("本物の未記録の変更には触らない。ぶつかるときは取り込みのほうが止まる", () => {
  const { real, work, base } = repos();
  write(real, "note.txt", "Masterの書きかけ\n");
  write(real, "a.txt", "a（Masterが直し中）\n");
  const head = write(work, "b.txt", "b\n", "b");
  assert.equal(take(real, `${base}..${head}`, passes).ok, true);
  assert.equal(readFileSync(join(real, "note.txt"), "utf8"), "Masterの書きかけ\n");
  assert.equal(readFileSync(join(real, "a.txt"), "utf8"), "a（Masterが直し中）\n");

  const next = write(work, "a.txt", "a2\n", "a2");
  const blocked = take(real, `${head}..${next}`, passes);
  assert.equal(blocked.ok, false);
  assert.equal(git(real, "rev-parse", "main"), head);
  assert.equal(readFileSync(join(real, "a.txt"), "utf8"), "a（Masterが直し中）\n");
});

test("基準がまだmainにない範囲・載せ替えでぶつかる範囲・枝の名前は取り込まない", () => {
  const { real, work, base } = repos();
  const first = write(work, "b.txt", "b\n", "b");
  const second = write(work, "b.txt", "b2\n", "b2");
  assert.match(take(real, `${first}..${second}`, passes).text, /基準がまだmainにない/);
  assert.equal(take(real, `main..${second}`, passes).ok, false);
  write(real, "b.txt", "mainのb\n", "main b");
  const conflicted = take(real, `${base}..${second}`, passes);
  assert.match(conflicted.text, /ぶつかる/);
  assert.match(conflicted.text, /b\.txt/);
  assert.equal(worktrees(real), 2);
  assert.equal(git(real, "status", "--porcelain"), "");
});

test("別の段のtakeが同時に始まってもmainを壊さず、後着の非fast-forwardは止まる", async () => {
  const { real, origin, work, base } = repos();
  const otherWork = join(real, "..", "other-work");
  git(real, "worktree", "add", "-q", "-b", "other-stage", otherWork, base);
  const a = write(work, "one.txt", "A\n", "stage A");
  const b = write(otherWork, "two.txt", "B\n", "stage B");
  const modulePath = fileURLToPath(new URL("./land.ts", import.meta.url));
  function runTake(head: string): Promise<{ code: number | null; output: string }> {
    return new Promise(resolve => {
      const child = spawn(process.execPath, ["--input-type=module", "-e",
        `import { take } from ${JSON.stringify(new URL("./land.ts", import.meta.url).href)}; const r=take(process.argv[1],process.argv[2],[{area:'',run:()=>({ok:true,out:''})}]); console.log(JSON.stringify(r)); process.exit(r.ok?0:2)`,
        real, `${base}..${head}`], { windowsHide: true });
      let output = "";
      child.stdout.setEncoding("utf8").on("data", data => output += data);
      child.stderr.setEncoding("utf8").on("data", data => output += data);
      child.on("close", code => resolve({ code, output }));
    });
  }
  const [first, second] = await Promise.all([runTake(a), runTake(b)]);
  const winner = git(real, "rev-parse", "main");
  assert.ok([a, b].includes(winner), "mainはどちらか一方だけを取り込む");
  assert.equal(git(origin, "rev-parse", "main"), winner, "push先とも一致");
  assert.equal([first, second].filter(r => r.code === 0).length, 1, `同時takeの結果: ${first.output} / ${second.output}`);
  assert.equal(worktrees(real), 3, "一時作業場が残らない");
});
