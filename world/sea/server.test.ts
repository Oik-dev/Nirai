import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import test from 'node:test';
import { SEA_HOST, startSeaServer as startServer } from './server.ts';

import { seaSettings } from './settings.ts';

async function ideaTemp(prefix: string) {
  const root = await mkdtemp(prefix);
  const idea = join(root, 'Serina');
  await mkdir(idea);
  return idea;
}

function startSeaServer({ ideaRoot, mindPort, host, port }: { ideaRoot: string; mindPort?: number; host?: string; port: number }) {
  return startServer({ settings: { ...seaSettings({ NIRAI_RESIDENTS: dirname(ideaRoot), NIRAI_SOURCE_REPO: resolve('..') }), mindPort }, host, port });
}

function glb(extensions: object = { VRMC_vrm: { specVersion: '1.0' } }) {
  const source = Buffer.from(JSON.stringify({ asset: { version: '2.0' }, extensions }), 'utf8');
  const jsonLength = Math.ceil(source.length / 4) * 4;
  const bytes = Buffer.alloc(20 + jsonLength, 0x20);
  bytes.writeUInt32LE(0x46546c67, 0);
  bytes.writeUInt32LE(2, 4);
  bytes.writeUInt32LE(bytes.length, 8);
  bytes.writeUInt32LE(jsonLength, 12);
  bytes.writeUInt32LE(0x4e4f534a, 16);
  source.copy(bytes, 20);
  return bytes;
}

test('海は127.0.0.1だけで起動し、Avatarと静的ページだけを配る', async t => {
  const idea = await ideaTemp(join(tmpdir(), 'nirai-sea-'));
  await mkdir(join(idea, 'body'));
  const avatar = glb();
  await writeFile(join(idea, 'body', 'avatar.vrm'), avatar);
  t.after(() => rm(dirname(idea), { recursive: true, force: true }));

  const server = await startSeaServer({ ideaRoot: idea, port: 0 });
  t.after(() => new Promise(resolve => server.close(resolve)));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  assert.equal(address.address, SEA_HOST);
  const base = `http://${SEA_HOST}:${address.port}`;

  const health = await fetch(`${base}/sea/status`);
  assert.equal(health.status, 200);
  assert.match(
    health.headers.get('content-security-policy') ?? '',
    /connect-src 'self' data: blob:/,
  );

  const page = await fetch(`${base}/`);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /海の窓/);

  const model = await fetch(`${base}/avatar.vrm`);
  assert.equal(model.status, 200);
  assert.deepEqual(Buffer.from(await model.arrayBuffer()), avatar);

  const privateModule = await fetch(`${base}/node_modules/zod/index.js`);
  assert.equal(privateModule.status, 404);
});

test('海は覚えた動きをイデアの body/motions から配り、窓の読むパッケージだけを配る', async t => {
  const idea = await ideaTemp(join(tmpdir(), 'nirai-sea-'));
  await mkdir(join(idea, 'body', 'motions'), { recursive: true });
  await writeFile(join(idea, 'body', 'avatar.vrm'), glb());
  const motion = glb({ VRMC_vrm_animation: { specVersion: '1.0' } });
  for (const name of ['のびをする', '100%']) await writeFile(join(idea, 'body', 'motions', `${name}.vrma`), motion);
  t.after(() => rm(dirname(idea), { recursive: true, force: true }));

  const server = await startSeaServer({ ideaRoot: idea, port: 0 });
  t.after(() => new Promise(resolve => server.close(resolve)));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const base = `http://${SEA_HOST}:${address.port}`;

  for (const name of ['のびをする', '100%']) {
    const learned = await fetch(`${base}/motions/${encodeURIComponent(name)}.vrma`);
    assert.equal(learned.status, 200, name);
    assert.deepEqual(Buffer.from(await learned.arrayBuffer()), motion);
  }
  for (const path of ['/motions/nothing.vrma', '/motions/..%2Favatar.vrm.vrma', '/motions/%E0%A4%A.vrma']) {
    assert.notEqual((await fetch(base + path)).status, 200, path);
  }
  assert.equal((await fetch(`${base}/node_modules/@pixiv/three-vrm-animation/package.json`)).status, 200);
});

test('海は外向きのbindを拒否する', async () => {
  await assert.rejects(
    startSeaServer({ ideaRoot: 'C:\\dummy', host: '0.0.0.0', port: 0 }),
    /127\.0\.0\.1/,
  );
});

test('海は会話APIだけを精神へ中継し、本文を加工しない', async t => {
  const seen: Array<{ method?: string; url?: string; body: string }> = [];
  const mind = createServer((req, res) => {
    let body = '';
    req.setEncoding('utf8');
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      seen.push({ method: req.method, url: req.url, body });
      res.statusCode = 200;
      res.setHeader('Content-Type', req.url === '/api/events' ? 'text/event-stream' : 'application/x-ndjson');
      res.end(req.url === '/api/events' ? 'data: {"type":"said"}\n\n' : '{"type":"done"}\n');
    });
  });
  await new Promise<void>(resolve => mind.listen(0, SEA_HOST, resolve));
  t.after(() => new Promise(resolve => mind.close(resolve)));
  const mindAddress = mind.address();
  assert.ok(mindAddress && typeof mindAddress === 'object');

  const idea = await ideaTemp(join(tmpdir(), 'nirai-sea-proxy-'));
  await mkdir(join(idea, 'body'));
  await writeFile(join(idea, 'body', 'avatar.vrm'), glb());
  t.after(() => rm(dirname(idea), { recursive: true, force: true }));
  const sea = await startSeaServer({ ideaRoot: idea, port: 0, mindPort: mindAddress.port });
  t.after(() => new Promise(resolve => sea.close(resolve)));
  const seaAddress = sea.address();
  assert.ok(seaAddress && typeof seaAddress === 'object');
  const base = `http://${SEA_HOST}:${seaAddress.port}`;

  const chat = await fetch(`${base}/api/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text: '海の話' }),
  });
  assert.equal(chat.status, 200);
  assert.equal(await chat.text(), '{"type":"done"}\n');
  assert.deepEqual(seen[0], { method: 'POST', url: '/api/chat', body: '{"text":"海の話"}' });

  const blocked = await fetch(`${base}/api/private`);
  assert.equal(blocked.status, 404);
  const staticPost = await fetch(`${base}/`, { method: 'POST' });
  assert.equal(staticPost.status, 405);
});

test('窓がSSEを切っても海は精神側のSSEを受け続け、drainで閉じる', async t => {
  let closed = false;
  let connections = 0;
  const mind = createServer((req, res) => {
    if (req.url === '/api/perceive') { res.end('{}'); return; }
    connections++;
    res.statusCode = 200;
    res.setHeader('Content-Type', 'text/event-stream');
    res.write(': connected\n\n');
    res.on('close', () => { closed = true; });
  });
  await new Promise<void>(resolve => mind.listen(0, SEA_HOST, resolve));
  t.after(() => new Promise(resolve => mind.close(resolve)));
  const mindAddress = mind.address();
  assert.ok(mindAddress && typeof mindAddress === 'object');

  const idea = await ideaTemp(join(tmpdir(), 'nirai-sea-sse-'));
  await mkdir(join(idea, 'body'));
  await writeFile(join(idea, 'body', 'avatar.vrm'), glb());
  t.after(() => rm(dirname(idea), { recursive: true, force: true }));
  const sea = await startSeaServer({ ideaRoot: idea, port: 0, mindPort: mindAddress.port });
  t.after(() => sea.drain());
  const seaAddress = sea.address();
  assert.ok(seaAddress && typeof seaAddress === 'object');

  const controller = new AbortController();
  const response = await fetch(`http://${SEA_HOST}:${seaAddress.port}/api/events`, { signal: controller.signal });
  assert.equal(response.status, 200);
  const reader = response.body?.getReader();
  assert.ok(reader);
  await reader.read();
  controller.abort();
  for (let i = 0; i < 100 && !connections; i++) await new Promise(done => setTimeout(done, 10));
  assert.equal(connections, 1);
  await new Promise(done => setTimeout(done, 30));
  assert.equal(closed, false);
  await sea.drain();
  for (let i = 0; i < 100 && !closed; i++) await new Promise(done => setTimeout(done, 10));
  assert.equal(closed, true);
});

test('精神へ接続できないAPIは502を返す', async t => {
  const unavailable = createServer();
  await new Promise<void>(resolve => unavailable.listen(0, SEA_HOST, resolve));
  const unavailableAddress = unavailable.address();
  assert.ok(unavailableAddress && typeof unavailableAddress === 'object');
  await new Promise<void>(resolve => unavailable.close(() => resolve()));

  const idea = await ideaTemp(join(tmpdir(), 'nirai-sea-502-'));
  await mkdir(join(idea, 'body'));
  await writeFile(join(idea, 'body', 'avatar.vrm'), glb());
  t.after(() => rm(dirname(idea), { recursive: true, force: true }));
  const sea = await startSeaServer({ ideaRoot: idea, port: 0, mindPort: unavailableAddress.port });
  t.after(() => new Promise(resolve => sea.close(resolve)));
  const seaAddress = sea.address();
  assert.ok(seaAddress && typeof seaAddress === 'object');

  const response = await fetch(`http://${SEA_HOST}:${seaAddress.port}/api/conversation`);
  assert.equal(response.status, 502);
});
