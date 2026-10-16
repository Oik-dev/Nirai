import assert from 'node:assert/strict';
import test from 'node:test';
import { PostStatusCache } from './post-status.ts';

test('海は郵便局の状態だけを受け取り、本文を持たない', async t => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  let path = '';
  globalThis.fetch = async (input) => {
    path = String(input);
    return { ok: true, json: async () => ({
      residents: [{
        name: 'Holo', state: 'stuck', awake: true, unread: 3,
        stuck: 1, unreachable: 1, limitUntil: '2026-10-16T12:00:00Z', limitKnown: false,
        body: '本文は決して読まない',
      }],
    }) } as Response;
  };
  const cache = new PostStatusCache();
  assert.equal(cache.get('Holo'), undefined);
  assert.equal(await cache.refresh(56789), true);
  assert.equal(path, 'http://127.0.0.1:56789/holo/residents');
  assert.deepEqual(cache.get('Holo'), {
    awake: true, unread: 3, stuck: true,
    limitUntil: '2026-10-16T12:00:00Z', limitKnown: false,
  });
  assert.equal(cache.get('Codex'), undefined);
  assert.equal(await cache.refresh(56789), false, '同じ状態では余計な描画更新を通知しない');
});

test('郵便局に届かなくなっても最後の正常な状態を保持する', async t => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  globalThis.fetch = async () => ({ ok: true, json: async () => ({
    residents: [{ name: 'Codex', awake: false, unread: 0, stuck: 0, unreachable: 0 }],
  }) }) as Response;
  const cache = new PostStatusCache();
  assert.equal(await cache.refresh(56789), true);
  globalThis.fetch = async () => { throw new Error('unreachable'); };
  assert.equal(await cache.refresh(56789), false);
  assert.deepEqual(cache.get('Codex'), { awake: false, unread: 0, stuck: false });
});
