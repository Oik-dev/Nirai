import assert from 'node:assert/strict';
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import { encodeRevision, fetchSeaStatus, fetchStatus, killTree, probePostOffice, readRevision, revisionKey, type Revision } from './reload.ts';

const world = resolve('.');
const delay = (ms: number) => new Promise(done => setTimeout(done, ms));

function run(file: string, args: string[], cwd: string) {
  const result = spawnSync(file, args, { cwd, encoding: 'utf8', windowsHide: true });
  assert.equal(result.status, 0, `${file}: ${result.stderr}`);
  return result.stdout.trim();
}

async function freePort() {
  const server = createServer();
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done));
  const addr = server.address();
  assert.ok(addr && typeof addr === 'object');
  await new Promise<void>(done => server.close(() => done()));
  return addr.port;
}

function copyWorld(target: string) {
  mkdirSync(join(target, 'world'), { recursive: true });
  for (const name of ['post', 'sea', 'window', 'package.json', 'package-lock.json']) {
    cpSync(join(world, name), join(target, 'world', name), { recursive: true });
  }
}

function dependencies(target: string) {
  symlinkSync(join(world, 'node_modules'), join(target, 'world', 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
}

async function fixture(t: test.TestContext, old = false) {
  const root = mkdtempSync(join(tmpdir(), 'nirai-keeper-test-'));
  const children: ChildProcess[] = [];
  const extraPids: number[] = [];
  const runtime = join(root, 'runtime');
  const repo = join(root, 'source');
  mkdirSync(repo); mkdirSync(runtime);
  t.after(async () => {
    try {
      const output = readFileSync(join(runtime, 'post.log'), 'utf8');
      extraPids.push(...[...output.matchAll(/candidate keeper pid=(\d+)/g)].map(match => Number(match[1])));
    } catch {}
    for (const child of children) if (child.exitCode === null && child.signalCode === null) killTree(child.pid);
    for (const pid of extraPids) killTree(pid);
    await delay(100);
    rmSync(root, { recursive: true, force: true, maxRetries: 20, retryDelay: 50 });
  });
  if (old) {
    const archive = join(root, 'old.zip');
    // A2の旧番人を固定する。この実装をcommitした後も、旧→新の移行を試す。
    run('git', ['archive', '--format=zip', `--output=${archive}`, '55f3a5c', 'world'], resolve('..'));
    run('tar', ['-xf', archive, '-C', repo], root);
  } else copyWorld(repo);
  run('git', ['init', '-q'], repo);
  const commit = () => {
    run('git', ['add', 'world'], repo);
    run('git', ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], repo);
  };
  commit();
  const residents = join(root, 'residents');
  const work = join(root, 'work');
  mkdirSync(residents); mkdirSync(work);
  const port = await freePort();
  let seaPort = await freePort();
  while (seaPort === port) seaPort = await freePort();
  const env = {
    ...process.env, NIRAI_SOURCE_REPO: repo, NIRAI_RESIDENTS: residents, NIRAI_WORK: work,
    NIRAI_RUNTIME_DIR: runtime, NIRAI_PORT: String(port), NIRAI_SEA_PORT: String(seaPort),
    NIRAI_KEEPER_LIVE: '0', NIRAI_SELF_RELOAD: '0', NIRAI_SKIP_LEFTOVERS: '1', NIRAI_SWEEP_MS: '100',
  };
  const snapshot = (revision: Revision) => {
    const target = join(runtime, 'post-candidates', revision.head);
    mkdirSync(target, { recursive: true });
    cpSync(join(repo, 'world'), join(target, 'world'), { recursive: true });
    dependencies(target);
    writeFileSync(join(target, 'revision.json'), JSON.stringify(revision));
    return { root: target, revision };
  };
  return { root, repo, runtime, port, seaPort, env, children, extraPids, snapshot, commit };
}

async function until(check: () => Promise<boolean>, ms = 12_000) {
  const deadline = Date.now() + ms;
  do { if (await check()) return; await delay(50); } while (Date.now() < deadline);
  assert.fail('使い捨ての番人が時間内に目的の状態にならなかった');
}

test('番人の海だけをkillしても郵便局は続き、海が同じ固定版で戻る', async t => {
  const f = await fixture(t);
  const revision = (await readRevision(f.repo))!;
  const candidate = f.snapshot(revision);
  const wrapper = join(f.root, 'keeper.mjs');
  writeFileSync(wrapper, `import { startKeeper } from ${JSON.stringify(pathToFileURL(join(candidate.root, 'world', 'post', 'keeper.ts')).href)};
const keeper = await startKeeper({ restartMs: 100, drainMs: 1000 });
process.on('message', () => { void keeper.stop().then(() => process.exit(0)); });`);
  // 旧形式の版＋誤ったpost/lockは、headからの再構築で受け止める。
  const legacy = Buffer.from(JSON.stringify({ head: revision.head, post: 'wrong', lock: 'wrong' })).toString('base64url');
  const child = spawn(process.execPath, ['--no-warnings', wrapper], {
    windowsHide: true, stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    env: { ...f.env, NIRAI_CANDIDATE_ROOT: candidate.root, NIRAI_RUNNING_REVISION: legacy },
  });
  f.children.push(child);
  await until(async () => !!(await fetchStatus(f.port, 300))?.revision && !!(await fetchSeaStatus(f.seaPort, 300))?.revision);
  const status = (await fetchStatus(f.port, 300))!;
  assert.deepEqual(status.revision, revision);
  const seaPids = () => [...readFileSync(join(f.runtime, 'post.log'), 'utf8').matchAll(/started sea pid=(\d+)/g)].map(match => Number(match[1]));
  const initialPid = seaPids()[0];
  assert.ok(initialPid);
  process.kill(initialPid);
  assert.deepEqual((await fetchStatus(f.port, 300))?.revision, revision);
  await until(async () => seaPids().length >= 2 && !!(await fetchSeaStatus(f.seaPort, 300))?.revision);
  assert.notEqual(seaPids().at(-1), initialPid);
  assert.deepEqual((await fetchSeaStatus(f.seaPort, 300))?.revision, revision);
  const exited = new Promise(done => child.once('exit', done));
  child.send('stop'); await exited;
});

test('probeは郵便局と海の両方を別ポートで起こし、海の版が違えば拒否する', async t => {
  const f = await fixture(t);
  const revision = (await readRevision(f.repo))!;
  f.snapshot(revision);
  // ランタイムだけの別ポート・使い捨て住人なので、本番の海は見ない。
  const original = process.env.NIRAI_SOURCE_REPO;
  const result = await probePostOffice(f.repo, f.runtime, revision, revision);
  assert.equal(result.ok, true, result.ok ? '' : result.detail);
  assert.equal(process.env.NIRAI_SOURCE_REPO, original);
  writeFileSync(join(f.repo, 'world', 'sea', 'server.ts'), `import { createServer } from 'node:http';
createServer((_req,res) => { res.setHeader('content-type','application/json'); res.end(JSON.stringify({revision:{head:'wrong',post:'wrong',sea:'wrong',window:'wrong',lock:'wrong'},relaying:0})); }).listen(Number(process.env.NIRAI_SEA_PORT),'127.0.0.1');`);
  f.commit();
  const wrong = (await readRevision(f.repo))!;
  f.snapshot(wrong);
  const rejected = await probePostOffice(f.repo, f.runtime, revision, wrong, 2500);
  assert.equal(rejected.ok, false);
});

test('旧番人から新版へ引き継ぎ、郵便局の旧post:lock契約と海の新版確認が通る', async t => {
  const f = await fixture(t, true);
  const before = (await readRevision(f.repo))!;
  const old = f.snapshot(before);
  const child = spawn(process.execPath, ['--no-warnings', join(old.root, 'world', 'post', 'keeper.ts')], {
    windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...f.env, NIRAI_SELF_RELOAD: '1', NIRAI_CANDIDATE_ROOT: old.root, NIRAI_RUNNING_REVISION: encodeRevision(before) },
  });
  f.children.push(child);
  await until(async () => (await fetchStatus(f.port, 300))?.revision?.head === before.head
    && readFileSync(join(f.runtime, 'post.log'), 'utf8').includes('post office ready revision='));
  copyWorld(f.repo); f.commit();
  const next = (await readRevision(f.repo))!;
  f.snapshot(next);
  try {
    await until(async () => {
      const status = await fetchStatus(f.port, 300);
      const sea = await fetchSeaStatus(f.seaPort, 300);
      return status?.revision?.head === next.head && sea?.revision?.head === next.head;
    }, 30_000);
  } catch (error) {
    throw new Error(`${error}\n${readFileSync(join(f.runtime, 'post.log'), 'utf8').slice(-7000)}`);
  }
  const output = readFileSync(join(f.runtime, 'post.log'), 'utf8');
  const keeperPids = [...output.matchAll(/candidate keeper pid=(\d+)/g)].map(match => Number(match[1]));
  f.extraPids.push(...keeperPids);
  assert.ok(keeperPids.length);
  assert.equal(revisionKey((await fetchStatus(f.port, 300))!.revision!), revisionKey(next));
  try { await until(async () => child.exitCode !== null || child.signalCode !== null); }
  catch (error) { throw new Error(`${error}; exit=${child.exitCode} signal=${child.signalCode}\n${readFileSync(join(f.runtime, 'post.log'), 'utf8').slice(-7000)}`); }
  assert.match(readFileSync(join(f.runtime, 'post.log'), 'utf8'), /keeper handoff complete/);
  assert.deepEqual((await fetchStatus(f.port, 300))?.revision, next);
  assert.deepEqual((await fetchSeaStatus(f.seaPort, 300))?.revision, next);
});
