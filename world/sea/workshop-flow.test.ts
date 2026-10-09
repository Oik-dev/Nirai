import assert from 'node:assert/strict';
import test from 'node:test';
import { runWorkshopWishes, type WorkshopFlow, type WorkshopSession } from './workshop-flow.ts';

const wish = { name: 'private Japanese wish', ref: 't1' };

function fakeFlow() {
  const records: string[] = [];
  const saved: string[] = [];
  const seeds: number[] = [];
  let closed = 0;
  let opened = 0;
  const session: WorkshopSession = {
    async generate(_description, seed) { seeds.push(seed); return { kind: 'candidate', bytes: Buffer.from('fake') }; },
    async gate() { return true; },
    async close() { closed++; },
  };
  const flow: WorkshopFlow = {
    async describe() { return { kind: 'ready', description: { text: 'a person moves an arm', seconds: 3 } }; },
    async open() { opened++; return session; },
    async continueWork() { return true; },
    async install(w) { saved.push(w.ref); },
    async record(w, kind) { records.push(`${w.ref}:${kind}`); },
  };
  return { flow, session, records, saved, seeds, get opened() { return opened; }, get closed() { return closed; } };
}

test('願いは説明してから生成器を一度だけ起こし、通ったものだけ保存してlearned', async () => {
  const f = fakeFlow();
  const order: string[] = [];
  f.flow.describe = async w => { order.push(`describe:${w.ref}`); return { kind: 'ready', description: { text: 'a person waves', seconds: 2 } }; };
  f.flow.open = async () => { order.push('open'); return f.session; };
  const counts = await runWorkshopWishes([wish, { name: 'secret 2', ref: 't2' }], 4, f.flow, new AbortController().signal);
  assert.deepEqual(order, ['describe:t1', 'describe:t2', 'open']);
  assert.deepEqual(counts, { learned: 2, failed: 0 });
  assert.deepEqual(f.records, ['t1:learned', 't2:learned']);
  assert.deepEqual(f.saved, ['t1', 't2']);
  assert.equal(f.closed, 1);
});

test('関門を通らない4回は異なるseedを使い、願いだけfailedにする', async () => {
  const f = fakeFlow();
  f.session.gate = async () => false;
  assert.deepEqual(await runWorkshopWishes([wish], 4, f.flow, new AbortController().signal), { learned: 0, failed: 1 });
  assert.equal(new Set(f.seeds).size, 4);
  assert.deepEqual(f.saved, []);
  assert.deepEqual(f.records, ['t1:failed']);
  assert.equal(f.closed, 1);
});

test('説明502はfailed、503と生成器起動失敗は本人の失敗に数えない', async () => {
  const f = fakeFlow();
  f.flow.describe = async w => w.ref === 't1' ? { kind: 'invalid' } : { kind: 'unavailable' };
  assert.deepEqual(await runWorkshopWishes([wish, { name: 'other', ref: 't2' }], 4, f.flow, new AbortController().signal), { learned: 0, failed: 1 });
  assert.deepEqual(f.records, ['t1:failed']);
  assert.equal(f.opened, 0);
  const g = fakeFlow();
  g.flow.open = async () => { throw new Error('machine unavailable'); };
  assert.deepEqual(await runWorkshopWishes([wish], 4, g.flow, new AbortController().signal), { learned: 0, failed: 0 });
  assert.deepEqual(g.records, []);
});

test('Masterが話す・忙しくなると処理を止め、失敗を記録せず子を閉じる', async () => {
  const f = fakeFlow();
  const controller = new AbortController();
  f.session.generate = async () => { controller.abort(); return { kind: 'rejected' }; };
  assert.deepEqual(await runWorkshopWishes([wish], 4, f.flow, controller.signal), { learned: 0, failed: 0 });
  assert.deepEqual(f.records, []);
  assert.equal(f.closed, 1);
  const g = fakeFlow();
  let calls = 0;
  g.flow.continueWork = async () => ++calls < 2;
  assert.deepEqual(await runWorkshopWishes([wish], 4, g.flow, new AbortController().signal), { learned: 0, failed: 0 });
  assert.deepEqual(g.records, []);
  assert.equal(g.closed, 1);
});

test('関門の判定不能と生成器の途中死はfailedにしない', async () => {
  const f = fakeFlow();
  f.session.gate = async () => { throw new Error('chrome died'); };
  assert.deepEqual(await runWorkshopWishes([wish], 4, f.flow, new AbortController().signal), { learned: 0, failed: 0 });
  assert.deepEqual(f.records, []);
  assert.equal(f.closed, 1);
  const g = fakeFlow();
  g.session.generate = async () => ({ kind: 'unavailable' });
  assert.deepEqual(await runWorkshopWishes([wish], 4, g.flow, new AbortController().signal), { learned: 0, failed: 0 });
  assert.deepEqual(g.records, []);
  assert.equal(g.closed, 1);
});
