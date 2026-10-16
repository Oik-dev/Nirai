import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { startSeaServer } from './server.ts';
import { seaSettings } from './settings.ts';
import { pastimePeriod } from './life.ts';

function avatar() {
  const source = Buffer.from(JSON.stringify({ asset: { version: '2.0' },
    extensions: { VRMC_vrm: { specVersion: '1.0' } } }));
  const padded = Math.ceil(source.length / 4) * 4;
  const bytes = Buffer.alloc(padded + 20, 0x20);
  bytes.writeUInt32LE(0x46546c67, 0); bytes.writeUInt32LE(2, 4);
  bytes.writeUInt32LE(bytes.length, 8); bytes.writeUInt32LE(padded, 12);
  bytes.writeUInt32LE(0x4e4f534a, 16); source.copy(bytes, 20);
  return bytes;
}

test('使い捨て4住人と偽の郵便局で、実際の海が好み・稼働・眠りを返す（本番ポートとイデアは使わない）', async t => {
  const root = await mkdtemp(join(tmpdir(), 'nirai-sea-free-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const names = ['Serina', 'Holo', 'Claude', 'Codex'];
  for (const name of names) {
    const directory = join(root, name, 'body');
    await mkdir(directory, { recursive: true });
    await writeFile(join(directory, 'avatar.vrm'), avatar());
  }
  for (const [name, pastimes] of [
    ['Holo', ['海の中を泳ぐ']], ['Claude', ['砂地で休む']],
  ] as const) {
    const folder = join(root, name, 'lifelog', 'body');
    await mkdir(folder, { recursive: true });
    await writeFile(join(folder, '2026-10-16.jsonl'), JSON.stringify({
      ts: '2026-10-16T02:00:00Z', kind: 'pastimes', value: pastimes, by: 'post',
    }) + '\n');
  }
  const states: Record<string, { awake: boolean; limitUntil?: string }> = Object.fromEntries(names.map(name => [name, { awake: false }]));
  const post = createServer((_req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ residents: names.map(name => ({ name, ...states[name], unread: 0, stuck: 0, unreachable: 0 })) }));
  });
  const mind = createServer((_req, res) => { res.statusCode = 503; res.end(); });
  await new Promise<void>(done => post.listen(0, '127.0.0.1', done));
  await new Promise<void>(done => mind.listen(0, '127.0.0.1', done));
  t.after(() => new Promise(done => post.close(done)));
  t.after(() => new Promise(done => mind.close(done)));
  const postAddress = post.address(), mindAddress = mind.address();
  assert.ok(postAddress && typeof postAddress !== 'string' && mindAddress && typeof mindAddress !== 'string');
  const wasPort = process.env.NIRAI_PORT;
  process.env.NIRAI_PORT = String(postAddress.port);
  t.after(() => {
    if (wasPort === undefined) delete process.env.NIRAI_PORT;
    else process.env.NIRAI_PORT = wasPort;
  });
  const settings = { ...seaSettings({ NIRAI_RESIDENTS: root, NIRAI_SOURCE_REPO: resolve('..') }), mindPort: mindAddress.port };
  const sea = await startSeaServer({ settings, port: 0 });
  t.after(() => new Promise(done => sea.close(done)));
  const seaAddress = sea.address();
  assert.ok(seaAddress && typeof seaAddress !== 'string');
  const base = `http://127.0.0.1:${seaAddress.port}`;
  const read = async () => (await (await fetch(`${base}/sea/body`)).json()).lives;
  const initial = await read();
  assert.deepEqual(Object.keys(initial).sort(), [...names].sort());
  assert.equal(initial.Holo.activity.name, '海の中を泳ぐ');
  assert.ok(initial.Holo.activity.at);
  assert.ok(initial.Holo.activity.route.changes.length > 3);
  assert.equal(initial.Claude.activity.name, '砂地で休む');
  assert.equal(initial.Serina.activity.at, undefined);
  assert.equal(initial.Serina.asleep, true);
  assert.equal(initial.Holo.asleep, false);
  assert.ok(Math.abs(Date.now() - pastimePeriod('Holo', Date.now()).since) < 17 * 60_000);

  states.Holo = { awake: true };
  await new Promise(resolve => setTimeout(resolve, 3200));
  const pondering = await read();
  assert.ok(pondering.Holo.activity.route.changes.length > 3,
    '考え始めても位置が跳ばないよう、泳ぎの時刻と行き先は維持する');
  assert.equal(pondering.Holo.asleep, false);

  states.Holo = { awake: false, limitUntil: new Date(Date.now() + 10 * 60_000).toISOString() };
  await new Promise(resolve => setTimeout(resolve, 3200));
  const resting = await read();
  assert.equal(resting.Holo.asleep, true);
  assert.equal(resting.Holo.activity.name, '砂地で休む');
  assert.ok(resting.Holo.activity.at.z <= -.55);
  assert.deepEqual((await read()).Holo.activity.at, resting.Holo.activity.at);
});
