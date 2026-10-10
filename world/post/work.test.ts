import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { append, type Line, trackKey } from "./letters.ts";
import { PostOffice } from "./office.ts";
import { ensureWork, removeWork, toClean } from "./work.ts";

const at = (s: number) => new Date(Date.UTC(2026, 9, 4, 0, 0, s)).toISOString();
const letter = (id: string, work: string): Line => ({ kind: "letter", ts: at(0), id, from: "Holo", to: "Codex", body: id, work });
const done = (id: string, s = 9): Line => ({ kind: "done", ts: at(s), letter: id });
const KEEP = 50 * 60_000;
const after = (line: Line, ms: number) => new Date(Date.parse(line.ts) + ms);

test("その名前の手紙が全部済んだ作業場だけを片付ける", () => {
  const codex = [letter("A", "review"), done("A"), letter("B", "build")];
  const claude = [letter("C", "review"), done("C", 20)];
  assert.deepEqual(toClean(["review", "build"], [codex, [letter("C", "review")]], new Set(), after(done("A"), KEEP + 1), KEEP), [], "Claude宛ての review がまだ済んでいない");
  assert.deepEqual(toClean(["review", "build"], [codex, claude], new Set(), after(done("C", 20), KEEP - 1), KEEP), [], "最新doneからkeepMs未満");
  assert.deepEqual(toClean(["review", "build"], [codex, claude], new Set(), after(done("C", 20), KEEP), KEEP), ["review"]);
});

test("コマンドが動いている作業場は、手紙が全部済んでも片付けない", () => {
  const codex = [letter("A", "review"), done("A")];
  const later = after(done("A"), KEEP + 1);
  assert.deepEqual(toClean(["review"], [codex], new Set(["review"]), later, KEEP), []);
  assert.deepEqual(toClean(["review"], [codex], new Set(), later, KEEP), ["review"]);
});

test("郵便局を起こし直しても、最後のdoneからkeepMsまでは同じ作業場を残す", () => {
  const lines = [letter("A", "review"), done("A")];
  assert.deepEqual(toClean(["review"], [lines], new Set(), after(done("A"), 61_000), KEEP), [], "再起動61秒後でも残す");
  assert.deepEqual(toClean(["review"], [lines], new Set(), after(done("A"), KEEP), KEEP), ["review"], "keepMs後は片付ける");
});

test("新しいPostOfficeに起こし直しても、61秒後は作業場を残し、keepMs後に片付ける", () => {
  const base = mkdtempSync(join(tmpdir(), "nirai-work-restart-"));
  const residentsRoot = join(base, "residents");
  const workRoot = join(base, "Work");
  ensureWork(workRoot, "review");
  append(residentsRoot, "Holo", letter("A", "review"));
  append(residentsRoot, "Holo", done("A"));
  const officeAfterRestart = new PostOffice({
    residentsRoot, workRoot, team: ["Holo"], tellMasterAfter: 3,
    sweepMs: 60_000, restMs: 60_000, workKeepMs: KEEP, limitWaitMs: 60 * 60_000,
  });

  officeAfterRestart.sweep(after(done("A"), 61_000));
  assert.equal(existsSync(join(workRoot, "review")), true, "再起動61秒後でも残る");
  officeAfterRestart.sweep(after(done("A"), KEEP));
  assert.equal(existsSync(join(workRoot, "review")), false, "keepMs後に片付く");
});

test("同じ作業場にdoneが複数あるときは、いちばん新しいdoneからkeepMsを数える", () => {
  const lines = [letter("A", "review"), done("A", 9), letter("B", "review"), done("B", 30)];
  assert.deepEqual(toClean(["review"], [lines], new Set(), after(done("A", 9), KEEP + 1), KEEP), [], "古いdone基準では消さない");
  assert.deepEqual(toClean(["review"], [lines], new Set(), after(done("B", 30), KEEP), KEEP), ["review"]);
});

test("手紙に出てこないフォルダーには触れない（Masterが置いたものなど）", () => {
  assert.deepEqual(toClean(["mine"], [[letter("A", "review"), done("A")]]), []);
});

test("作業場は Work の直下にだけ作る", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-work-"));
  ensureWork(root, "review");
  assert.equal(existsSync(join(root, "review")), true);
  for (const name of ["..", "..\\x", "a\\b", "a/b"]) assert.throws(() => ensureWork(root, name), name);
});

test("中のジャンクションだけを外し、リンク先は残して作業場を片付ける", () => {
  const base = mkdtempSync(join(tmpdir(), "nirai-links-"));
  const outside = join(base, "outside");
  mkdirSync(outside);
  writeFileSync(join(outside, "sentinel.txt"), "外の本物");
  const root = join(base, "Work");
  ensureWork(root, "inner");
  mkdirSync(join(root, "inner", "nested"));
  symlinkSync(outside, join(root, "inner", "nested", "link"), "junction");
  symlinkSync(outside, join(root, "itself"), "junction");

  assert.equal(removeWork(root, "inner"), "removed");
  assert.equal(existsSync(join(root, "inner")), false);
  assert.equal(removeWork(root, "itself"), "has-links");
  assert.equal(readFileSync(join(outside, "sentinel.txt"), "utf8"), "外の本物");
  assert.equal(existsSync(join(root, "itself")), true);

  ensureWork(root, "still-here");
  const linkedRoot = join(base, "linked-root");
  symlinkSync(root, linkedRoot, "junction");
  assert.equal(removeWork(linkedRoot, "still-here"), "has-links", "Work 自体がリンクなら触らない");
  assert.equal(existsSync(join(root, "still-here")), true);
});

test("ファイルシンボリックリンクの先は消さない", t => {
  const base = mkdtempSync(join(tmpdir(), "nirai-file-link-"));
  const externalFile = join(base, "important.txt");
  writeFileSync(externalFile, "大切なファイル");
  const root = join(base, "Work");
  ensureWork(root, "inner");
  try {
    symlinkSync(externalFile, join(root, "inner", "file-link"), "file");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EPERM") throw error;
    t.skip("Windowsがファイルシンボリックリンクの作成権限を拒否した");
    return;
  }
  assert.equal(removeWork(root, "inner"), "removed");
  assert.equal(existsSync(join(root, "inner")), false);
  assert.equal(readFileSync(externalFile, "utf8"), "大切なファイル");
});

test("走査中の削除やファイル操作の失敗でも、例外を上位へ投げない", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-work-error-"));
  assert.equal(removeWork(root, "gone"), "failed", "見回り後に消えた作業場");
  writeFileSync(join(root, "not-a-folder"), "file");
  assert.equal(removeWork(root, "not-a-folder"), "failed", "readdirSync に失敗");
  assert.equal(removeWork(join(root, "missing-root"), "gone"), "failed", "Work 自体の lstatSync に失敗");
});

test("作業場の名前は、大文字と小文字を区別しない（Windowsのフォルダーと同じ）", () => {
  assert.deepEqual(toClean(["Review"], [[letter("A", "review")]]), [], "済んでいない手紙がある");
  const later = after(done("A"), KEEP + 1);
  assert.deepEqual(toClean(["Review"], [[letter("A", "REVIEW"), done("A")]], new Set(), later, KEEP), ["Review"]);
  assert.deepEqual(toClean(["Review"], [[letter("A", "review"), done("A")]], new Set([trackKey("REVIEW")]), later, KEEP), [], "コマンドが動いている");
});

test("本物からたどれないworktreeとcloneのコミットは、片付け前に本物のwork/枝へ残す", () => {
  const base = mkdtempSync(join(tmpdir(), "nirai-work-save-"));
  const repo = join(base, "original");
  const workRoot = join(base, "Work");
  ensureWork(workRoot, "save");
  const git = (...args: string[]) => execFileSync("git", args, { encoding: "utf8" }).trim();
  git("init", "-b", "main", repo);
  git("-C", repo, "config", "user.email", "test@example.com");
  git("-C", repo, "config", "user.name", "Test");
  writeFileSync(join(repo, "base.txt"), "base");
  git("-C", repo, "add", ".");
  git("-C", repo, "commit", "-m", "base");
  const main = git("-C", repo, "rev-parse", "HEAD");
  const wt = join(workRoot, "save", "wt");
  const clone = join(workRoot, "save", "clone");
  git("-C", repo, "worktree", "add", "--detach", wt);
  git("clone", repo, clone);
  for (const tree of [wt, clone]) {
    git("-C", tree, "config", "user.email", "test@example.com");
    git("-C", tree, "config", "user.name", "Test");
    writeFileSync(join(tree, "new.txt"), tree === wt ? "wt" : "clone");
    git("-C", tree, "add", ".");
    git("-C", tree, "commit", "-m", "not landed");
  }
  const wtHead = git("-C", wt, "rev-parse", "HEAD");
  const cloneHead = git("-C", clone, "rev-parse", "HEAD");
  assert.equal(removeWork(workRoot, "save", repo), "removed");
  assert.equal(existsSync(join(workRoot, "save")), false);
  assert.equal(git("-C", repo, "rev-parse", `refs/heads/work/save/${wtHead}`), wtHead);
  assert.equal(git("-C", repo, "rev-parse", `refs/heads/work/save/${cloneHead}`), cloneHead);
  assert.equal(git("-C", repo, "rev-parse", "main"), main, "本物のmainは動かさない");
});

test("未取り込みを保護できないときは、作業場を削除しない", () => {
  const base = mkdtempSync(join(tmpdir(), "nirai-work-keep-git-"));
  const repo = join(base, "original");
  const workRoot = join(base, "Work");
  ensureWork(workRoot, "save");
  const git = (...args: string[]) => execFileSync("git", args, { encoding: "utf8" }).trim();
  git("init", "-b", "main", repo);
  git("-C", repo, "config", "user.email", "test@example.com");
  git("-C", repo, "config", "user.name", "Test");
  git("-C", repo, "commit", "--allow-empty", "-m", "base");
  const wt = join(workRoot, "save", "wt");
  git("-C", repo, "worktree", "add", "--detach", wt);
  writeFileSync(join(wt, "important.txt"), "not merged");
  git("-C", wt, "add", ".");
  git("-C", wt, "commit", "-m", "important");
  assert.equal(removeWork(workRoot, "save", join(base, "missing")), "failed");
  assert.equal(readFileSync(join(wt, "important.txt"), "utf8"), "not merged");
});

test("cloneで別の枝に残した未取り込みコミットを、mainに戻しても失わない", () => {
  const base = mkdtempSync(join(tmpdir(), "nirai-work-other-branch-"));
  const repo = join(base, "original");
  const workRoot = join(base, "Work");
  ensureWork(workRoot, "save");
  const git = (...args: string[]) => execFileSync("git", args, { encoding: "utf8" }).trim();
  git("init", "-b", "main", repo);
  git("-C", repo, "config", "user.email", "test@example.com");
  git("-C", repo, "config", "user.name", "Test");
  git("-C", repo, "commit", "--allow-empty", "-m", "base");
  const clone = join(workRoot, "save", "clone");
  git("clone", repo, clone);
  git("-C", clone, "config", "user.email", "test@example.com");
  git("-C", clone, "config", "user.name", "Test");
  git("-C", clone, "switch", "-c", "unmerged");
  writeFileSync(join(clone, "important.txt"), "not landed");
  git("-C", clone, "add", ".");
  git("-C", clone, "commit", "-m", "important");
  const important = git("-C", clone, "rev-parse", "HEAD");
  git("-C", clone, "switch", "main");

  assert.equal(removeWork(workRoot, "save", repo), "removed");
  assert.equal(existsSync(join(workRoot, "save")), false);
  assert.equal(
    git("-C", repo, "rev-parse", `refs/heads/work/save/${important}`),
    important,
    "作業中でないローカル枝も保護する",
  );
  assert.equal(git("-C", repo, "cat-file", "-t", important), "commit");
});

function historyFixture() {
  const base = mkdtempSync(join(tmpdir(), "nirai-work-history-"));
  const repo = join(base, "original");
  const workRoot = join(base, "Work");
  const git = (...args: string[]) => execFileSync("git", args, { encoding: "utf8" }).trim();
  git("init", "-b", "main", repo);
  git("-C", repo, "config", "user.email", "test@example.com");
  git("-C", repo, "config", "user.name", "Test");
  git("-C", repo, "commit", "--allow-empty", "-m", "base");
  ensureWork(workRoot, "save");
  const clone = join(workRoot, "save", "clone");
  git("clone", repo, clone);
  git("-C", clone, "config", "user.email", "test@example.com");
  git("-C", clone, "config", "user.name", "Test");
  const commit = (tree: string) => {
    writeFileSync(join(tree, "important.txt"), "only here");
    git("-C", tree, "add", ".");
    git("-C", tree, "commit", "-m", "important");
    return git("-C", tree, "rev-parse", "HEAD");
  };
  return { repo, workRoot, clone, git, commit };
}

test("stashだけのコミットも元repoへ保護してからcloneを消す", () => {
  const { repo, workRoot, clone, git } = historyFixture();
  writeFileSync(join(clone, "important.txt"), "stashed");
  git("-C", clone, "add", ".");
  git("-C", clone, "stash", "push", "-m", "valuable");
  const oid = git("-C", clone, "rev-parse", "refs/stash");
  assert.equal(removeWork(workRoot, "save", repo), "removed");
  assert.equal(git("-C", repo, "rev-parse", `refs/heads/work/save/${oid}`), oid);
});

test("タグだけに残したコミットも元repoへ保護する", () => {
  const { repo, workRoot, clone, git, commit } = historyFixture();
  git("-C", clone, "checkout", "--detach");
  const oid = commit(clone);
  git("-C", clone, "tag", "valuable");
  git("-C", clone, "switch", "main");
  assert.equal(removeWork(workRoot, "save", repo), "removed");
  assert.equal(git("-C", repo, "cat-file", "-t", oid), "commit");
  assert.equal(git("-C", repo, "rev-parse", `refs/heads/work/save/${oid}`), oid);
});

test("cloneの内側にある別のcloneの未取り込みコミットも保護する", () => {
  const { repo, workRoot, clone, git, commit } = historyFixture();
  const nested = join(clone, "nested");
  git("clone", repo, nested);
  git("-C", nested, "config", "user.email", "test@example.com");
  git("-C", nested, "config", "user.name", "Test");
  const oid = commit(nested);
  assert.equal(removeWork(workRoot, "save", repo), "removed");
  assert.equal(git("-C", repo, "rev-parse", `refs/heads/work/save/${oid}`), oid);
});

test("本物のworktreeは本物の枝を写さず、名前も本物に残さない。使い捨てのリポジトリも写さない", () => {
  const { repo, workRoot, git } = historyFixture();
  git("-C", repo, "switch", "-c", "feature");
  git("-C", repo, "commit", "--allow-empty", "-m", "not landed");
  git("-C", repo, "branch", "work/old/kept");
  git("-C", repo, "switch", "main");
  const wt = join(workRoot, "save", "wt");
  git("-C", repo, "worktree", "add", wt, "feature");
  const scratch = join(workRoot, "save", "tmp", "fixture");
  git("init", "-b", "main", scratch);
  git("-C", scratch, "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "--allow-empty", "-m", "throwaway");
  const refs = () => git("-C", repo, "for-each-ref", "--format=%(refname)", "refs/heads/work").split(/\r?\n/).filter(Boolean);
  assert.equal(removeWork(workRoot, "save", repo), "removed");
  assert.deepEqual(refs(), ["refs/heads/work/old/kept"], "枝は本物にあるので写さず、テストのリポジトリは本物の歴史でない");
  assert.equal(git("-C", repo, "worktree", "list", "--porcelain").split(/\r?\n/).filter(line => line.startsWith("worktree ")).length, 1);
});
