// 番人は同じ固定候補から郵便局と海を別々に起こす。精神は海から切り離される。
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { createWriteStream, existsSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  clearHandoff, decodeRevision, encodeRevision, fetchSeaStatus, fetchStatus, killTree,
  prepareCandidate, readHandoff, readRevision, RELOAD_EXIT_CODE, revisionKey, type Candidate,
} from './reload.ts';
import { settings } from './settings.ts';
import { SEA_PORT } from '../sea/settings.ts';

export async function startKeeper({ restartMs = 60_000, startupMs = 15_000, drainMs = 180_000 } = {}) {
  const sourceRepo = settings.repoRoot;
  const runtime = process.env.NIRAI_RUNTIME_DIR ?? join(sourceRepo, 'world', 'runtime');
  const live = process.env.NIRAI_KEEPER_LIVE !== '0';
  const port = Number(process.env.NIRAI_PORT ?? 47800);
  const seaPort = Number(process.env.NIRAI_SEA_PORT ?? SEA_PORT);
  mkdirSync(runtime, { recursive: true });
  const log = createWriteStream(join(runtime, 'post.log'), { flags: 'a' });
  const seaLog = createWriteStream(join(runtime, 'sea.log'), { flags: 'a' });
  const note = (text: string) => log.write(`${new Date().toISOString()} keeper: ${text}\n`);
  let post: ChildProcess | undefined;
  let sea: ChildProcess | undefined;
  let postTimer: ReturnType<typeof setTimeout> | undefined;
  let seaTimer: ReturnType<typeof setTimeout> | undefined;
  let handingOver = false;

  function stopLeftovers() {
    if (process.env.NIRAI_SKIP_LEFTOVERS === '1' || process.platform !== 'win32') return;
    const script = `Get-CimInstance Win32_Process -Filter "Name='node.exe'" | `
      + `Where-Object { $_.ProcessId -ne ${process.pid} -and $_.CommandLine -match '(post[\\\\/](keeper|server)|sea[\\\\/]server)\\.ts' } | `
      + 'ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }';
    const result = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], {
      windowsHide: true, encoding: 'utf8',
    });
    const stopped = result.stdout?.trim().split(/\s+/).filter(Boolean) ?? [];
    if (stopped.length) note(`stopped leftovers pid=${stopped.join(',')}`);
  }

  async function startupCandidate(): Promise<Candidate> {
    const suppliedRoot = process.env.NIRAI_CANDIDATE_ROOT;
    const supplied = decodeRevision(process.env.NIRAI_RUNNING_REVISION);
    const revision = await readRevision(sourceRepo, supplied?.head ?? 'HEAD');
    if (!revision) throw new Error('本物のworldのGit版を読めない');
    if (suppliedRoot && supplied && existsSync(join(suppliedRoot, 'world', 'post', 'server.ts'))
        && existsSync(join(suppliedRoot, 'world', 'sea', 'server.ts'))) {
      return { root: suppliedRoot, revision };
    }
    return prepareCandidate(sourceRepo, runtime, revision, [revision.head]);
  }

  function env(candidate: Candidate): NodeJS.ProcessEnv {
    return {
      ...process.env, NIRAI_SOURCE_REPO: sourceRepo, NIRAI_RUNTIME_DIR: runtime,
      NIRAI_RESIDENTS: settings.residentsRoot, NIRAI_WORK: settings.workRoot,
      NIRAI_PORT: String(port), NIRAI_SEA_PORT: String(seaPort),
      NIRAI_CANDIDATE_ROOT: candidate.root, NIRAI_RUNNING_REVISION: encodeRevision(candidate.revision),
      ...(live ? {} : { NIRAI_SELF_RELOAD: process.env.NIRAI_SELF_RELOAD ?? '0' }),
    };
  }

  function runSea(candidate: Candidate) {
    const child = spawn(process.execPath, ['--no-warnings', join(candidate.root, 'world', 'sea', 'server.ts')], {
      cwd: join(candidate.root, 'world'), windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe', 'ipc'], env: env(candidate),
    });
    sea = child;
    child.stdout?.pipe(seaLog, { end: false });
    child.stderr?.pipe(seaLog, { end: false });
    note(`started sea pid=${child.pid} revision=${candidate.revision.sea}`);
    let stopped = false;
    const restart = () => {
      if (stopped) return;
      stopped = true;
      if (handingOver) return;
      note(`sea stopped; retry in ${restartMs}ms`);
      seaTimer = setTimeout(() => runSea(candidate), restartMs);
    };
    child.once('error', restart);
    child.once('exit', restart);
  }

  function runPost(candidate: Candidate) {
    const child = spawn(process.execPath, ['--no-warnings', join(candidate.root, 'world', 'post', 'server.ts'), ...(live ? ['--live'] : [])], {
      cwd: join(candidate.root, 'world'), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env: env(candidate),
    });
    post = child;
    child.stdout?.pipe(log, { end: false });
    child.stderr?.pipe(log, { end: false });
    note(`started post office pid=${child.pid} revision=${candidate.revision.post}`);
    let ready = false;
    let stopped = false;
    void waitFor(candidate, false).then(ok => {
      if (child !== post || stopped) return;
      ready = ok;
      if (!ok && child.exitCode === null) killTree(child.pid);
    });
    const ended = (code: number | null) => {
      if (stopped) return;
      stopped = true;
      if (handingOver) return;
      if (code === RELOAD_EXIT_CODE && ready) { void reload(candidate); return; }
      note(`post office stopped code=${code}; retry in ${restartMs}ms`);
      postTimer = setTimeout(() => runPost(candidate), restartMs);
    };
    child.once('error', () => ended(null));
    child.once('exit', ended);
  }

  async function waitFor(candidate: Candidate, both = true): Promise<boolean> {
    const until = Date.now() + startupMs;
    while (Date.now() < until) {
      const [status, seaStatus] = await Promise.all([
        fetchStatus(port, 700), both ? fetchSeaStatus(seaPort, 700) : Promise.resolve(undefined),
      ]);
      if (status?.revision && revisionKey(status.revision) === revisionKey(candidate.revision)
          && (!both || (seaStatus?.revision && revisionKey(seaStatus.revision) === revisionKey(candidate.revision)))) return true;
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    return false;
  }

  async function drainSea() {
    clearTimeout(seaTimer);
    const child = sea;
    if (!child || child.exitCode !== null || !child.pid) return;
    await new Promise<void>(resolveDrain => {
      const timer = setTimeout(() => {
        // 海の子である精神を巻き込まない。taskkill /T は使わない。
        try { process.kill(child.pid!); } catch { clearTimeout(timer); resolveDrain(); }
      }, drainMs);
      child.once('exit', () => { clearTimeout(timer); resolveDrain(); });
      if (child.connected) child.disconnect();
      else { try { process.kill(child.pid!); } catch { clearTimeout(timer); resolveDrain(); } }
    });
  }

  async function reload(current: Candidate) {
    const suppliedNext = readHandoff(runtime);
    clearHandoff(runtime);
    const nextRevision = suppliedNext && await readRevision(sourceRepo, suppliedNext.revision.head);
    if (!suppliedNext || !nextRevision || !existsSync(join(suppliedNext.root, 'world', 'post', 'keeper.ts'))
        || !existsSync(join(suppliedNext.root, 'world', 'sea', 'server.ts'))) {
      note('reload handoff missing or invalid; restore current verified post');
      runPost(current); return;
    }
    const next = { ...suppliedNext, revision: nextRevision };
    handingOver = true;
    clearTimeout(postTimer);
    await drainSea();
    const child = spawn(process.execPath, ['--no-warnings', join(next.root, 'world', 'post', 'keeper.ts')], {
      cwd: join(next.root, 'world'), windowsHide: true, detached: true, stdio: 'ignore',
      env: { ...env(next), NIRAI_SKIP_LEFTOVERS: '1', NIRAI_KEEPER_LIVE: live ? '1' : '0' },
    });
    let spawnFailed = false;
    child.once('error', () => { spawnFailed = true; });
    child.unref();
    note(`candidate keeper pid=${child.pid}`);
    if (!spawnFailed && await waitFor(next)) {
      note(`keeper handoff complete revision=${next.revision.head}`);
      process.exit(0);
    }
    note('new keeper did not become ready; restore previous verified post and sea');
    killTree(child.pid);
    handingOver = false;
    runPost(current); runSea(current);
  }

  async function startOrRetry() {
    try {
      const candidate = await startupCandidate();
      runPost(candidate); runSea(candidate);
    } catch (error) {
      note(`could not prepare world candidate: ${(error as Error).message}; retry in ${restartMs}ms`);
      postTimer = setTimeout(() => { void startOrRetry(); }, restartMs);
    }
  }

  stopLeftovers();
  await startOrRetry();
  return {
    async stop() {
      handingOver = true;
      clearTimeout(postTimer); clearTimeout(seaTimer);
      await drainSea();
      killTree(post?.pid);
      log.end(); seaLog.end();
    },
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) void startKeeper();
