// 本番の郵便局の番人。タスクスケジューラ「Nirai Post」がMasterのログオン時に起こす。
// 郵便局は本物の可変ファイルから直接起こさず、Gitの確定版から作った固定スナップショットで動かす。

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import {
  clearHandoff, decodeRevision, encodeRevision, fetchStatus, killTree, prepareCandidate, readHandoff,
  readPostRevision, RELOAD_EXIT_CODE, revisionKey, type Candidate,
} from "./reload.ts";
import { settings } from "./settings.ts";

const SOURCE_REPO_ROOT = settings.repoRoot;
const RUNTIME_DIR = process.env.NIRAI_RUNTIME_DIR ?? join(SOURCE_REPO_ROOT, "world", "runtime");
const LOG_FILE = join(RUNTIME_DIR, "post.log");
const RESTART_MS = 60_000;
const LIVE = process.env.NIRAI_KEEPER_LIVE !== "0";
const PORT = Number(process.env.NIRAI_PORT ?? 47800);

mkdirSync(RUNTIME_DIR, { recursive: true });
const log = createWriteStream(LOG_FILE, { flags: "a" });
const note = (text: string) => log.write(`${new Date().toISOString()} keeper: ${text}\n`);

/** タスクから起きたときだけ、前の番人と郵便局を片付ける。引継ぎで起きた番人は旧番人を残す。 */
function stopLeftovers(): void {
  if (process.env.NIRAI_SKIP_LEFTOVERS === "1") return;
  const script = `Get-CimInstance Win32_Process -Filter "Name='node.exe'" | ` +
    `Where-Object { $_.ProcessId -ne ${process.pid} -and $_.CommandLine -match 'post[\\\\/](keeper|server)\\.ts' } | ` +
    `ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }`;
  const result = spawnSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], {
    windowsHide: true, encoding: "utf8",
  });
  const stopped = result.stdout.trim().split(/\s+/).filter(Boolean);
  if (stopped.length) note(`stopped leftovers pid=${stopped.join(",")}`);
}

async function startupCandidate(): Promise<Candidate> {
  const suppliedRoot = process.env.NIRAI_CANDIDATE_ROOT;
  const suppliedRevision = decodeRevision(process.env.NIRAI_RUNNING_REVISION);
  if (suppliedRoot && suppliedRevision && existsSync(join(suppliedRoot, "world", "post", "server.ts"))) {
    return { root: suppliedRoot, revision: suppliedRevision };
  }
  const revision = await readPostRevision(SOURCE_REPO_ROOT);
  if (!revision) throw new Error("本物の郵便局のGit版を読めない");
  return await prepareCandidate(SOURCE_REPO_ROOT, RUNTIME_DIR, revision, [revision.head]);
}

function serverEnv(candidate: Candidate): NodeJS.ProcessEnv {
  return {
    ...process.env,
    NIRAI_SOURCE_REPO: SOURCE_REPO_ROOT,
    NIRAI_RUNTIME_DIR: RUNTIME_DIR,
    NIRAI_CANDIDATE_ROOT: candidate.root,
    NIRAI_RUNNING_REVISION: encodeRevision(candidate.revision),
    ...(LIVE ? {} : { NIRAI_SELF_RELOAD: process.env.NIRAI_SELF_RELOAD ?? "0" }),
  };
}

function run(candidate: Candidate): void {
  const server = join(candidate.root, "world", "post", "server.ts");
  const args = ["--no-warnings", server, ...(LIVE ? ["--live"] : [])];
  const child = spawn(process.execPath, args, {
    cwd: join(candidate.root, "world"), windowsHide: true, stdio: ["ignore", "pipe", "pipe"], env: serverEnv(candidate),
  });
  child.stdout.pipe(log, { end: false });
  child.stderr.pipe(log, { end: false });
  note(`started post office pid=${child.pid} revision=${candidate.revision.post}`);

  let ready = false;
  let startupFinished = false;
  void waitForCandidate(candidate, 15_000).then(ok => {
    startupFinished = true;
    ready = ok;
    if (ok) return note(`post office ready revision=${candidate.revision.post}`);
    note(`post office did not become ready revision=${candidate.revision.post}`);
    if (child.exitCode === null) killTree(child.pid);
  });

  child.on("exit", code => {
    if (code === RELOAD_EXIT_CODE && ready) return void reload(candidate);
    if (!ready) {
      const recover = () => {
        note(`post office failed before ready; retry in ${RESTART_MS / 1000}s`);
        setTimeout(() => run(candidate), RESTART_MS);
      };
      return startupFinished ? recover() : setTimeout(recover, 50);
    }
    note(`post office stopped code=${code}; restart in ${RESTART_MS / 1000}s`);
    setTimeout(() => run(candidate), RESTART_MS);
  });
}

async function reload(current: Candidate): Promise<void> {
  const next = readHandoff(RUNTIME_DIR);
  clearHandoff(RUNTIME_DIR);
  if (!next || !existsSync(join(next.root, "world", "post", "server.ts"))) {
    note("reload handoff missing or invalid; restore current verified version");
    return run(current);
  }

  await handOverKeeper(current, next);
}

async function handOverKeeper(current: Candidate, next: Candidate): Promise<void> {
  const keeper = join(next.root, "world", "post", "keeper.ts");
  const child = spawn(process.execPath, ["--no-warnings", keeper], {
    cwd: join(next.root, "world"), windowsHide: true, detached: true, stdio: "ignore",
    env: {
      ...process.env,
      NIRAI_SOURCE_REPO: SOURCE_REPO_ROOT, NIRAI_RUNTIME_DIR: RUNTIME_DIR,
      NIRAI_CANDIDATE_ROOT: next.root, NIRAI_RUNNING_REVISION: encodeRevision(next.revision),
      NIRAI_SKIP_LEFTOVERS: "1", NIRAI_KEEPER_LIVE: LIVE ? "1" : "0",
    },
  });
  child.unref();
  note(`candidate keeper pid=${child.pid}`);
  if (await waitForCandidate(next, 15_000)) {
    note(`keeper handoff complete revision=${next.revision.post}`);
    process.exit(0);
  }
  note("new keeper did not become ready; kill it and restore previous verified version");
  killTree(child.pid);
  run(current);
}

async function waitForCandidate(candidate: Candidate, timeoutMs: number): Promise<boolean> {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    const status = await fetchStatus(PORT, 700);
    if (status?.revision && revisionKey(status.revision) === revisionKey(candidate.revision)) return true;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  return false;
}

async function startOrRetry(): Promise<void> {
  try {
    run(await startupCandidate());
  } catch (error) {
    note(`could not prepare startup candidate: ${(error as Error).message}; retry in ${RESTART_MS / 1000}s`);
    setTimeout(() => void startOrRetry(), RESTART_MS);
  }
}

stopLeftovers();
void startOrRetry();
