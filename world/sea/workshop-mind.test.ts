import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import test from 'node:test';
import { describeWorkshopWish, workshopHands } from './workshop-mind.ts';

async function fakeMind(handler: (url: string, request: unknown) => { status?: number; value: unknown }) {
  const server = createServer(async (req, res) => {
    let text = '';
    for await (const chunk of req) text += chunk;
    let value: unknown;
    try { value = text ? JSON.parse(text) : undefined; } catch { value = undefined; }
    const result = handler(req.url ?? '/', value);
    res.writeHead(result.status ?? 200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(result.value));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('fake port missing');
  return { port: address.port, close: async () => {
    await new Promise<void>(resolve => server.close(() => resolve()));
  } };
}

test('精神の英文はHTTPの本文にのみ渡し、正しい説明を一つ受け取る', async t => {
  const names: unknown[] = [];
  const mind = await fakeMind((url, request) => {
    assert.equal(url, '/api/motion/describe');
    names.push(request);
    return { value: { text: 'a person extends the right arm', seconds: 4 } };
  });
  t.after(mind.close);
  assert.deepEqual(await describeWorkshopWish(mind.port, '手をのばす', new AbortController().signal), {
    kind: 'ready', description: { text: 'a person extends the right arm', seconds: 4 },
  });
  assert.deepEqual(names, [{ wish: '手をのばす' }]);
});

test('精神から不正な説明ならfailed側、精神に届かないときは保留側へ', async t => {
  const invalid = await fakeMind(() => ({ value: { text: 'waves', seconds: 4 } }));
  t.after(invalid.close);
  assert.deepEqual(await describeWorkshopWish(invalid.port, 'private', new AbortController().signal), { kind: 'invalid' });
  const malformed = await fakeMind(() => ({ status: 502, value: {} }));
  t.after(malformed.close);
  assert.deepEqual(await describeWorkshopWish(malformed.port, 'private', new AbortController().signal), { kind: 'invalid' });
  const offline = await fakeMind(() => ({ status: 503, value: {} }));
  t.after(offline.close);
  assert.deepEqual(await describeWorkshopWish(offline.port, 'private', new AbortController().signal), { kind: 'unavailable' });
  const canceled = new AbortController();
  canceled.abort();
  assert.deepEqual(await describeWorkshopWish(offline.port, 'private', canceled.signal), { kind: 'unavailable' });
});

test('Masterの手元の忙しさと離席秒数だけを受け取る', async t => {
  const mind = await fakeMind(() => ({ value: { busy: false, away_seconds: 1801 } }));
  t.after(mind.close);
  assert.deepEqual(await workshopHands(mind.port, new AbortController().signal), { busy: false, away_seconds: 1801 });
  const unknown = await fakeMind(() => ({ value: { busy: false, away_seconds: null } }));
  t.after(unknown.close);
  assert.deepEqual(await workshopHands(unknown.port, new AbortController().signal), { busy: false, away_seconds: null });
  const invalid = await fakeMind(() => ({ value: { busy: 'no', away_seconds: -1 } }));
  t.after(invalid.close);
  assert.equal(await workshopHands(invalid.port, new AbortController().signal), null);
});
