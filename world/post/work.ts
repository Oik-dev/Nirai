// 作業場（D:\Products\Work の下の依頼ごとのフォルダー）の寿命を、手紙に結び付ける。
// 名前を付けた手紙が届いたら作り、その名前を付けた手紙が全部済み、最後のdoneから1回の目覚めの長さがたち、
// その中で動いているコマンド（Holoの手）もなくなったら片付ける。
// 帳簿は持たず、毎回生ログから決める。
// 片付けはごみ箱を通さない。本物からたどれないGitコミットだけは、本物のリポジトリのwork/枝に保存してから消す。

import { spawnSync } from "node:child_process";
import { existsSync, lstatSync, mkdirSync, readdirSync, rmSync, unlinkSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import type { Line } from "./letters.ts";
import { trackKey, unfinished } from "./letters.ts";

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
      if (line.kind !== "letter") continue;
      const key = trackKey(line.work);
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
    for (const letter of unfinished(lines)) open.add(trackKey(letter.work));
  }
  return folders.filter(name => {
    const key = trackKey(name);
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

/** 消す。本物からたどれないGitコミットは本物のリポジトリのwork/枝に残す。
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
    if (existsSync(path)) return "failed";
    // 消したworktreeの名前だけが本物に残らないようにする。
    if (repoRoot) git(repoRoot, ["worktree", "prune"]);
    return "removed";
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

function git(cwd: string, args: string[], input?: string): { ok: boolean; out: string[] } {
  const result = spawnSync("git", ["-C", cwd, ...args], {
    encoding: "utf8", timeout: 15_000, windowsHide: true, input,
  });
  return { ok: result.status === 0, out: (result.stdout ?? "").split(/\r?\n/).filter(Boolean) };
}

function refPart(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-").replace(/^-+|-+$/g, "") || "repo";
}

/** 作業場の中のコミットのうち、本物の歴史につながり、本物のどのrefからもたどれないものを
 *  work/<作業場>/<コミット>の枝に残す。見るのは各リポジトリのHEADと全ref（枝・stash・タグ）。
 *  本物のworktreeは枝を本物と共有するので、残るのはdetachedの先頭くらいになる。
 *  本物の歴史につながらないリポジトリ（テストが作った使い捨てなど）は守るものでないので写さない。
 *  一度残せば本物からたどれるので、何度片付け直しても枝は増えない。 */
function preserveGitHeads(path: string, workName: string, repoRoot?: string): boolean {
  const trees = gitTrees(path);
  if (trees.length === 0) return true;
  if (!repoRoot) return false; // 行き先の本物が分からないなら、決して消さない
  const roots = git(repoRoot, ["rev-list", "--max-parents=0", "--all"]);
  if (!roots.ok) return false;
  const ours = new Set(roots.out);
  for (const tree of trees) {
    // --allは他のworktreeのHEADまで含むので、そのリポジトリのHEADとrefs/だけを見る。
    const head = git(tree, ["rev-parse", "--verify", "-q", "HEAD"]);
    const heads = git(tree, ["rev-list", "--no-walk", "--glob=refs/*", ...head.out]);
    if (!heads.ok) return false;
    const loose = unreachable(repoRoot, heads.out);
    if (!loose) return false;
    const keep: string[] = [];
    for (const oid of loose) {
      const own = git(tree, ["rev-list", "--max-parents=0", oid]);
      if (!own.ok) return false;
      if (own.out.some(root => ours.has(root))) keep.push(oid);
    }
    if (keep.length === 0) continue;
    const refspecs = keep.map(oid => `${oid}:refs/heads/work/${refPart(workName)}/${oid}`);
    if (!git(tree, ["push", repoRoot, ...refspecs]).ok) return false;
  }
  return true;
}

/** 本物にないか、本物のどのrefからもたどれないコミット。worktreeのHEADは消えるので数えない。 */
function unreachable(repoRoot: string, oids: string[]): string[] | undefined {
  if (oids.length === 0) return [];
  const known = git(repoRoot, ["cat-file", "--batch-check=%(objectname) %(objecttype)"], oids.join("\n") + "\n");
  if (!known.ok) return undefined;
  const present = known.out.filter(line => line.endsWith(" commit")).map(line => line.split(" ")[0]);
  const outside = present.length === 0 ? { ok: true, out: [] }
    : git(repoRoot, ["rev-list", "--stdin", "--not", "--glob=refs/*"], present.join("\n") + "\n");
  if (!outside.ok) return undefined;
  const has = new Set(present);
  const loose = new Set(outside.out);
  return oids.filter(oid => !has.has(oid) || loose.has(oid));
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
