import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { createServer, type ServerResponse } from 'node:http';
import { join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import test, { type TestContext } from 'node:test';
import { SEA_HOST, startSeaServer } from './server.ts';
import type { Life } from './life.ts';
import { seaSettings } from './settings.ts';

type Catalog = { expressions: string[]; gestures: string[] };
type Record = { ts: string; kind: string; value: string; by: string; ref: string };
type Event = { type: string; [key: string]: unknown };

function glb(extensions: object) {
  const source = Buffer.from(JSON.stringify({ asset: { version: '2.0' }, extensions }));
  const length = Math.ceil(source.length / 4) * 4;
  const bytes = Buffer.alloc(20 + length, 0x20);
  bytes.writeUInt32LE(0x46546c67, 0);
  bytes.writeUInt32LE(2, 4);
  bytes.writeUInt32LE(bytes.length, 8);
  bytes.writeUInt32LE(length, 12);
  bytes.writeUInt32LE(0x4e4f534a, 16);
  source.copy(bytes, 20);
  return bytes;
}

function avatar(name = '初めの表情') {
  return glb({ VRMC_vrm: { expressions: { custom: {
    [name]: { morphTargetBinds: [{ node: 0, index: 0, weight: 1 }] },
  } } } });
}
const motion = () => glb({ VRMC_vrm_animation: { specVersion: '1.0' } });

async function until(check: () => unknown | Promise<unknown>, message: string) {
  const deadline = Date.now() + 4_000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await delay(10);
  }
  assert.fail(message);
}

async function records(idea: string): Promise<Record[]> {
  const directory = join(idea, 'lifelog', 'body');
  const files = (await readdir(directory).catch(() => [] as string[])).filter(name => name.endsWith('.jsonl')).sort();
  const lines = await Promise.all(files.map(name => readFile(join(directory, name), 'utf8')));
  return lines.flatMap(text => text.split('\n').filter(Boolean).map(line => JSON.parse(line)));
}

async function windowStream(base: string) {
  const controller = new AbortController();
  const response = await fetch(`${base}/api/events`, { signal: controller.signal });
  assert.equal(response.status, 200);
  const reader = response.body!.getReader();
  const events: Event[] = [];
  const done = (async () => {
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      for (;;) {
        const item = await reader.read();
        if (item.done) break;
        buffer += decoder.decode(item.value, { stream: true });
        let end;
        while ((end = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, end);
          buffer = buffer.slice(end + 2);
          const data = block.split('\n').find(line => line.startsWith('data: '));
          if (data) events.push(JSON.parse(data.slice(6)));
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) throw error;
    } finally { reader.releaseLock(); }
  })();
  void done.catch(() => undefined);
  return { events, done, async close() { controller.abort(); await done; } };
}

async function fixture(t: TestContext, { failFirstPerceive = false } = {}) {
  const root = await mkdtemp(join(resolve('.'), '.test-sea-events-'));
  const idea = join(root, 'Resident');
  await mkdir(join(idea, 'body', 'motions'), { recursive: true });
  await writeFile(join(idea, 'body', 'avatar.vrm'), avatar());
  const streams = new Set<ServerResponse>();
  const perceived: Array<{ kind: string; catalog: Catalog }> = [];
  let connections = 0;
  let refusing = false;
  const mind = createServer(async (req, res) => {
    if (req.url === '/api/events' && refusing) {
      res.statusCode = 503;
      res.end();
    } else if (req.url === '/api/events') {
      connections++;
      streams.add(res);
      res.setHeader('content-type', 'text/event-stream');
      res.write(': connected\n\n');
      res.once('close', () => streams.delete(res));
    } else if (req.url === '/api/perceive') {
      let body = '';
      for await (const chunk of req) body += chunk.toString();
      perceived.push(JSON.parse(body));
      if (failFirstPerceive && perceived.length === 1) res.statusCode = 503;
      res.end('{}');
    } else {
      res.end('{}');
    }
  });
  await new Promise<void>(done => mind.listen(0, SEA_HOST, done));
  const address = mind.address();
  assert.ok(address && typeof address === 'object');
  const settings = { ...seaSettings({ NIRAI_RESIDENTS: root, NIRAI_SOURCE_REPO: resolve('..') }), mindPort: address.port };
  let sea: Awaited<ReturnType<typeof startSeaServer>>;
  let base = '';
  const windows: Awaited<ReturnType<typeof windowStream>>[] = [];
  t.after(async () => {
    for (const window of windows) await window.close();
    await sea?.drain(1_000);
    mind.closeAllConnections();
    await new Promise<void>((done, reject) => mind.close(error => error ? reject(error) : done()));
    await rm(root, { recursive: true, force: true });
  });
  async function start() {
    sea = await startSeaServer({ settings, port: 0 });
    const address = sea.address();
    assert.ok(address && typeof address === 'object');
    base = `http://${SEA_HOST}:${address.port}`;
    await until(() => streams.size === 1 && perceived.length > 0, '海が窓なしで精神につなぐ');
  }
  await start();
  return {
    idea, mind, perceived, streams,
    get sea() { return sea; }, get base() { return base; }, get connections() { return connections; },
    async restart() { await sea.drain(1_000); await until(() => streams.size === 0, '前の接続を閉じる'); await start(); },
    async window() { const window = await windowStream(base); windows.push(window); return window; },
    // 精神が止まった替わり：流れを切り、つなぎ直しを断る。
    refuse() { refusing = true; for (const stream of streams) stream.destroy(); },
    async snapshot(): Promise<{ catalog: Catalog; life: Life; revision: string }> {
      const response = await fetch(`${base}/sea/body`);
      assert.equal(response.status, 200);
      return response.json();
    },
    raw(text: string | Buffer) { for (const stream of streams) stream.write(text); },
    emit(event: object) { for (const stream of streams) stream.write(`data: ${JSON.stringify(event)}\n\n`); },
    async barrier(window: Awaited<ReturnType<typeof windowStream>>, ref: string) {
      for (const stream of streams) stream.write(`data: ${JSON.stringify({ type: 'said', ref })}\n\n`);
      await until(() => window.events.some(event => event.type === 'said' && event.ref === ref), '無効な体の知らせの後も流れを続ける');
    },
  };
}

test('窓がなくても体を記録し、海の再起動後は記録から同じ暮らしを戻す', { timeout: 10_000 }, async t => {
  const f = await fixture(t);
  const first = await f.snapshot();
  assert.equal(first.life.expression, null);
  assert.ok(first.catalog.expressions.includes('初めの表情'));
  assert.deepEqual(f.perceived[0], { kind: 'body', catalog: first.catalog });
  assert.equal(f.connections, 1);
  assert.equal((await fetch(`${f.base}/api/pulse/mute`, { method: 'POST' })).status, 404);
  assert.equal((await fetch(`${f.base}/api/perceive`, { method: 'POST' })).status, 404);
  f.emit({ type: 'body', by: 'reply', ref: 'reply-1', expression: '初めの表情', gesture: first.catalog.gestures[0] });
  await until(async () => (await records(f.idea)).length === 2, '窓0でも選択を記録する');
  const saved = await records(f.idea);
  assert.deepEqual(saved.map(({ kind, value, by, ref }) => ({ kind, value, by, ref })), [
    { kind: 'expression', value: '初めの表情', by: 'reply', ref: 'reply-1' },
    { kind: 'gesture', value: first.catalog.gestures[0], by: 'reply', ref: 'reply-1' },
  ]);
  for (const record of saved) {
    assert.deepEqual(Object.keys(record).sort(), ['by', 'kind', 'ref', 'ts', 'value']);
    assert.ok(Number.isFinite(Date.parse(record.ts)));
  }
  const before = await f.snapshot();
  await f.restart();
  const restored = await f.snapshot();
  assert.deepEqual(restored.life, before.life);
  assert.equal(restored.life.expression, '初めの表情');
  assert.equal(restored.revision, first.revision, '体の記録でカタログの版は変わらない');
  f.emit({ type: 'body', by: 'pulse', ref: 'pulse-1', expression: 'なし' });
  await until(async () => (await records(f.idea)).length === 3, '本人の表情を戻す選択も記録する');
  assert.equal((await f.snapshot()).life.expression, null);
});

test('窓2枚も精神への接続は1本で、断片SSEを読み、無効な選択を落とす', { timeout: 10_000 }, async t => {
  const f = await fixture(t);
  const a = await f.window();
  const b = await f.window();
  assert.equal(f.connections, 1);
  const gesture = (await f.snapshot()).catalog.gestures[0];
  const frame = Buffer.from(`event: body\r\ndata: {"type":"body",\r\ndata: "by":"pulse","ref":"pulse-2","expression":"初めの表情","gesture":"${gesture}"}\r\n\r\n`);
  // UTF-8の日本語の途中でも分割し、CRLFと複数data行を通す。
  const split = frame.indexOf(Buffer.from('初め')) + 1;
  f.raw(frame.subarray(0, 7)); await delay(15);
  f.raw(frame.subarray(7, split)); await delay(15);
  f.raw(frame.subarray(split));
  await until(() => a.events.some(event => event.type === 'life') && b.events.some(event => event.type === 'life'), '2枚へ暮らしが変わったと知らせる');
  assert.equal((await records(f.idea)).length, 2);
  assert.deepEqual((await f.snapshot()).life.gesture?.name, gesture);
  f.raw('data: broken JSON\n\ndata: null\n\n');
  f.emit({ type: 'body', by: 'reply', ref: 'unknown', expression: '知らない表情', gesture: '知らない身振り' });
  f.emit({ type: 'body', by: 'invalid', ref: 'invalid-by', expression: '初めの表情' });
  await f.barrier(b, 'after-invalid');
  assert.equal((await records(f.idea)).length, 2);
  assert.equal(b.events.filter(event => event.type === 'life').length, 1);
  await a.close();
  f.emit({ type: 'body', by: 'reply', ref: 'reply-2', expression: '初めの表情', gesture: '知らない身振り' });
  await until(() => b.events.filter(event => event.type === 'life').length === 2, '片方を閉じても他方へ届く');
  assert.equal((await records(f.idea)).length, 3);
  await b.close();
  f.emit({ type: 'body', by: 'pulse', ref: 'after-windows', expression: 'なし' });
  await until(async () => (await records(f.idea)).length === 4, '窓を全部閉じても記録を続ける');
  assert.equal(f.connections, 1);
  assert.equal(f.streams.size, 1);
});

test('VRMと覚えた動きが替わったら新しいカタログを知覚へ送り、古い名前を受け取らない', { timeout: 10_000 }, async t => {
  const f = await fixture(t);
  const motions = join(f.idea, 'body', 'motions');
  await writeFile(join(motions, '昔の動き.vrma'), motion());
  const before = await f.snapshot();
  assert.ok(before.catalog.gestures.includes('昔の動き'));
  const window = await f.window();
  await writeFile(join(f.idea, 'body', 'avatar.vrm'), avatar('新しい表情'));
  await rm(join(motions, '昔の動き.vrma'));
  await writeFile(join(motions, '新しい動き.vrma'), motion());
  f.emit({ type: 'body', by: 'reply', ref: 'old-body', expression: '初めの表情', gesture: '昔の動き' });
  await f.barrier(window, 'after-swap');
  assert.equal((await records(f.idea)).length, 0);
  const replaced = await f.snapshot();
  assert.notEqual(replaced.revision, before.revision);
  assert.ok(replaced.catalog.expressions.includes('新しい表情'));
  assert.ok(!replaced.catalog.expressions.includes('初めの表情'));
  assert.ok(replaced.catalog.gestures.includes('新しい動き'));
  assert.ok(!replaced.catalog.gestures.includes('昔の動き'));
  assert.deepEqual(f.perceived.at(-1), { kind: 'body', catalog: replaced.catalog });
  assert.ok(window.events.some(event => event.type === 'catalog'));
  f.emit({ type: 'body', by: 'pulse', ref: 'new-body', expression: '新しい表情', gesture: '新しい動き' });
  await until(async () => (await records(f.idea)).length === 2, '新しい体だけを記録する');
  await writeFile(join(motions, '新しい動き.vrma'), Buffer.from('broken motion'));
  const changed = await f.snapshot();
  assert.notEqual(changed.revision, replaced.revision);
  assert.ok(!changed.catalog.gestures.includes('新しい動き'));
  assert.deepEqual(f.perceived.at(-1), { kind: 'body', catalog: changed.catalog });
  f.emit({ type: 'body', by: 'reply', ref: 'broken-motion', expression: '初めの表情', gesture: '新しい動き' });
  await f.barrier(window, 'after-motion-change');
  assert.equal((await records(f.idea)).length, 2);
});

test('記録へ追記できなければ体を窓へ知らせず、直った後は続きを記録する', { timeout: 10_000 }, async t => {
  const f = await fixture(t);
  const window = await f.window();
  await mkdir(join(f.idea, 'lifelog'));
  const blocked = join(f.idea, 'lifelog', 'body');
  await writeFile(blocked, '記録先を塞ぐ替え玉');
  f.emit({ type: 'body', by: 'reply', ref: 'write-failure', expression: '初めの表情' });
  await f.barrier(window, 'after-write-failure');
  assert.ok(!window.events.some(event => event.type === 'life'));
  assert.equal(await readFile(blocked, 'utf8'), '記録先を塞ぐ替え玉');
  await rm(blocked);
  f.emit({ type: 'body', by: 'reply', ref: 'write-recovered', expression: '初めの表情' });
  await until(() => window.events.some(event => event.type === 'life'), '追記失敗後も次の選択を受ける');
  assert.equal((await records(f.idea))[0].ref, 'write-recovered');
});

test('精神の流れが切れたら知覚を送り直し、流れだけなら海の手すきを妨げない', { timeout: 10_000 }, async t => {
  const f = await fixture(t);
  const oldWindow = await f.window();
  const firstPerceived = f.perceived.length;
  for (const stream of f.streams) stream.destroy();
  await oldWindow.done;
  await until(() => f.connections === 2 && f.perceived.length > firstPerceived, '再接続時に体の知覚を送り直す');
  assert.equal(f.streams.size, 1);
  assert.deepEqual(f.perceived.at(-1), f.perceived[0]);
  const window = await f.window();
  f.emit({ type: 'body', by: 'pulse', ref: 'reconnected', expression: '初めの表情' });
  await until(() => window.events.some(event => event.type === 'life'), '再接続後も記録した選択を渡す');
  const status = await (await fetch(`${f.base}/sea/status`)).json();
  assert.equal(status.relaying, 0);
  const started = Date.now();
  await f.sea.drain(3_000);
  assert.ok(Date.now() - started < 1_000, '窓と精神のSSEだけなら返事の完了待ちは不要');
  await until(() => f.streams.size === 0, '入れ替え時は精神への流れも閉じる');
  assert.equal(f.mind.listening, true, '海を閉じても精神の替え玉は動いたまま');
});

test('知覚だけが一度503でも接続を張り直して再送し、その後の選択を記録して窓へ渡す', { timeout: 10_000 }, async t => {
  // 替え玉のSSEは開いたまま。知覚の失敗を受けて海自身が接続を張り直す。
  const f = await fixture(t, { failFirstPerceive: true });
  await until(() => f.connections === 2 && f.perceived.length === 2 && f.streams.size === 1, '知覚失敗後は接続ごと張り直して再送する');
  assert.deepEqual(f.perceived[1], f.perceived[0]);
  const window = await f.window();
  f.emit({ type: 'body', by: 'reply', ref: 'after-perceive-retry', expression: '初めの表情' });
  await until(() => window.events.some(event => event.type === 'life'), '知覚の再送後も本人の選択を窓へ渡す');
  const saved = await records(f.idea);
  assert.equal(saved.length, 1);
  assert.equal(saved[0].ref, 'after-perceive-retry');
  assert.equal((await f.snapshot()).life.expression, '初めの表情');
  assert.equal(f.connections, 2);
});

test('精神の眠りと目覚め、精神が止まったことを、暮らしの眠りとして窓へ知らせる', { timeout: 10_000 }, async t => {
  const f = await fixture(t);
  const window = await f.window();
  const lives = () => window.events.filter(event => event.type === 'life').length;
  assert.equal((await f.snapshot()).life.asleep, false);
  f.emit({ type: 'state', state: 'asleep' });
  await until(() => lives() === 1, '眠ったと知らせる');
  assert.equal((await f.snapshot()).life.asleep, true);
  f.emit({ type: 'state', state: 'asleep' });
  f.emit({ type: 'state', state: 'dreaming' });
  f.emit({ type: 'state', state: 'awake' });
  await until(() => lives() === 2, '起きたと知らせる');
  await f.barrier(window, 'after-state');
  assert.equal(lives(), 2, '同じ値と知らない値では知らせない');
  assert.ok(!window.events.some(event => event.type === 'state'), '精神の様子は暮らしとしてだけ渡す');
  assert.equal((await f.snapshot()).life.asleep, false);
  f.refuse();
  await window.done;
  assert.equal((await f.snapshot()).life.asleep, true, '精神が動いていなければ眠っている');
});
