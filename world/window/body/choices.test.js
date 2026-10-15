import assert from 'node:assert/strict';
import test from 'node:test';
import { BodyChoices, validLife } from './choices.js';

const catalog = { expressions: ['喜び', '悲しみ', 'custom'], gestures: ['うなずく', '覚えた動き'] };
const HOME = { name: '居場所でくつろぐ', since: null, from: null };
const life = (fields = {}) => ({ activity: HOME, expression: '喜び', gesture: null, asleep: false, ...fields });
const snapshot = (fields = {}, revision = 'body-1') => ({
  catalogs: { Serina: catalog }, lives: { Serina: life(fields) }, revisions: { Serina: revision },
});
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
function fixture(options = {}) {
  const body = { expressions: ['happy', 'sad', 'custom'], expression: null, gestures: [], life: null,
    setLife(value) { this.life = value; },
    setAppearance(value) { this.appearance = value; },
    setExpression(name, at) { this.expression = name; this.expressionAt = at; },
    async play(name, since) { this.gestures.push([name, since]); },
  };
  const errors = [];
  let reloads = 0;
  const choices = new BodyChoices({ body: () => body, snapshot: async () => snapshot(),
    reloadAvatar: async () => { reloads++; }, onError: error => errors.push(error), ...options });
  return { body, choices, errors, reloads: () => reloads };
}

test('服の選択を再読み込みなしで切り替え、表示中のVRMへlabelのまま渡す', async () => {
  let state = snapshot({ appearance: { 衣装: '上着' } });
  const { choices, body, reloads } = fixture({ snapshot: async () => state });
  await choices.refresh();
  assert.deepEqual(body.appearance, { 衣装: '上着' });
  assert.equal(reloads(), 1);
  state = snapshot({ appearance: { 衣装: '普段着' } });
  await choices.refresh();
  assert.deepEqual(body.appearance, { 衣装: '普段着' });
  assert.equal(reloads(), 1);
  assert.equal(validLife(life({ appearance: { 衣装: null } })), false);
});

test('暮らしをまるごと体へ渡し、本人の表情の名前を体の表情にする', async () => {
  let state = snapshot();
  const { choices, body, reloads } = fixture({ snapshot: async () => state });
  await choices.refresh();
  assert.deepEqual(body.life, state.lives.Serina);
  assert.equal(body.expression, 'happy');
  assert.equal(reloads(), 1);
  state = snapshot({ expression: 'custom', asleep: true, activity: { name: '砂地で休む', since: '2026-10-08T03:00:00.000Z', from: HOME } });
  await choices.refresh();
  assert.equal(body.expression, 'custom');
  assert.equal(body.life.asleep, true);
  assert.equal(reloads(), 1, '体が同じなら読み直さない');
  state = snapshot({ expression: null });
  await choices.refresh();
  assert.equal(body.expression, null);
});

test('表情を選んだ時刻を体へ渡して、再読み込み時も表情の寿命を延長しない', async () => {
  const at = '2026-10-09T02:00:00.000Z';
  const { choices, body } = fixture({ snapshot: async () => snapshot({ expressionAt: at }) });
  await choices.refresh();
  assert.equal(body.expression, 'happy');
  assert.equal(body.expressionAt, at);
  choices.apply('Serina', ['Serina']);
  assert.equal(body.expressionAt, at, '再適用しても古い選択時刻のまま');
  assert.equal(validLife(life({ expressionAt: 'invalid' })), false);
});

test('身振りは記録の時刻から一度だけ始め、読み直しても繰り返さない', async () => {
  const at = '2026-10-08T03:00:00.000Z';
  let state = snapshot({ gesture: { name: 'うなずく', at } });
  const { choices, body } = fixture({ snapshot: async () => state });
  await choices.refresh();
  await choices.refresh();
  assert.deepEqual(body.gestures, [['うなずく', Date.parse(at)]]);
  state = snapshot({ gesture: { name: 'うなずく', at: '2026-10-08T03:01:00.000Z' } });
  await choices.refresh();
  assert.equal(body.gestures.length, 2, '同じ身振りでも、新しく選んだものは始める');
  state = snapshot({ gesture: { name: '知らない動き', at: '2026-10-08T03:02:00.000Z' } });
  await choices.refresh();
  assert.equal(body.gestures.length, 2);
});

test('新しいカタログにない古い表情と身振りは新しい体に掛けない', async () => {
  let state = snapshot();
  let current;
  const { choices, body } = fixture({
    body: () => current,
    snapshot: async () => state,
    reloadAvatar: async () => { current = { ...body, expressions: [], gestures: [] }; },
  });
  current = body;
  await choices.refresh();
  state = { catalogs: { Serina: { expressions: [], gestures: [] } }, lives: { Serina: life({ gesture: { name: 'うなずく', at: '2026-10-08T03:00:00.000Z' } }) }, revisions: { Serina: 'body-2' } };
  await choices.refresh();
  assert.equal(current.expression, null);
  assert.deepEqual(current.gestures, []);
  assert.deepEqual(current.life, state.lives.Serina, '新しい体にも暮らしを渡す');
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
  second.resolve(snapshot({ expression: '悲しみ' }, 'body-2'));
  await latest;
  first.resolve(snapshot({ expression: '喜び' }, 'body-1'));
  await old;
  assert.equal(body.expression, 'sad');
  assert.equal(choices.revisions.Serina, 'body-2');
});

test('形の違う暮らしは体へ渡さず、破棄した窓には遅い読み直しを反映しない', async () => {
  for (const wrong of [{ ...life(), asleep: 'yes' }, { ...life(), activity: { name: '砂地で休む' } },
    { ...life(), gesture: { name: 'うなずく', at: 'いつか' } }]) assert.equal(validLife(wrong), false);
  assert.equal(validLife(null), true);
  const broken = fixture({ snapshot: async () => ({ catalogs: { Serina: catalog }, lives: { Serina: { activity: HOME } }, revisions: { Serina: 'body-1' } }) });
  assert.equal(await broken.choices.refresh(), true, '一人の記録が壊れても窓全体は続く');
  assert.equal(broken.body.life, null);
  assert.equal(broken.errors.length, 1);

  const wait = deferred();
  const { choices, body, reloads } = fixture({ snapshot: () => wait.promise });
  const loading = choices.refresh();
  choices.dispose();
  wait.resolve(snapshot());
  await loading;
  assert.equal(reloads(), 0);
  assert.equal(body.life, null);
});

test('壊れた住人の体だけを諦め、ほかの住人を描き続ける', async () => {
  const shown = [];
  const errors = [];
  const names = ['A-Claude', 'Holo', 'Serina'];
  const state = {
    lives: Object.fromEntries(names.map(name => [name, life()])),
    revisions: Object.fromEntries(names.map(name => [name, 'v1'])),
    catalogs: Object.fromEntries(names.map(name => [name, catalog])),
  };
  const choices = new BodyChoices({
    body: name => ({ expressions: [], setLife() { shown.push(name); }, setExpression() {} }),
    reloadAvatar: async name => { if (name === 'Holo') throw new Error('VRM不正'); },
    snapshot: async () => state,
    onError: error => errors.push(error.message),
  });
  assert.equal(await choices.refresh(), true);
  assert.deepEqual(shown, ['A-Claude', 'Serina']);
  assert.deepEqual(errors, ['VRM不正']);
  assert.deepEqual(Object.keys(choices.revisions).sort(), ['A-Claude', 'Serina']);
  choices.dispose();
});
