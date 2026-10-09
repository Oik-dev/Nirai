import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import test, { type TestContext } from 'node:test';
import { readIdeaMotion } from './body.ts';
import { createWorkshopRun } from './workshop-run.ts';
import { seaSettings } from './settings.ts';

function glb(extensions: object) {
  const json = Buffer.from(JSON.stringify({ asset: { version: '2.0' }, extensions }));
  const size = Math.ceil(json.length / 4) * 4;
  const bytes = Buffer.alloc(20 + size, 0x20);
  bytes.writeUInt32LE(0x46546c67, 0);
  bytes.writeUInt32LE(2, 4);
  bytes.writeUInt32LE(bytes.length, 8);
  bytes.writeUInt32LE(size, 12);
  bytes.writeUInt32LE(0x4e4f534a, 16);
  json.copy(bytes, 20);
  return bytes;
}

const motion = glb({ VRMC_vrm_animation: { specVersion: '1.0' } });
const morning = new Date('2026-10-09T20:00:00Z');

async function fixture(t: TestContext) {
  const root = await mkdtemp(join(resolve('.'), '.test-workshop-run-'));
  const idea = join(root, 'Test');
  await mkdir(join(idea, 'body'), { recursive: true });
  await writeFile(join(idea, 'body', 'avatar.vrm'), glb({ VRMC_vrm: { specVersion: '1.0' } }));
  let handsBusy = false;
  let describeStatus = 200;
  const seen: string[] = [];
  const mind = createServer(async (req, res) => {
    if (req.url === '/api/hands') {
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ busy: handsBusy, away_seconds: 3600 }));
    } else if (req.url === '/api/motion/describe') {
      let body = '';
      for await (const chunk of req) body += chunk.toString();
      seen.push(JSON.parse(body).wish);
      res.statusCode = describeStatus;
      res.end(describeStatus === 200 ? JSON.stringify({ text: 'a person raises one arm', seconds: 2 }) : '{}');
    } else { res.statusCode = 404; res.end(); }
  });
  await new Promise<void>(resolve => mind.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    mind.closeAllConnections();
    await new Promise<void>(resolve => mind.close(() => resolve()));
    await rm(root, { recursive: true, force: true });
  });
  const port = (mind.address() as { port: number }).port;
  const settings = { ...seaSettings({ NIRAI_RESIDENTS: root, NIRAI_SOURCE_REPO: resolve('..') }),
    mindPort: port };
  const context = {
    resident: { name: 'Test', idea, port }, connected: true, mindAsleep: false,
    wishes: [{ name: '片腕を上げる', ref: 'original-ref' }],
  };
  return {
    idea, settings, context, seen,
    busy(value: boolean) { handsBusy = value; },
    describeStatus(value: number) { describeStatus = value; },
    async records() {
      const folder = join(idea, 'lifelog', 'body');
      const files = (await readdir(folder).catch(() => [] as string[])).filter(v => v.endsWith('.jsonl'));
      const lines = await Promise.all(files.map(v => readFile(join(folder, v), 'utf8')));
      return lines.flatMap(v => v.split('\n').filter(Boolean).map(v => JSON.parse(v)));
    },
  };
}

test('本人の願いを精神で説明し、偽生成器と偽関門を通った動きだけ保存する', async t => {
  const f = await fixture(t);
  const calls: string[] = [];
  const run = createWorkshopRun(f.settings, {
    now: () => morning,
    openGenerator: async () => ({
      generate: async (description) => {
        assert.equal(description.text, 'a person raises one arm');
        calls.push('generate');
        return { kind: 'candidate', bytes: motion };
      },
      close: async () => { calls.push('generator-close'); },
    }),
    openGate: async avatar => {
      assert.ok(avatar.length > 0);
      return {
        check: async () => { calls.push('gate'); return true; },
        close: async () => { calls.push('gate-close'); },
      };
    },
  });
  await run(f.context, new AbortController().signal);
  assert.deepEqual(f.seen, ['片腕を上げる']);
  assert.deepEqual(calls, ['generate', 'gate', 'gate-close', 'generator-close']);
  assert.deepEqual((await f.records()).map(({ kind, value, by, ref }) => ({ kind, value, by, ref })),
    [{ kind: 'learned', value: '片腕を上げる', by: 'workshop', ref: 'original-ref' }]);
  assert.deepEqual((await readIdeaMotion(f.idea, '片腕を上げる')).bytes, motion);
});

test('関門を4回通らない願いだけfailedとし、不合格の動きは置かない', async t => {
  const f = await fixture(t);
  const seeds = new Set<number>();
  let closed = 0;
  const run = createWorkshopRun(f.settings, {
    now: () => morning,
    openGenerator: async () => ({
      generate: async (_description, seed) => {
        seeds.add(seed);
        return { kind: 'candidate', bytes: motion };
      },
      close: async () => { closed++; },
    }),
    openGate: async () => ({ check: async () => false, close: async () => { closed++; } }),
  });
  await run(f.context, new AbortController().signal);
  assert.equal(seeds.size, 4);
  assert.equal(closed, 2);
  assert.deepEqual((await f.records()).map(v => v.kind), ['failed']);
  assert.deepEqual(await readdir(join(f.idea, 'body')), ['avatar.vrm']);
});

test('Masterが操作中なら記録せず、精神503なら生成器も起こさない', async t => {
  const f = await fixture(t);
  let opens = 0;
  const options = {
    now: () => morning,
    openGenerator: async () => {
      opens++;
      throw new Error('generator should not start');
    },
    openGate: async () => { throw new Error('gate should not start'); },
  };
  f.busy(true);
  await createWorkshopRun(f.settings, options)(f.context, new AbortController().signal);
  assert.deepEqual(await f.records(), []);
  f.busy(false);
  f.describeStatus(503);
  await createWorkshopRun(f.settings, options)(f.context, new AbortController().signal);
  assert.deepEqual(await f.records(), []);
  assert.equal(opens, 0);
});

test('関門が起動できなければ生成器を必ず閉じ、願いに失敗を付けない', async t => {
  const f = await fixture(t);
  let closed = false;
  await createWorkshopRun(f.settings, {
    now: () => morning,
    openGenerator: async () => ({
      generate: async () => ({ kind: 'candidate', bytes: motion }),
      close: async () => { closed = true; },
    }),
    openGate: async () => { throw new Error('test gate start failed'); },
  })(f.context, new AbortController().signal);
  assert.equal(closed, true);
  assert.deepEqual(await f.records(), []);
});
