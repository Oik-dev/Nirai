import assert from 'node:assert/strict';
import test from 'node:test';
import { BodyChoices } from './choices.js';

const catalog = { expressions: ['喜び', '悲しみ', 'custom'], gestures: ['うなずく', '覚えた動き'] };
const snapshot = (expression = '喜び', revision = 'body-1') => ({ catalog, expression, revision });
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
function fixture(options = {}) {
  const body = { expressions: ['happy', 'sad', 'custom'], expression: null, gestures: [],
    setExpression(name) { this.expression = name; },
    async play(name) { this.gestures.push(name); },
  };
  const errors = [];
  let reloads = 0;
  const choices = new BodyChoices({ body: () => body, snapshot: async () => snapshot(),
    reloadAvatar: async () => { reloads++; }, onError: error => errors.push(error), ...options });
  return { body, choices, errors, reloads: () => reloads };
}

test('窓を開く・つなぎ直すと最後の表情だけが戻り、古い身振りは再生しない', async () => {
  let state = { ...snapshot(), records: [{ kind: 'gesture', value: 'うなずく' }] };
  const { choices, body, reloads } = fixture({ snapshot: async () => state });
  await choices.refresh();
  assert.equal(body.expression, 'happy');
  assert.deepEqual(body.gestures, []);
  assert.equal(reloads(), 1);
  state = snapshot('悲しみ');
  await choices.refresh();
  assert.equal(body.expression, 'sad');
  assert.equal(reloads(), 1, '体が同じなら読み直さない');
  state = snapshot(null);
  await choices.refresh();
  assert.equal(body.expression, null);
  assert.deepEqual(body.gestures, []);
});

test('本人の表情の名前を体へ渡し、届いた身振りだけを一度再生する', async () => {
  const { choices, body } = fixture();
  await choices.refresh();
  choices.receive([{ kind: 'expression', value: '悲しみ' }, { kind: 'gesture', value: 'うなずく' }]);
  assert.equal(body.expression, 'sad');
  assert.deepEqual(body.gestures, ['うなずく']);
  choices.receive([{ kind: 'expression', value: '未知の表情' }, { kind: 'gesture', value: '未知の動き' }]);
  assert.equal(body.expression, 'sad');
  assert.deepEqual(body.gestures, ['うなずく']);
  choices.receive([{ kind: 'expression', value: 'なし' }, { kind: 'gesture', value: 'なし' }]);
  assert.equal(body.expression, null);
  assert.deepEqual(body.gestures, ['うなずく']);
  choices.receive([{ kind: 'expression', value: 'custom' }, { kind: 'gesture', value: '覚えた動き' }]);
  assert.equal(body.expression, 'custom');
  assert.deepEqual(body.gestures, ['うなずく', '覚えた動き']);
});

test('記録の読み直し中に届いた新しい表情を、古い記録で上書きしない', async () => {
  const wait = deferred();
  const { choices, body } = fixture({ snapshot: () => wait.promise });
  const loading = choices.refresh();
  choices.receive([{ kind: 'expression', value: '悲しみ' }, { kind: 'gesture', value: 'うなずく' }]);
  assert.deepEqual(body.gestures, []);
  wait.resolve(snapshot('喜び'));
  await loading;
  assert.equal(body.expression, 'sad');
  assert.deepEqual(body.gestures, ['うなずく']);
  await choices.refresh();
  assert.deepEqual(body.gestures, ['うなずく'], '読み直しても身振りを繰り返さない');
});

test('体が変わったら新しい体で表情を戻し、読み込み中の身振りも新しい体へ渡す', async () => {
  let state = snapshot();
  let current;
  const loading = deferred();
  const { choices, body } = fixture({
    body: () => current,
    snapshot: async () => state,
    reloadAvatar: async () => {
      if (state.revision !== 'body-1') {
        await loading.promise;
        current = { ...body, expressions: ['joy'], gestures: [] };
      }
    },
  });
  current = body;
  await choices.refresh();
  state = snapshot('喜び', 'body-2');
  const changed = choices.refresh();
  await Promise.resolve();
  choices.receive([{ kind: 'gesture', value: 'うなずく' }]);
  loading.resolve();
  await changed;
  assert.equal(current.expression, 'joy');
  assert.deepEqual(current.gestures, ['うなずく']);
  assert.deepEqual(body.gestures, []);
});

test('新しいカタログにない古い表情と身振りは新しい体に掛けない', async () => {
  let state = snapshot();
  const { choices, body } = fixture({ snapshot: async () => state });
  await choices.refresh();
  state = { catalog: { expressions: [], gestures: [] }, expression: '喜び', revision: 'body-2' };
  await choices.refresh();
  assert.equal(body.expression, null);
  choices.receive([{ kind: 'expression', value: '喜び' }, { kind: 'gesture', value: 'うなずく' }]);
  assert.equal(body.expression, null);
  assert.deepEqual(body.gestures, []);
});

test('遅れて返った古い読み直しは新しい状態を上書きしない', async () => {
  const first = deferred();
  const second = deferred();
  let count = 0;
  const signals = [];
  const { choices, body } = fixture({ snapshot: signal => {
    signals.push(signal);
    return ++count === 1 ? first.promise : second.promise;
  } });
  const old = choices.refresh();
  const latest = choices.refresh();
  assert.equal(signals[0].aborted, true);
  second.resolve(snapshot('悲しみ', 'body-2'));
  await latest;
  first.resolve(snapshot('喜び', 'body-1'));
  await old;
  assert.equal(body.expression, 'sad');
  assert.equal(choices.revision, 'body-2');
});

test('破棄した窓に遅い読み直しと新しい知らせを反映しない', async () => {
  const wait = deferred();
  const { choices, body, reloads } = fixture({ snapshot: () => wait.promise });
  const loading = choices.refresh();
  choices.dispose();
  choices.receive([{ kind: 'expression', value: '喜び' }, { kind: 'gesture', value: 'うなずく' }]);
  wait.resolve(snapshot());
  await loading;
  assert.equal(reloads(), 0);
  assert.equal(body.expression, null);
  assert.deepEqual(body.gestures, []);
});
