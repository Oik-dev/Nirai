// 使い捨てのイデアと偽の精神・生成器・関門だけで、
// 本人の願いから覚えた動きがカタログに現れるまでを通す。
import assert from 'node:assert/strict';
import { createServer, type ServerResponse } from 'node:http';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { readBodyCatalog, readIdeaMotion } from './body.ts';
import { SeaEvents } from './events.ts';
import { createWorkshopRun } from './workshop-run.ts';
import { WorkshopSchedule } from './workshop-schedule.ts';
import { workshopHands } from './workshop-mind.ts';
import { WorkshopDuty } from './workshop.ts';
import { seaSettings } from './settings.ts';

function glb(extensions: object) {
  const json = Buffer.from(JSON.stringify({ asset: { version: '2.0' }, extensions }));
  const jsonSize = Math.ceil(json.length / 4) * 4;
  const result = Buffer.alloc(20 + jsonSize, 0x20);
  result.writeUInt32LE(0x46546c67, 0);
  result.writeUInt32LE(2, 4);
  result.writeUInt32LE(result.length, 8);
  result.writeUInt32LE(jsonSize, 12);
  result.writeUInt32LE(0x4e4f534a, 16);
  json.copy(result, 20);
  return result;
}

async function until(check: () => Promise<boolean> | boolean, label: string) {
  const deadline = Date.now() + 4_000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.fail(label);
}

test('海が願いを聞き、朝の工房で合格した仕草を学び、カタログへ載せる', {
  timeout: 10_000,
}, async t => {
  const root = await mkdtemp(join(resolve('.'), '.test-workshop-whole-'));
  const idea = join(root, 'Test');
  const avatar = glb({ VRMC_vrm: { specVersion: '1.0' } });
  const animation = glb({ VRMC_vrm_animation: { specVersion: '1.0' } });
  await mkdir(join(idea, 'body'), { recursive: true });
  await writeFile(join(idea, 'body', 'avatar.vrm'), avatar);
  const streams = new Set<ServerResponse>();
  let described = 0;
  const mind = createServer(async (req, res) => {
    if (req.url === '/api/events') {
      res.setHeader('content-type', 'text/event-stream');
      res.write(': connected\n\n');
      streams.add(res);
      res.once('close', () => streams.delete(res));
    } else if (req.url === '/api/perceive') {
      res.end('{}');
    } else if (req.url === '/api/hands') {
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ busy: false, away_seconds: 3600 }));
    } else if (req.url === '/api/motion/describe') {
      let requestBody = '';
      for await (const chunk of req) requestBody += chunk.toString();
      assert.equal(JSON.parse(requestBody).wish, '手を振る');
      described++;
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ text: 'a person waves a hand', seconds: 2 }));
    } else {
      res.statusCode = 404;
      res.end();
    }
  });
  await new Promise<void>(resolve => mind.listen(0, '127.0.0.1', resolve));
  const port = (mind.address() as { port: number }).port;
  const settings = {
    ...seaSettings({ NIRAI_RESIDENTS: root, NIRAI_SOURCE_REPO: resolve('..') }),
    mindPort: port,
  };
  const events = new SeaEvents(settings);
  t.after(async () => {
    events.stop();
    mind.closeAllConnections();
    await new Promise<void>(resolve => mind.close(() => resolve()));
    await rm(root, { recursive: true, force: true });
  });
  events.start();
  await until(async () => streams.size === 1 && (await events.workshopContext()).connected,
    '精神のイベント流れが接続する');
  for (const stream of streams) stream.write('data: ' + JSON.stringify({
    type: 'body', by: 'reply', ref: 'r1', gesture: 'ほかの動き', wish: '手を振る',
  }) + '\n\n');
  await until(async () => (await events.workshopContext()).wishes.length === 1,
    '身振りの願いが記録される');

  const fakeCalls: string[] = [];
  const run = createWorkshopRun(settings, {
    now: () => new Date('2026-10-09T20:00:00Z'),
    openGenerator: async () => ({
      generate: async () => { fakeCalls.push('generate'); return { kind: 'candidate', bytes: animation }; },
      close: async () => { fakeCalls.push('generator-closed'); },
    }),
    openGate: async bytes => {
      assert.deepEqual(bytes, avatar);
      return {
        check: async bytes => { assert.deepEqual(bytes, animation); fakeCalls.push('gate'); return true; },
        close: async () => { fakeCalls.push('gate-closed'); },
      };
    },
  });
  const duty = new WorkshopDuty();
  const schedule = new WorkshopSchedule({
    duty, settings: settings.workshop, context: () => events.workshopContext(),
    hands: (mindPort, signal) => workshopHands(mindPort, signal),
    perform: run, now: () => new Date('2026-10-09T20:00:00Z'),
  });
  t.after(() => schedule.stop());
  await schedule.tick();
  await until(() => !duty.running, '工房が動きの保存と記録を終える');
  assert.ok(await readIdeaMotion(idea, '手を振る'), '合格したVRMAが保存される');
  await duty.stop();
  await schedule.tick();
  assert.equal(described, 1, '同じ朝には作り直さない');
  assert.deepEqual(fakeCalls, ['generate', 'gate', 'gate-closed', 'generator-closed']);
  const folder = join(idea, 'lifelog', 'body');
  const files = (await readdir(folder)).filter(name => name.endsWith('.jsonl'));
  const records = (await Promise.all(files.map(name => readFile(join(folder, name), 'utf8'))))
    .flatMap(data => data.split('\n').filter(Boolean).map(line => JSON.parse(line)));
  assert.deepEqual(records.map(({ kind, value }) => ({ kind, value })), [
    { kind: 'wish', value: '手を振る' }, { kind: 'learned', value: '手を振る' },
  ]);
  assert.ok((await readBodyCatalog(idea)).gestures.includes('手を振る'));
  await schedule.stop();
});
