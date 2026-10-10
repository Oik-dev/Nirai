// 作業場（D:\Products\Work の下の依頼ごとのフォルダー）の寿命を、手紙に結び付ける。
// 名前を付けた手紙が届いたら作り、その名前を付けた手紙が全部済み、最後のdoneから1回の目覚めの長さがたち、
// その中で動いているコマンド（Holoの手）もなくなったら片付ける。
// 帳簿は持たず、毎回生ログから決める。
// 片付けはごみ箱を通さない。未取り込みのGitコミットだけは、本物のリポジトリのwork/枝に保存してから消す。

import { spawnSync } from "node:child_process";
import { existsSync, lstatSync, mkdirSync, readdirSync, rmSync, unlinkSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import type { Line } from "./letters.ts";
import { unfinished, workKey } from "./letters.ts";

export { workKey } from "./letters.ts";

/** 片付けてよい作業場の名前。生ログのdone時刻と時計、手のbusyだけで決める。 */
export function toClean(
  folders: string[], linesOfTeam: Line[][], busy: ReadonlySet<string> = new Set(), now = new Date(), keepMs = 0,
): string[] {
  const named = new Set<string>();
  const open = new Set<string>();
  const lastDone = new Map<string, number>();
  for (const lines of linesOfTeam) {
    const workOf = new Map<string, string>();
    for (const line of lines) {
      if (line.kind !== "letter" || !line.work) continue;
      const key = workKey(line.work);
      named.add(key);
      workOf.set(line.id, key);
    }
    for (const line of lines) {
      if (line.kind !== "done") continue;
      const key = workOf.get(line.letter);
      if (!key) continue;
      const at = Date.parse(line.ts);
      if (!Number.isFinite(at)) continue;
      lastDone.set(key, Math.max(lastDone.get(key) ?? -Infinity, at));
    }
    for (const letter of unfinished(lines)) if (letter.work) open.add(workKey(letter.work));
  }
  return folders.filter(name => {
    const key = workKey(name);
    const doneAt = lastDone.get(key);
    return named.has(key) && !open.has(key) && !busy.has(key)
      && doneAt !== undefined && now.getTime() - doneAt >= keepMs;
  });
}

export function ensureWork(workRoot: string, name: string): void {
  mkdirSync(workPath(workRoot, name), { recursive: true });
}

export function folders(workRoot: string): string[] {
  return existsSync(workRoot) ? readdirSync(workRoot, { withFileTypes: true }).filter(e => e.isDirectory()).map(e => e.name) : [];
}

/** 消す。未取り込みのGitコミットは本物のリポジトリのwork/枝に残す。
 *  保存できなければ作業場ごと残し、取り返せないコミットを消さない。
 *  Work 自身や作業場の直下がリンクなら触らない。途中で失敗しても次の見回りで試し直す。 */
export function removeWork(workRoot: string, name: string, repoRoot?: string): "removed" | "has-links" | "failed" {
  const path = workPath(workRoot, name);
  try {
    if (lstatSync(workRoot).isSymbolicLink() || lstatSync(path).isSymbolicLink()) return "has-links";
    if (!preserveGitHeads(path, name, repoRoot)) return "failed";
    detachLinks(path);
    // 再走査で残ったリンクがあれば、再帰削除に渡さない。
    if (hasLinks(path)) return "failed";
    rmSync(path, { recursive: true, maxRetries: 3 });
    return existsSync(path) ? "failed" : "removed";
  } catch {
    return "failed";
  }
}

/** .gitを持つ作業ツリーをすべて見つける。clone内の入れ子cloneも対象。
 *  .gitの管理ディレクトリとリンク先へは踏み込まない。 */
function gitTrees(path: string): string[] {
  const trees: string[] = existsSync(join(path, ".git")) ? [path] : [];
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    if (entry.name === ".git") continue;
    const child = join(path, entry.name);
    const stat = lstatSync(child);
    if (stat.isDirectory() && !stat.isSymbolicLink()) trees.push(...gitTrees(child));
  }
  return trees;
}

function git(cwd: string, ...args: string[]): { ok: boolean; out: string } {
  const result = spawnSync("git", ["-C", cwd, ...args], {
    encoding: "utf8", timeout: 15_000, windowsHide: true,
  });
  return { ok: result.status === 0, out: (result.stdout ?? "").trim() };
}

function refPart(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-").replace(/^-+|-+$/g, "") || "repo";
}

function preserveGitHeads(path: string, workName: string, repoRoot?: string): boolean {
  const trees = gitTrees(path);
  if (trees.length === 0) return true;
  if (!repoRoot) return false; // 行き先の本物が分からないなら、決して消さない
  for (const tree of trees) {
    const head = git(tree, "rev-parse", "--verify", "HEAD");
    if (!head.ok) return false;
    const branches = git(tree, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads");
    if (!branches.ok) return false;
    const refs = git(tree, "for-each-ref", "--format=%(refname)", "refs");
    const reflogs = git(tree, "reflog", "show", "--all", "--format=%H");
    if (!refs.ok || !reflogs.ok) return false;
    const parts = relative(path, tree).split(/[\\/]/).filter(Boolean).map(refPart);
    const base = ["work", refPart(workName), ...parts].join("/");
    // HEADが別の枝を指していても、元の先頭を保護する。
    if (!git(repoRoot, "merge-base", "--is-ancestor", head.out, "main").ok
        && !git(tree, "push", repoRoot, `HEAD:refs/heads/${base}`).ok) return false;
    // cloneではチェックアウトしていないローカル枝も複製とともに消える。
    // 現在のHEADと同じコミットは上で保存済みなので、別の先頭だけを保存する。
    for (const line of branches.out.split(/\r?\n/).filter(Boolean)) {
      const match = /^(refs\/heads\/[^ ]+) ([0-9a-f]{40,64})$/.exec(line);
      if (!match) return false;
      const [, sourceRef, oid] = match;
      if (oid === head.out || git(repoRoot, "merge-base", "--is-ancestor", oid, "main").ok) continue;
      const branchName = sourceRef.slice("refs/heads/".length);
      const destination = `${base}-branches/${branchName}`;
      if (!git(tree, "push", repoRoot, `${oid}:refs/heads/${destination}`).ok) return false;
    }
    // stash・タグだけの先頭・チェックアウト前のdetached HEADは
    // refs/headsにも現在のHEADにも現れない。全refsとreflogの記録を保護する。
    const saved = new Set([head.out]);
    for (const line of branches.out.split(/\r?\n/).filter(Boolean)) saved.add(line.split(" ").at(-1)!);
    const historical = new Set(reflogs.out.split(/\r?\n/).filter(Boolean));
    for (const ref of refs.out.split(/\r?\n/).filter(Boolean)) {
      const target = git(tree, "rev-parse", "--verify", `${ref}^{commit}`);
      // コミットでないタグなどはこの方法で安全に移せないため、削除しない。
      if (!target.ok) return false;
      historical.add(target.out);
    }
    for (const oid of historical) {
      if (!/^[0-9a-f]{40,64}$/.test(oid)) return false;
      if (saved.has(oid) || git(repoRoot, "merge-base", "--is-ancestor", oid, "main").ok) continue;
      if (!git(tree, "push", repoRoot, `${oid}:refs/heads/${base}-history/${oid}`).ok) return false;
    }
  }
  return true;
}

/** lstat で判別するので、Windows のジャンクションもリンク先へ入らない。 */
function detachLinks(dir: string): void {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    const stat = lstatSync(path);
    if (stat.isSymbolicLink()) unlinkSync(path);
    else if (stat.isDirectory()) detachLinks(path);
  }
}

function hasLinks(dir: string): boolean {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    const stat = lstatSync(path);
    if (stat.isSymbolicLink()) return true;
    if (stat.isDirectory() && hasLinks(path)) return true;
  }
  return false;
}

/** 作業場のフォルダー。Work の直下のフォルダー名でなければ投げる。 */
export function workPath(workRoot: string, name: string): string {
  const path = resolve(workRoot, name);
  const rel = relative(resolve(workRoot), path);
  if (!rel || rel.startsWith("..") || rel.includes("\\") || rel.includes("/")) throw new Error(`作業場の外: ${name}`);
  return path;
}
