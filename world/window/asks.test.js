import assert from 'node:assert/strict';
import test from 'node:test';
import { MasterAsks } from './asks.js';

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.handlers = {};
    this.hidden = false;
    this.value = '';
    this.textContent = '';
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(event, fn) { this.handlers[event] = fn; }
  act(event) { this.handlers[event]?.({}); }
}

function harness(t, onChange) {
  const previous = { document: globalThis.document, fetch: globalThis.fetch };
  const nodes = Object.fromEntries(['asksButton', 'asksPanel', 'asksList', 'asksWarning', 'asksClose']
    .map(id => [id, new Element()]));
  const document = {
    hidden: false,
    getElementById: id => nodes[id],
    createElement: tag => new Element(tag),
    addEventListener: () => {},
  };
  globalThis.document = document;
  t.after(() => {
    globalThis.document = previous.document;
    globalThis.fetch = previous.fetch;
  });
  const calls = [];
  let items = [];
  let result = 200;
  let fail = false;
  globalThis.fetch = async (path, options) => {
    calls.push([path, options]);
    if (fail) throw new Error('disconnected');
    if (path === '/master/asks') return { ok: true, json: async () => ({ asks: items }) };
    return { ok: result === 200, status: result, json: async () => ({ id: 'R' }) };
  };
  const instance = new MasterAsks(onChange);
  t.after(() => instance.dispose());
  const ticket = (letter, asked = '2026-10-15T05:00:00.000Z') => ({
    letter, asked, resident: 'Holo', work: 'job', from: 'Claude',
    question: '質問\n2行目', letterBody: '依頼の本文',
  });
  return { nodes, calls, instance, ticket,
    setItems(value) { items = value; }, setResult(value) { result = value; },
    setFailure(value) { fail = value; } };
}

test('0枚なら非表示、2枚なら右上で開け、会話欄には触れず原文をそのまま送る', async t => {
  const h = harness(t);
  await h.instance.refresh();
  assert.equal(h.nodes.asksButton.hidden, true);
  h.setItems([h.ticket('A'), h.ticket('B')]);
  await h.instance.refresh();
  assert.equal(h.nodes.asksButton.textContent, '確認 2');
  h.nodes.asksButton.act('click');
  assert.equal(h.nodes.asksPanel.hidden, false);
  assert.equal(h.nodes.asksList.children.length, 2);
  const card = h.nodes.asksList.children[0];
  assert.equal(card.children[1].textContent, '質問\n2行目');
  const input = card.children[3];
  const send = card.children[4];
  input.value = ' 前\r\n改行\n後 ';
  input.act('input');
  const original = input.value;
  send.act('click');
  await new Promise(done => setImmediate(done));
  const post = h.calls.find(([path]) => path === '/master/answer');
  assert.equal(JSON.parse(post[1].body).body, original);
  assert.equal(h.nodes.asksList.children.length, 2, 'GETで残る間は生ログを再取得して表示する');
});

test('体の「？」からその住人の確認チケットを開ける', async t => {
  const h = harness(t);
  const others = { ...h.ticket('C'), resident: 'Codex' };
  h.setItems([h.ticket('H'), others]);
  await h.instance.refresh();
  assert.equal(h.instance.open, false);
  h.instance.openForResident('Codex');
  assert.equal(h.instance.open, true);
  assert.equal(h.nodes.asksPanel.hidden, false);
  h.instance.toggle(false);
  h.instance.openForResident('Serina');
  assert.equal(h.instance.open, false, '問いのない住人では開かない');
});

test('問いが届いたら動きを止めている窓にも更新を知らせる', async t => {
  let changes = 0;
  const h = harness(t, () => changes++);
  await h.instance.refresh();
  const before = changes;
  h.setItems([h.ticket('H')]);
  await h.instance.refresh();
  assert.equal(changes, before + 1);
  await h.instance.refresh();
  assert.equal(changes, before + 1, '同じ問いでは再描画を増やさない');
});

test('409で下書きを残して最新を読み直し、通信不能でも最後の表示を消さない', async t => {
  const h = harness(t);
  h.setItems([h.ticket('A')]);
  await h.instance.refresh();
  h.instance.toggle(true);
  const input = h.nodes.asksList.children[0].children[3];
  input.value = '消してはいけない下書き';
  input.act('input');
  h.setResult(409);
  h.setItems([h.ticket('A', '2026-10-15T06:00:00.000Z')]);
  h.nodes.asksList.children[0].children[4].act('click');
  await new Promise(done => setImmediate(done));
  assert.equal(h.nodes.asksList.children[0].children[3].value, '消してはいけない下書き');
  assert.equal(h.nodes.asksList.children[0].children[5].textContent, 'この問いは、もう変わった');
  assert.equal(h.nodes.asksList.children[0].children[4].disabled, false, '新しい問いの送信ボタンも有効に戻る');
  h.setFailure(true);
  await h.instance.refresh();
  assert.equal(h.nodes.asksButton.hidden, false);
  assert.match(h.nodes.asksWarning.textContent, /郵便局につながらない/);
});
