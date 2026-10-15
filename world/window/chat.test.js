import assert from 'node:assert/strict';
import test from 'node:test';
import { ChatWindow } from './chat.js';

function fixture(t, options = {}) {
  const previousDocument = globalThis.document;
  globalThis.document = { getElementById: () => ({ textContent: '' }) };
  t.after(() => { if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument; });
  const sources = [];
  class Events {
    static CLOSED = 2;
    readyState = 1;
    constructor(url) { this.url = url; sources.push(this); }
    close() { this.readyState = Events.CLOSED; }
    receive(payload) { this.onmessage({ data: JSON.stringify(payload) }); }
  }
  const previous = globalThis.EventSource;
  globalThis.EventSource = Events;
  t.after(() => { if (previous === undefined) delete globalThis.EventSource; else globalThis.EventSource = previous; });
  const chat = new ChatWindow(options);
  chat.mind = 'up';
  chat.reconcileLatest = async () => {};
  chat.loadLatest = async () => {};
  t.after(() => chat.dispose());
  return { chat, sources, Events };
}

test('会話と同じ流れで、接続・暮らし・カタログの知らせを窓へ渡す', async t => {
  const calls = [];
  const { chat, sources } = fixture(t, {
    onLife: () => calls.push('life'),
    onVoice: (...voice) => calls.push(voice),
  });
  let reconciled = 0;
  chat.reconcileLatest = async () => { reconciled++; };
  chat.connectEvents();
  const events = sources[0];
  assert.equal(events.url, '/api/events');
  events.onopen();
  events.receive({ type: 'life' });
  events.receive({ type: 'catalog' });
  events.receive({ type: 'said', text: 'こんにちは' });
  assert.equal(reconciled, 1);
  assert.deepEqual(calls, ['life', 'life', 'life', ['Serina', 'こんにちは']]);
  chat.sending = true;
  events.receive({ type: 'said', text: '流れから受け取る返事' });
  events.receive({ type: 'life' });
  assert.equal(chat.refreshPending, true);
  assert.equal(calls.at(-1), 'life', '返事の最中も体の選びを渡す');
  assert.equal(calls.filter(call => Array.isArray(call) && call[0] === 'Serina').length, 1, '返事の声を二重に渡さない');
});

test('流れの終了後は接続し直し、古い接続からの知らせは受け付けない', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let lives = 0;
  const { chat, sources, Events } = fixture(t, { onLife: () => { lives++; } });
  chat.connectEvents();
  const first = sources[0];
  first.onopen();
  first.readyState = Events.CLOSED;
  first.onerror();
  t.mock.timers.tick(500);
  assert.equal(sources.length, 2);
  sources[1].onopen();
  first.onopen();
  first.receive({ type: 'life' });
  sources[1].receive({ type: 'life' });
  assert.equal(lives, 3, '開いた2回と、今の流れの知らせ1回');
});

test('Serinaの精神が停止中でも2秒ごとの見回りでほかの住人の更新を読む', async t => {
  let changes = 0;
  const { chat } = fixture(t, { onLife: () => { changes++; } });
  const previous = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ resident: 'Serina', mind: 'down' }) });
  t.after(() => { globalThis.fetch = previous; });
  chat.syncMindControls = () => {};
  chat.wakeMind = async () => {};
  await chat.pollMind();
  assert.equal(changes, 1);
  await chat.pollMind();
  assert.equal(changes, 2, '精神がすでにdownでも体の更新確認を継続');
});

test('雲の途中返事をresetで消し、手元の書き直しだけを残す', async t => {
  const { chat } = fixture(t);
  const priorFetch = globalThis.fetch;
  const priorStorage = globalThis.localStorage;
  t.after(() => {
    globalThis.fetch = priorFetch;
    if (priorStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = priorStorage;
  });
  globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
  chat.input = { value: 'こんにちは', focus: () => {} };
  chat.messages = { scrollHeight: 0, scrollTop: 0, clientHeight: 0 };
  chat.syncMindControls = () => {};
  chat.render = () => {};
  chat.growInput = () => {};
  const seen = [];
  chat.updatePendingReply = reply => { seen.push(reply.text); };
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const event of [
        { type: 'token', text: 'Geminiの途中' },
        { type: 'reset' },
        { type: 'token', text: 'Gemmaの書き直し' },
        { type: 'done', reply: 'Gemmaの書き直し' },
      ]) controller.enqueue(encoder.encode(JSON.stringify(event) + '\n'));
      controller.close();
    },
  });
  globalThis.fetch = async () => ({ ok: true, body: stream });
  await chat.submit();
  assert.deepEqual(seen, ['Geminiの途中', '', 'Gemmaの書き直し', 'Gemmaの書き直し']);
  assert.equal(chat.status.textContent, '');
});
