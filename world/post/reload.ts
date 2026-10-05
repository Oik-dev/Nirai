// 本物の郵便局を、Gitの確定版から作った固定スナップショットで試してから安全に入れ替える。
// 本物の作業ツリーは試験にも実行にも使わない。外のgit・npm・子プロセスの不確実さはここで止める。

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { join } from "node:path";

export const RELOAD_EXIT_CODE = 75;

export type PostRevision = {
  head: string;
  post: string;
  lock: string;
};

export type Candidate = { root: string; revision: PostRevision };
export type ProbeResult = { ok: true; candidate: Candidate } | { ok: false; detail: string };

type ProcessResult = { status: number | null; stdout: string; stderr: string; error?: string };

async function runProcess(file: string, args: string[], options: { cwd?: string; timeoutMs?: number } = {}): Promise<ProcessResult> {
  return await new Promise(resolve => {
    const child = spawn(file, args, { cwd: options.cwd, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const remember = (current: string, chunk: unknown) => (current + String(chunk)).slice(-64_000);
    child.stdout?.on("data", chunk => (stdout = remember(stdout, chunk)));
    child.stderr?.on("data", chunk => (stderr = remember(stderr, chunk)));
    const finish = (result: ProcessResult) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      resolve(result);
    };
    child.once("error", error => finish({ status: null, stdout, stderr, error: error.message }));
    child.once("exit", status => finish({ status, stdout, stderr }));
    const timer = options.timeoutMs ? setTimeout(() => {
      killTree(child.pid);
      finish({ status: null, stdout, stderr, error: `timeout ${options.timeoutMs}ms` });
    }, options.timeoutMs) : undefined;
    timer?.unref();
  });
}

/** HEADにある郵便局・依存の版。読めないときは更新判定をしない。 */
export async function readPostRevision(repoRoot: string): Promise<PostRevision | undefined> {
  const result = await runProcess("git", ["-C", repoRoot, "rev-parse", "HEAD", "HEAD:world/post", "HEAD:world/package-lock.json"], { timeoutMs: 10_000 });
  if (result.status !== 0 || result.error) return undefined;
  const [head, post, lock] = result.stdout.trim().split(/\r?\n/);
  return head && post && lock ? { head, post, lock } : undefined;
}

export function sameRevision(a: PostRevision, b: PostRevision): boolean {
  return a.post === b.post && a.lock === b.lock;
}

export function revisionKey(revision: PostRevision): string {
  return `${revision.post}:${revision.lock}`;
}

export function postIdle(holoAwake: boolean, cliAwake: boolean[], busyWork: ReadonlySet<string>, activeRequests = 0): boolean {
  return !holoAwake && cliAwake.every(awake => !awake) && busyWork.size === 0 && activeRequests === 0;
}

export function encodeRevision(revision: PostRevision): string {
  return Buffer.from(JSON.stringify(revision), "utf8").toString("base64url");
}

export function decodeRevision(raw: string | undefined): PostRevision | undefined {
  if (!raw) return undefined;
  try {
    const value = JSON.parse(Buffer.from(raw, "base64url").toString("utf8")) as Partial<PostRevision>;
    return value.head && value.post && value.lock
      ? { head: value.head, post: value.post, lock: value.lock }
      : undefined;
  } catch {
    return undefined;
  }
}

async function cleanCandidates(parent: string, keepHeads: ReadonlySet<string>): Promise<void> {
  await mkdir(parent, { recursive: true });
  for (const entry of await readdir(parent, { withFileTypes: true })) {
    if (!entry.isDirectory() || keepHeads.has(entry.name)) continue;
    await rm(join(parent, entry.name), { recursive: true, force: true });
  }
}

/** Gitの確定コミットからworldだけを取り出し、そのlockの依存を候補自身へ入れる。 */
export async function prepareCandidate(
  repoRoot: string,
  runtimeDir: string,
  revision: PostRevision,
  keepHeads: string[] = [],
): Promise<Candidate> {
  const parent = join(runtimeDir, "post-candidates");
  const finalRoot = join(parent, revision.head);
  await cleanCandidates(parent, new Set([...keepHeads, revision.head]));
  const marker = join(finalRoot, "revision.json");
  if (existsSync(marker)) {
    try {
      const saved = JSON.parse(await readFile(marker, "utf8")) as PostRevision;
      if (saved.head === revision.head && saved.post === revision.post && saved.lock === revision.lock) {
        if (existsSync(join(finalRoot, "world", "node_modules"))) return { root: finalRoot, revision };
      }
    } catch { /* 作り直す */ }
    await rm(finalRoot, { recursive: true, force: true });
  }

  const staging = await mkdtemp(join(parent, `${revision.head.slice(0, 12)}-`));
  try {
    const archive = join(staging, "world.zip");
    const archived = await runProcess("git", ["-C", repoRoot, "archive", "--format=zip", `--output=${archive}`, revision.head, "world"], { timeoutMs: 60_000 });
    if (archived.status !== 0 || archived.error) throw new Error(`候補版の書き出しに失敗：${lastOutput(`${archived.stdout}\n${archived.stderr}\n${archived.error ?? ""}`)}`);
    const extracted = await runProcess("tar", ["-xf", archive, "-C", staging], { timeoutMs: 60_000 });
    if (extracted.status !== 0 || extracted.error) throw new Error(`候補版の展開に失敗：${lastOutput(`${extracted.stdout}\n${extracted.stderr}\n${extracted.error ?? ""}`)}`);
    await rm(archive, { force: true });
    const installed = await npmCi(join(staging, "world"), 180_000);
    if (!installed.ok) throw new Error(`候補版のnpm ciに失敗：${installed.detail}`);
    await writeFile(join(staging, "revision.json"), JSON.stringify(revision), "utf8");
    try {
      await rename(staging, finalRoot);
    } catch (error) {
      if (!existsSync(finalRoot)) throw error;
      await rm(staging, { recursive: true, force: true });
    }
    return { root: finalRoot, revision };
  } catch (error) {
    try { await rm(staging, { recursive: true, force: true }); } catch { /* 片付け失敗は元の失敗を隠さない */ }
    throw error;
  }
}

/** Windowsでは npm.cmd を直接spawnできないため、cmd.exeを入口にする。 */
export async function npmCi(cwd: string, timeoutMs: number): Promise<{ ok: boolean; detail: string }> {
  const file = process.platform === "win32" ? (process.env.ComSpec ?? "cmd.exe") : "npm";
  const args = process.platform === "win32"
    ? ["/d", "/s", "/c", "npm.cmd", "ci", "--prefer-offline", "--no-audit", "--no-fund"]
    : ["ci", "--prefer-offline", "--no-audit", "--no-fund"];
  const result = await runProcess(file, args, { cwd, timeoutMs });
  return { ok: result.status === 0 && !result.error, detail: lastOutput(`${result.stdout}\n${result.stderr}\n${result.error ?? ""}`) };
}

/** 候補版はserverだけでなくkeeperから起こし、番人ごと生きることを確かめる。 */
export async function probePostOffice(
  repoRoot: string,
  runtimeDir: string,
  from: PostRevision,
  to: PostRevision,
  timeoutMs = 15_000,
): Promise<ProbeResult> {
  let temp: string | undefined;
  let child: ChildProcess | undefined;
  try {
    const candidate = await prepareCandidate(repoRoot, runtimeDir, to, [from.head, to.head]);
    temp = mkdtempSync(join(runtimeDir, "post-probe-"));
    const port = await freePort();
    const residents = join(temp, "residents");
    const work = join(temp, "work");
    const probeRuntime = join(temp, "runtime");
    mkdirSync(residents, { recursive: true });
    mkdirSync(work, { recursive: true });
    mkdirSync(probeRuntime, { recursive: true });
    const keeper = join(candidate.root, "world", "post", "keeper.ts");
    let tail = "";
    child = spawn(process.execPath, ["--no-warnings", keeper], {
      cwd: join(candidate.root, "world"), windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        NIRAI_PORT: String(port), NIRAI_RESIDENTS: residents, NIRAI_WORK: work,
        NIRAI_KEEPER_LIVE: "0", NIRAI_SELF_RELOAD: "0", NIRAI_SKIP_LEFTOVERS: "1",
        NIRAI_SOURCE_REPO: repoRoot, NIRAI_RUNTIME_DIR: probeRuntime,
        NIRAI_CANDIDATE_ROOT: candidate.root, NIRAI_RUNNING_REVISION: encodeRevision(candidate.revision),
      },
    });
    const remember = (chunk: unknown) => (tail = (tail + String(chunk)).slice(-8000));
    child.stdout?.on("data", remember);
    child.stderr?.on("data", remember);

    const until = Date.now() + timeoutMs;
    while (Date.now() < until) {
      if (child.exitCode !== null) return { ok: false, detail: `候補版の番人が起動中に終了した（code=${child.exitCode}）：\n${lastOutput(tail)}` };
      const status = await fetchStatus(port, 800);
      if (status?.revision && revisionKey(status.revision) === revisionKey(to)) return { ok: true, candidate };
      await delay(150);
    }
    return { ok: false, detail: `候補版の番人が${timeoutMs}ms以内に正しい/holo/statusを返さなかった：\n${lastOutput(tail)}` };
  } catch (error) {
    return { ok: false, detail: (error as Error).message };
  } finally {
    if (child && child.exitCode === null) killTree(child.pid);
    if (temp) try { rmSync(temp, { recursive: true, force: true }); } catch { /* 合否は変えない */ }
  }
}

export type Handoff = Candidate;

export function writeHandoff(runtimeDir: string, candidate: Candidate): void {
  mkdirSync(runtimeDir, { recursive: true });
  const target = join(runtimeDir, "post-handoff.json");
  const temp = `${target}.tmp`;
  writeFileSync(temp, JSON.stringify(candidate), "utf8");
  renameSync(temp, target);
}

export function readHandoff(runtimeDir: string): Handoff | undefined {
  const path = join(runtimeDir, "post-handoff.json");
  try {
    const value = JSON.parse(readFileSync(path, "utf8")) as Handoff;
    return value?.root && value?.revision?.head ? value : undefined;
  } catch {
    return undefined;
  }
}

export function clearHandoff(runtimeDir: string): void {
  rmSync(join(runtimeDir, "post-handoff.json"), { force: true });
}

export class ReloadWatcher {
  private initial: PostRevision | undefined;
  private read: () => Promise<PostRevision | undefined>;
  private idle: () => boolean;
  private probe: (from: PostRevision, to: PostRevision) => Promise<ProbeResult>;
  private ready: (candidate: Candidate) => void;
  private rejected: (revision: PostRevision, detail: string) => void;
  private discard: (candidate: Candidate) => void;
  private checking = false;
  private rejectedKeys = new Set<string>();
  private verified = new Map<string, Candidate>();

  constructor(options: {
    initial: PostRevision | undefined;
    read: () => Promise<PostRevision | undefined>;
    idle: () => boolean;
    probe: (from: PostRevision, to: PostRevision) => Promise<ProbeResult>;
    ready: (candidate: Candidate) => void;
    rejected: (revision: PostRevision, detail: string) => void;
    discard?: (candidate: Candidate) => void;
  }) {
    this.initial = options.initial;
    this.read = options.read;
    this.idle = options.idle;
    this.probe = options.probe;
    this.ready = options.ready;
    this.rejected = options.rejected;
    this.discard = options.discard ?? (() => {});
  }

  /** 失敗を外へ投げない。旧郵便局を落とさないことが最優先。 */
  async check(): Promise<void> {
    if (this.checking) return;
    this.checking = true;
    try {
      if (!this.initial || !this.idle()) return;
      const current = await this.read();
      if (!current || sameRevision(this.initial, current)) return;
      const key = revisionKey(current);
      if (this.rejectedKeys.has(key)) return;
      const verified = this.verified.get(key);
      if (verified) {
        if (this.idle()) this.ready(verified);
        return;
      }

      let result: ProbeResult;
      try {
        result = await this.probe(this.initial, current);
      } catch (error) {
        result = { ok: false, detail: (error as Error).message };
      }
      const latest = await this.read();
      if (!latest || revisionKey(latest) !== key) {
        if (result.ok) this.discard(result.candidate);
        return;
      }
      if (!result.ok) {
        this.rejectedKeys.add(key);
        this.rejected(current, result.detail);
        return;
      }
      this.verified.set(key, result.candidate);
      if (this.idle()) this.ready(result.candidate);
    } catch (error) {
      const current = await this.read();
      if (current) {
        const key = revisionKey(current);
        if (!this.rejectedKeys.has(key)) {
          this.rejectedKeys.add(key);
          this.rejected(current, (error as Error).message);
        }
      }
    } finally {
      this.checking = false;
    }
  }
}

export async function fetchStatus(port: number, timeoutMs: number): Promise<{ revision?: PostRevision } | undefined> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/holo/status`, { signal: controller.signal });
    return response.ok ? await response.json() as { revision?: PostRevision } : undefined;
  } catch {
    return undefined;
  } finally {
    clearTimeout(timer);
  }
}

export function killTree(pid: number | undefined): void {
  if (!pid) return;
  if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
  else {
    try { process.kill(pid, "SIGKILL"); } catch { /* もう止まっている */ }
  }
}

async function freePort(): Promise<number> {
  const server = createNetServer();
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>(resolve => server.close(() => resolve()));
  if (!port) throw new Error("試し用の空きポートを取れなかった");
  return port;
}

function lastOutput(text: string): string {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  return lines.slice(-8).join("\n") || "出力なし";
}

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
