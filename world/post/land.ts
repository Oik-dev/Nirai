// Holoの確かめと取り込み（郵便の決まりの「Holoだけ」）。手の1回で、差分を見る・取り込むができる。
//   node D:\Products\Nirai\world\post\land.ts show <基準..先頭>
//   node D:\Products\Nirai\world\post\land.ts take <基準..先頭>
// takeは、確かめた範囲だけを本物のmainの上へ載せ替え、触った場所のテストを通し、mainをff-onlyで進めてpushする。
// 載せ替えとテストは使い捨てのworktreeで行い、本物の作業ツリーはgitの取り込みでしか変えない
// （gitはまだ記録されていない変更を上書きしないので、ぶつかれば取り込みのほうが止まる）。

import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { settings } from "./settings.ts";

type Step = { ok: boolean; out: string };
export type Suite = { area: string; run: (tree: string, repoRoot: string) => Step };
export type Landing = { ok: boolean; text: string };

const SHA = /^[0-9a-f]{7,40}$/i;

function run(file: string, args: string[], cwd: string, timeoutMs = 120_000): Step {
  const result = spawnSync(file, args, { cwd, encoding: "utf8", windowsHide: true, timeout: timeoutMs, maxBuffer: 256 * 1024 * 1024 });
  const out = `${result.stdout ?? ""}${result.stderr ?? ""}${result.error ? `\n${result.error.message}` : ""}`.trim();
  return { ok: result.status === 0 && !result.error, out };
}

function git(cwd: string, ...args: string[]): Step {
  return run("git", ["-c", "core.quotepath=false", "-C", cwd, ...args], cwd);
}

function tail(text: string, lines = 40): string {
  return text.split(/\r?\n/).slice(-lines).join("\n");
}

/** Windowsでは npm.cmd を直接spawnできないため、cmd.exeを入口にする。 */
function npm(cwd: string, args: string[], timeoutMs: number): Step {
  return process.platform === "win32"
    ? run(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", "npm.cmd", ...args], cwd, timeoutMs)
    : run("npm", args, cwd, timeoutMs);
}

/** 触った場所ごとのテスト。worldは候補の版と同じくlockから依存を入れ、mindは本物の.venvで回す。 */
export const suites: Suite[] = [
  {
    area: "world/",
    run: (tree) => {
      const world = join(tree, "world");
      const installed = npm(world, ["ci", "--prefer-offline", "--no-audit", "--no-fund"], 300_000);
      return installed.ok ? npm(world, ["test"], 900_000) : installed;
    },
  },
  {
    area: "mind/",
    run: (tree, repoRoot) => {
      const python = join(repoRoot, "mind", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
      if (!existsSync(python)) return { ok: false, out: `${python} がない` };
      return run(python, ["-m", "pytest", "tests/", "-q"], join(tree, "mind"), 900_000);
    },
  },
];

function commit(repoRoot: string, name: string): string | undefined {
  const found = git(repoRoot, "rev-parse", "--verify", "--quiet", `${name}^{commit}`);
  return found.ok ? found.out : undefined;
}

function isAncestor(repoRoot: string, older: string, newer: string): boolean {
  return git(repoRoot, "merge-base", "--is-ancestor", older, newer).ok;
}

/** 基準がmainに入っているか。前の段の枝に積んだ段では、前の段は載せ替えて取り込まれているので、
 *  同じ番号がなくても、同じ変更が全部mainにあれば入っているとみなす（git cherryの「-」）。 */
function inMain(repoRoot: string, base: string, main: string): boolean {
  if (isAncestor(repoRoot, base, main)) return true;
  const cherry = git(repoRoot, "cherry", main, base);
  return cherry.ok && cherry.out.split(/\r?\n/).filter(Boolean).every((line) => line.startsWith("-"));
}

/** 範囲はコミットの番号だけを受け付ける。枝の名前は、確かめたあとに動きうるので使わない。 */
function parseRange(repoRoot: string, range: string | undefined): { base: string; head: string } | string {
  const [base, head, extra] = (range ?? "").split("..");
  if (extra !== undefined || !SHA.test(base ?? "") || !SHA.test(head ?? "")) return "範囲は <基準..先頭> のコミットの番号で渡す（確かめの依頼状にあるもの）。";
  const b = commit(repoRoot, base);
  const h = commit(repoRoot, head);
  if (!b || !h) return `${!b ? base : head} というコミットが本物のリポジトリにない。`;
  if (!isAncestor(repoRoot, b, h)) return `${base} は ${head} の祖先ではない。`;
  return { base: b, head: h };
}

function header(repoRoot: string, base: string, head: string): string {
  const main = commit(repoRoot, "main") ?? "";
  const where = base === main ? "基準はmainの先頭"
    : inMain(repoRoot, base, main) ? "mainは基準より進んでいる（取り込みで載せ替える）"
    : "基準はまだmainにない（前の段が先）";
  const log = git(repoRoot, "log", "--reverse", "--format=%h %s", `${base}..${head}`).out;
  const stat = git(repoRoot, "diff", "--stat=120", `${base}..${head}`).out;
  return `## ${base.slice(0, 7)}..${head.slice(0, 7)}　${where}\n\n${log}\n\n${stat}`;
}

/** 確かめる材料を1回で出す。lockの差分は量だけ出す。 */
export function show(repoRoot: string, range: string | undefined): Landing {
  const parsed = parseRange(repoRoot, range);
  if (typeof parsed === "string") return { ok: false, text: parsed };
  const { base, head } = parsed;
  const diff = git(repoRoot, "diff", `${base}..${head}`, "--", ".", ":(exclude)**/package-lock.json").out;
  return { ok: true, text: `${header(repoRoot, base, head)}\n\n${diff}` };
}

function push(repoRoot: string, lines: string[]): Landing {
  const pushed = git(repoRoot, "push", "origin", "main");
  lines.push(pushed.ok ? "pushした。" : `pushできなかった（mainには入っている。もう一度takeすればpushだけやり直す）：\n${tail(pushed.out)}`);
  return { ok: pushed.ok, text: lines.join("\n") };
}

/** 確かめた範囲だけを本物のmainへ入れる。どこかで止まったら、mainは動かさない。 */
export function take(repoRoot: string, range: string | undefined, checks: Suite[] = suites): Landing {
  const parsed = parseRange(repoRoot, range);
  if (typeof parsed === "string") return { ok: false, text: parsed };
  const { base, head } = parsed;
  const lines = [header(repoRoot, base, head), ""];
  const stop = (why: string): Landing => ({ ok: false, text: [...lines, `取り込まなかった：${why}`].join("\n") });

  const branch = git(repoRoot, "symbolic-ref", "--short", "HEAD");
  if (branch.out !== "main") return stop(`本物の作業ツリーがmainにいない（${branch.out || "枝なし"}）。`);
  const main = commit(repoRoot, "main")!;
  if (isAncestor(repoRoot, head, main)) {
    lines.push("この範囲は、もうmainに入っている。");
    return push(repoRoot, lines);
  }
  if (!inMain(repoRoot, base, main)) return stop("基準がまだmainにない。前の段を先に取り込む。前の段の部屋へ『取り込んだらこの作業場へ知らせて』と手紙を出してから、今の手紙に済みの印を付ける。");

  const tree = mkdtempSync(join(tmpdir(), "nirai-land-"));
  try {
    const added = git(repoRoot, "worktree", "add", "--detach", tree, head);
    if (!added.ok) return stop(`試しの置き場を作れなかった：\n${tail(added.out)}`);
    if (base !== main) {
      const rebased = git(tree, "rebase", "--onto", main, base);
      if (!rebased.ok) {
        const conflicts = git(tree, "diff", "--name-only", "--diff-filter=U").out;
        git(tree, "rebase", "--abort");
        return stop(`mainの上へ載せ替えるとぶつかる。作った人に、mainの上で直してもらう：\n${conflicts || tail(rebased.out)}`);
      }
      lines.push(`mainの上へ載せ替えた（${main.slice(0, 7)}から）。`);
    }
    const landed = commit(tree, "HEAD")!;
    const touched = git(tree, "diff", "--name-only", `${main}..${landed}`).out.split(/\r?\n/).filter(Boolean);
    for (const suite of checks.filter((s) => touched.some((path) => path.startsWith(s.area)))) {
      const started = Date.now();
      const result = suite.run(tree, repoRoot);
      if (!result.ok) return stop(`${suite.area}のテストが落ちた：\n${tail(result.out)}`);
      lines.push(`${suite.area}のテストが通った（${Math.round((Date.now() - started) / 1000)}秒）。`);
    }
    const merged = git(repoRoot, "merge", "--ff-only", landed);
    if (!merged.ok) return stop(`本物のmainをff-onlyで進められなかった（未記録の変更とぶつかるか、途中でmainが進んだ）：\n${tail(merged.out)}`);
    lines.push(`mainを ${main.slice(0, 7)} から ${landed.slice(0, 7)} へ進めた。`);
    return push(repoRoot, lines);
  } finally {
    if (!git(repoRoot, "worktree", "remove", "--force", tree).ok) {
      rmSync(tree, { recursive: true, force: true });
      git(repoRoot, "worktree", "prune");
    }
  }
}

if (import.meta.main) {
  const [verb, range] = process.argv.slice(2);
  const result = verb === "show" ? show(settings.repoRoot, range)
    : verb === "take" ? take(settings.repoRoot, range)
    : { ok: false, text: "使い方：land.ts show|take <基準..先頭>" };
  process.stdout.write(`${result.text}\n`);
  process.exitCode = result.ok ? 0 : 1;
}
