import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import { startSeaServer } from './server.ts';
import { mindState, restartMind, seaResident, wakeMind } from './mind.ts';
import { seaSettings } from './settings.ts';

async function fixture(t: test.TestContext, body = true) {
  const root = await mkdtemp(join(tmpdir(), 'nirai-mind-test-'));
  const cleanup: Array<() => Promise<unknown> | void> = [];
  t.after(async () => {
    for (const stop of cleanup.reverse()) await stop();
    await rm(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 50 });
  });
  const residentsRoot = join(root, 'residents');
  const idea = join(residentsRoot, 'Serina');
  await mkdir(join(idea, 'body'), { recursive: true });
  await mkdir(join(root, 'mind', 'app'), { recursive: true });
  if (body) await writeFile(join(idea, 'body', 'avatar.vrm'), 'test body');
  const settings = { ...seaSettings({ NIRAI_RESIDENTS: residentsRoot, NIRAI_SOURCE_REPO: root }), python: process.execPath, script: join(root, 'mind', 'app', 'fake.mjs') };
  return { root, idea, settings, cleanup };
}

async function listen(server: ReturnType<typeof createServer>) {
  await new Promise<void>(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
  const addr = server.address();
  assert.ok(addr && typeof addr === 'object');
  return addr.port;
}

function base(server: Awaited<ReturnType<typeof startSeaServer>>) {
  const addr = server.address();
  assert.ok(addr && typeof addr === 'object');
  return `http://127.0.0.1:${addr.port}`;
}

test('住人の場所は必須で、本体の身体がない間は住人も起こす道もない', async t => {
  assert.throws(() => seaSettings({}), /NIRAI_RESIDENTS/);
  assert.throws(() => seaSettings({ NIRAI_RESIDENTS: 'test' }), /NIRAI_SOURCE_REPO/);
  const { settings, idea } = await fixture(t, false);
  assert.equal(await seaResident(settings), undefined);
  const sea = await startSeaServer({ settings, port: 0 });
  t.after(() => sea.drain());
  assert.deepEqual(await (await fetch(`${base(sea)}/sea/mind`)).json(), { resident: null, mind: 'down' });
  assert.equal((await fetch(`${base(sea)}/sea/mind/wake`, { method: 'POST' })).status, 404);
  await assert.rejects(readFile(join(idea, 'data', 'logs', 'mind.log')), /ENOENT/);
});

test('down時にserver.pyがなければ明確に失敗し、旧GUIには切り替わらない', async t => {
  const { settings } = await fixture(t);
  const unavailable = createServer();
  settings.mindPort = await listen(unavailable);
  await new Promise<void>(done => unavailable.close(() => done()));
  const sea = await startSeaServer({ settings, port: 0 });
  t.after(() => sea.drain());
  const response = await fetch(`${base(sea)}/sea/mind/wake`, { method: 'POST' });
  assert.equal(response.status, 502);
  assert.match(await response.text(), /server.py/);
  const forbidden = await fetch(`${base(sea)}/sea/mind/restart`, { method: 'POST', headers: { origin: 'http://example.invalid' } });
  assert.equal(forbidden.status, 403);
});

test('中継中のrestartは409、drainは返答を完走させSSEを閉じ、新しい接続を断る', async t => {
  const { settings } = await fixture(t);
  let finishReply!: () => void;
  let sseClosed = false;
  const mind = createServer((req, res) => {
    if (req.url === '/api/events') {
      res.setHeader('content-type', 'text/event-stream'); res.write(': connected\n\n');
      res.on('close', () => { sseClosed = true; }); return;
    }
    res.setHeader('content-type', 'application/x-ndjson');
    res.write('{"type":"token","text":"海"}\n');
    finishReply = () => res.end('{"type":"done","reply":"海の返事"}\n');
  });
  settings.mindPort = await listen(mind);
  t.after(() => new Promise(done => mind.close(done)));
  const sea = await startSeaServer({ settings, port: 0 });
  t.after(() => sea.drain(100));
  const url = base(sea);
  const sse = await fetch(`${url}/api/events`);
  const events = sse.body!.getReader();
  await events.read();
  const response = await fetch(`${url}/api/chat`, { method: 'POST', body: '{"text":"test"}' });
  const text = response.text();
  assert.equal((await (await fetch(`${url}/sea/status`)).json()).relaying, 1);
  assert.equal((await fetch(`${url}/sea/mind/restart`, { method: 'POST' })).status, 409);
  const drained = sea.drain();
  await assert.rejects(fetch(`${url}/api/chat`, { method: 'POST' }));
  finishReply();
  assert.equal(await text, '{"type":"token","text":"海"}\n{"type":"done","reply":"海の返事"}\n');
  await drained;
  await events.read().catch(() => undefined);
  for (let i = 0; i < 20 && !sseClosed; i++) await new Promise(done => setTimeout(done, 10));
  assert.equal(sseClosed, true);
});

test('精神の偽物をdetachedで起こし、親と海を止めても生存、restartとUTF8・ログ追記が働く', async t => {
  const { root, settings, idea, cleanup } = await fixture(t);
  const reservation = createServer();
  settings.mindPort = await listen(reservation);
  await new Promise<void>(done => reservation.close(() => done()));
  await writeFile(settings.script, `
import { createServer } from 'node:http';
console.log('🌊 fake mind ' + process.env.PYTHONUTF8);
console.error('fake stderr');
createServer((req, res) => {
  if (req.url === '/api/close') { res.end('{}'); setTimeout(() => process.exit(0), 20); return; }
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify({ pid: process.pid, idea: process.env.NIRAI_IDEA }));
}).listen(Number(process.env.NIRAI_MIND_PORT), '127.0.0.1');
`);
  const resident = (await seaResident(settings))!;
  cleanup.push(async () => {
    try { await fetch(`http://127.0.0.1:${resident.port}/api/close`, { method: 'POST' }); } catch {}
    for (let i = 0; i < 30 && await mindState(resident) === 'up'; i++) await new Promise(done => setTimeout(done, 20));
  });
  const seaEntry = join(root, 'sea.mjs');
  await writeFile(seaEntry, `
import { startSeaServer } from ${JSON.stringify(pathToFileURL(resolve('sea/server.ts')).href)};
const sea = await startSeaServer({ settings: ${JSON.stringify(settings)}, port: 0 });
process.on('disconnect', () => { void sea.drain().then(() => process.exit(0)); });
process.send({ port: sea.address().port, pid: process.pid });
`);
  const parentEntry = join(root, 'parent.mjs');
  await writeFile(parentEntry, `
import { spawn } from 'node:child_process';
const child = spawn(process.execPath, [${JSON.stringify(seaEntry)}], { windowsHide: true, stdio: ['ignore','ignore','ignore','ipc'] });
child.on('message', message => process.send(message));
process.on('message', () => { child.disconnect(); child.once('exit', () => process.exit(0)); });
`);
  const parent = spawn(process.execPath, [parentEntry], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe', 'ipc'] });
  cleanup.push(async () => {
    if (parent.exitCode !== null || parent.signalCode !== null) return;
    const exited = new Promise(done => parent.once('exit', done));
    parent.kill(); await exited;
  });
  const ready = await new Promise<{ port: number; pid: number }>((done, reject) => {
    parent.once('message', message => done(message as { port: number; pid: number }));
    parent.once('error', reject);
    parent.once('exit', code => reject(new Error(`fake keeper exited ${code}`)));
  });
  const wake = await fetch(`http://127.0.0.1:${ready.port}/sea/mind/wake`, { method: 'POST' });
  assert.equal(wake.status, 200, await wake.text());
  const state = await (await fetch(`http://127.0.0.1:${resident.port}/api/state`)).json();
  assert.equal(state.idea, idea);
  process.kill(ready.pid); // 木全体ではなく海のpidだけ。
  assert.equal(await mindState(resident), 'up');
  parent.kill();
  await new Promise(done => setTimeout(done, 100));
  assert.equal(await mindState(resident), 'up');
  await wakeMind(settings, resident); // upなら新しく起こさない。
  assert.equal((await (await fetch(`http://127.0.0.1:${resident.port}/api/state`)).json()).pid, state.pid);
  await restartMind(settings, resident);
  assert.notEqual((await (await fetch(`http://127.0.0.1:${resident.port}/api/state`)).json()).pid, state.pid);
  const output = await readFile(join(idea, 'data', 'logs', 'mind.log'), 'utf8');
  assert.equal(output.split('🌊 fake mind 1').length - 1, 2);
  assert.match(output, /fake stderr/);
  await assert.rejects(readFile(join(root, 'world', 'runtime', 'sea.log')), /ENOENT/);
});

test('実際のIPC切断で海がdrainし、中継中の返答を届けてからプロセスを終える', async t => {
  const { settings, cleanup } = await fixture(t);
  let finishReply!: () => void;
  const mind = createServer((_req, res) => {
    res.writeHead(200, { 'content-type': 'application/x-ndjson' });
    res.write('{"type":"token","text":"続き"}\n');
    finishReply = () => res.end('{"type":"done"}\n');
  });
  settings.mindPort = await listen(mind);
  cleanup.push(() => new Promise(done => mind.close(done)));
  const reservation = createServer();
  const port = await listen(reservation);
  await new Promise<void>(done => reservation.close(() => done()));
  const child = spawn(process.execPath, ['--no-warnings', resolve('sea/server.ts')], {
    windowsHide: true, stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    env: { ...process.env, NIRAI_SOURCE_REPO: settings.sourceRepo, NIRAI_RESIDENTS: settings.residentsRoot,
      NIRAI_SEA_PORT: String(port), NIRAI_MIND_PORT: String(settings.mindPort) },
  });
  cleanup.push(async () => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    const exited = new Promise(done => child.once('exit', done));
    child.kill(); await exited;
  });
  const url = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let i = 0; i < 100 && !ready; i++) {
    ready = await fetch(`${url}/sea/status`).then(response => response.ok).catch(() => false);
    if (!ready) await new Promise(done => setTimeout(done, 20));
  }
  assert.equal(ready, true);
  const reply = await fetch(`${url}/api/chat`, { method: 'POST', body: '{}' });
  const text = reply.text();
  const exited = new Promise(done => child.once('exit', done));
  child.disconnect();
  await new Promise(done => setTimeout(done, 30));
  await assert.rejects(fetch(`${url}/api/chat`, { method: 'POST' }));
  assert.equal(child.exitCode, null);
  finishReply();
  assert.equal(await text, '{"type":"token","text":"続き"}\n{"type":"done"}\n');
  await exited;
  assert.equal(child.exitCode, 0);
});
