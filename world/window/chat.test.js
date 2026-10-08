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

test('会話と同じ流れで、接続・体の記録・カタログの知らせを窓へ渡す', async t => {
  const calls = [];
  const { chat, sources } = fixture(t, {
    onEventsOpen: () => calls.push('open'),
    onBody: records => calls.push(records),
    onCatalog: () => calls.push('catalog'),
    onVoice: (...voice) => calls.push(voice),
  });
  let reconciled = 0;
  chat.reconcileLatest = async () => { reconciled++; };
  chat.connectEvents();
  const events = sources[0];
  assert.equal(events.url, '/api/events');
  events.onopen();
  const records = [{ kind: 'expression', value: '喜び' }, { kind: 'gesture', value: 'うなずく' }];
  events.receive({ type: 'body', records });
  events.receive({ type: 'catalog' });
  events.receive({ type: 'said', text: 'こんにちは' });
  assert.equal(reconciled, 1);
  assert.deepEqual(calls, ['open', records, 'catalog', ['Serina', 'こんにちは']]);
  chat.sending = true;
  events.receive({ type: 'said', text: '流れから受け取る返事' });
  events.receive({ type: 'body', records });
  assert.equal(chat.refreshPending, true);
  assert.deepEqual(calls.at(-1), records, '返事の最中も体の選びを渡す');
  assert.equal(calls.filter(call => Array.isArray(call) && call[0] === 'Serina').length, 1, '返事の声を二重に渡さない');
});

test('流れの終了後は接続し直し、古い接続からの知らせは受け付けない', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let opened = 0;
  let bodies = 0;
  const { chat, sources, Events } = fixture(t, { onEventsOpen: () => { opened++; }, onBody: () => { bodies++; } });
  chat.connectEvents();
  const first = sources[0];
  first.onopen();
  first.readyState = Events.CLOSED;
  first.onerror();
  t.mock.timers.tick(500);
  assert.equal(sources.length, 2);
  sources[1].onopen();
  first.onopen();
  first.receive({ type: 'body', records: [] });
  sources[1].receive({ type: 'body', records: [] });
  assert.equal(opened, 2);
  assert.equal(bodies, 1);
});
