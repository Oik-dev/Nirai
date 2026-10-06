import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { SEA_HOST, startSeaServer } from './server.ts';

function glb() {
  const source = Buffer.from(JSON.stringify({
    asset: { version: '2.0' },
    extensions: { VRMC_vrm: { specVersion: '1.0' } },
  }), 'utf8');
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
  const idea = await mkdtemp(join(tmpdir(), 'nirai-sea-'));
  await mkdir(join(idea, 'body'));
  const avatar = glb();
  await writeFile(join(idea, 'body', 'avatar.vrm'), avatar);
  t.after(() => rm(idea, { recursive: true, force: true }));

  const server = await startSeaServer({ ideaRoot: idea, port: 0 });
  t.after(() => new Promise(resolve => server.close(resolve)));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  assert.equal(address.address, SEA_HOST);
  const base = `http://${SEA_HOST}:${address.port}`;

  const health = await fetch(`${base}/health`);
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

test('海は外向きのbindを拒否する', async () => {
  await assert.rejects(
    startSeaServer({ ideaRoot: 'C:\\dummy', host: '0.0.0.0', port: 0 }),
    /127\.0\.0\.1/,
  );
});
