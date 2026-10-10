import assert from 'node:assert/strict';
import test from 'node:test';
import { placeAt, PLACES } from './place.js';
import { sleepRouteAt } from './sleep-route.js';
import { lifeOf } from '../../sea/life.ts';

const start = Date.parse('2026-10-09T03:00:00.000Z');
const view = { x: 0, y: 1.8, z: 3.65, yaw: 0 };
const swim = { name: '海の中を泳ぐ', since: new Date(start - 100_000).toISOString(), from: null };
const float = { name: '水面の近くで漂う', since: null, from: null };
const sand = { name: '砂地で休む', since: null, from: null };
const first = { asleep: false, at: 0 };
const close = (a, b, tol = 1e-7) => assert.ok(Math.abs(a - b) <= tol, `${a} != ${b}`);
const route = (history, chosen, now) => sleepRouteAt(history, chosen, now, view, 4, .6);

test('窓を開いたら既に眠っている場合は移動せず砂地で横たわる', () => {
  const state = route([{ asleep: true, at: 0 }], swim, start);
  assert.equal(state.activity.name, sand.name);
  assert.equal(state.activity.from, null);
  assert.equal(state.rest.level, 1);
  assert.equal(state.rest.phase, 'sleeping');
  assert.equal(state.seatedAt, 0);
  const point = placeAt(state.activity, start, view);
  close(point.x, PLACES['砂地'].x);
  close(point.z, PLACES['砂地'].z);
});

test('泳いでいる途中で眠ると現在地から砂地へ連続移動し、座るまでは横にならない', () => {
  const history = [first, { asleep: true, at: start, activity: swim }];
  const begin = route(history, swim, start);
  const before = placeAt(swim, start, view);
  const after = placeAt(begin.activity, start, view);
  close(before.x, after.x);
  close(before.y, after.y);
  close(before.z, after.z);
  assert.equal(begin.rest.level, 0);
  const arrival = placeAt(begin.activity, start + 60_000, view).arrivedAt;
  assert.ok(begin.seatedAt > arrival);
  const waiting = route(history, swim, begin.seatedAt - 1);
  assert.equal(waiting.rest.level, 0);
  const half = route(history, swim, begin.seatedAt + 2000);
  close(half.rest.level, .5);
  close(placeAt(half.activity, begin.seatedAt + 2000, view).x, PLACES['砂地'].x);
});

test('既に砂地に座っているなら到着待ちなしで寝転ぶ', () => {
  const history = [first, { asleep: true, at: start, activity: sand }];
  const state = route(history, sand, start + 1000);
  assert.equal(state.seatedAt, start);
  close(state.rest.level, .25);
});

test('途中で起きると寝転び量を逆再生し、起き上がってから選んだ場所へ向かう', () => {
  const history = [first, { asleep: true, at: start, activity: sand },
    { asleep: false, at: start + 2000 }];
  const rising = route(history, float, start + 3000);
  assert.equal(rising.activity.name, sand.name);
  assert.equal(rising.rest.phase, 'getting-up');
  close(rising.rest.level, .25);
  close(rising.releaseAt, start + 4000);
  const resumed = route(history, float, start + 4000);
  assert.equal(resumed.activity.name, float.name);
  close(resumed.rest.level, 0);
  const before = placeAt(rising.activity, start + 4000, view);
  const after = placeAt(resumed.activity, start + 4000, view);
  close(before.x, after.x);
  close(before.y, after.y);
  close(before.z, after.z);
  assert.ok(placeAt(resumed.activity, start + 5000, view).moving);
});

test('座る前に起きたら寝転ばず、その時点の移動位置から活動へ戻る', () => {
  const history = [first, { asleep: true, at: start, activity: swim },
    { asleep: false, at: start + 300 }];
  const after = route(history, float, start + 300);
  assert.equal(after.releaseAt, start + 300);
  close(after.rest.level, 0);
  assert.equal(after.activity.name, float.name);
  const point = placeAt(after.activity, start + 300, view);
  const sandRoute = route(history.slice(0, 2), float, start + 300);
  const previous = placeAt(sandRoute.activity, start + 300, view);
  close(point.x, previous.x);
  close(point.z, previous.z);
});

test('起床後の新しい活動の時刻は維持し、出発点だけ砂地帰路へ置き直す', () => {
  const history = [first, { asleep: true, at: start, activity: sand },
    { asleep: false, at: start + 1000 }];
  const newActivity = {
    name: '海の中を泳ぐ',
    since: new Date(start + 8000).toISOString(),
    from: { name: '水面の近くで漂う', since: null, from: null },
  };
  const state = route(history, newActivity, start + 9000);
  assert.equal(state.activity.name, newActivity.name);
  assert.equal(state.activity.since, newActivity.since);
  assert.equal(state.activity.from.name, '水面の近くで漂う');
  close(Date.parse(state.activity.from.since), state.releaseAt);
  assert.equal(state.activity.from.from.name, sand.name);
});

test('実際のlifeOfで起床後に別の活動を選んでも、その瞬間に砂地への帰路から飛ばない', async () => {
  const began = 1_000_000;
  const iso = at => new Date(at).toISOString();
  const oldRecord = { kind: 'activity', value: '水面の近くで漂う', ts: iso(began - 100_000) };
  const oldLife = await lifeOf([oldRecord], false);
  const history = [first, { asleep: true, at: began, activity: oldLife.activity },
    { asleep: false, at: began + 40_000 }];
  const choiceAt = began + 44_500;
  const previous = route(history, oldLife.activity, choiceAt);
  const newLife = await lifeOf([{ kind: 'activity', value: '海の中を泳ぐ', ts: iso(choiceAt) }, oldRecord], false);
  const chosen = route(history, newLife.activity, choiceAt);
  const a = placeAt(previous.activity, choiceAt, view);
  const b = placeAt(chosen.activity, choiceAt, view);
  close(a.x, b.x); close(a.y, b.y); close(a.z, b.z);
  assert.equal(chosen.activity.name, '海の中を泳ぐ');
  let prior = null;
  for (let frame = -60; frame <= 60; frame++) {
    const t = choiceAt + frame * 1000 / 60;
    const current = placeAt(route(history, frame < 0 ? oldLife.activity : newLife.activity, t).activity, t, view);
    if (prior) {
      const jump = Math.hypot(current.x - prior.x, current.y - prior.y, current.z - prior.z);
      assert.ok(jump < .1, `return-choice position jump ${jump} at frame ${frame}`);
    }
    prior = current;
  }
});

test('起床前から泳いでいた場合も、帰路終了後に活動を選び直す時の位置飛びを防ぐ', async () => {
  const began = 1_000_000;
  const iso = at => new Date(at).toISOString();
  const oldRecord = { kind: 'activity', value: '海の中を泳ぐ', ts: iso(began - 100_000) };
  const oldLife = await lifeOf([oldRecord], false);
  const history = [first, { asleep: true, at: began, activity: oldLife.activity },
    { asleep: false, at: began + 40_000 }];
  const changeAt = began + 120_000;
  const oldPosition = placeAt(route(history, oldLife.activity, changeAt).activity, changeAt, view);
  const newLife = await lifeOf([
    { kind: 'activity', value: '水面の近くで漂う', ts: iso(changeAt) }, oldRecord], false);
  const nextPosition = placeAt(route(history, newLife.activity, changeAt).activity, changeAt, view);
  close(oldPosition.x, nextPosition.x);
  close(oldPosition.y, nextPosition.y);
  close(oldPosition.z, nextPosition.z);
  let prior = null;
  for (let frame = -60; frame <= 60; frame++) {
    const t = changeAt + frame * 1000 / 60;
    const current = placeAt(route(history, frame < 0 ? oldLife.activity : newLife.activity, t).activity, t, view);
    if (prior) {
      const jump = Math.hypot(current.x - prior.x, current.y - prior.y, current.z - prior.z);
      assert.ok(jump < .1, `swim-resume position jump ${jump} at frame ${frame}`);
    }
    prior = current;
  }
});

test('起床途中の新しい活動は、起き終わるまで寝姿を続け、砂地から出発する', async () => {
  const began = 1_000_000;
  const iso = at => new Date(at).toISOString();
  const old = { kind: 'activity', value: '水面の近くで漂う', ts: iso(began - 100_000) };
  const previous = await lifeOf([old], false);
  const history = [first, { asleep: true, at: began, activity: previous.activity },
    { asleep: false, at: began + 40_000 }];
  const choiceAt = began + 42_000;
  const newLife = await lifeOf([{ kind: 'activity', value: '海の中を泳ぐ', ts: iso(choiceAt) }, old], false);
  const rising = route(history, newLife.activity, choiceAt);
  assert.equal(rising.activity.name, sand.name);
  const leaving = route(history, newLife.activity, rising.releaseAt);
  assert.equal(leaving.activity.name, newLife.activity.name);
  const before = placeAt(rising.activity, rising.releaseAt, view);
  const after = placeAt(leaving.activity, rising.releaseAt, view);
  close(before.x, after.x); close(before.y, after.y); close(before.z, after.z);
});

test('帰路後の新活動から二度目の眠りへ移る時も、直前の経路から出発する', async () => {
  const began = 1_000_000;
  const iso = at => new Date(at).toISOString();
  const old = { kind: 'activity', value: '海の中を泳ぐ', ts: iso(began - 100_000) };
  const previous = await lifeOf([old], false);
  const choiceAt = began + 120_000;
  const newLife = await lifeOf([{ kind: 'activity', value: '水面の近くで漂う', ts: iso(choiceAt) }, old], false);
  const history = [first, { asleep: true, at: began, activity: previous.activity },
    { asleep: false, at: began + 40_000 }];
  const sleepAgainAt = choiceAt + 3000;
  const before = placeAt(route(history, newLife.activity, sleepAgainAt).activity, sleepAgainAt, view);
  const after = placeAt(route([...history, { asleep: true, at: sleepAgainAt, activity: newLife.activity }],
    newLife.activity, sleepAgainAt).activity, sleepAgainAt, view);
  close(before.x, after.x); close(before.y, after.y); close(before.z, after.z);
});

test('帰路後に砂地で休むを新しく選んでも、元の位置から砂地へ帰る', async () => {
  const began = 1_000_000;
  const iso = at => new Date(at).toISOString();
  const old = { kind: 'activity', value: '海の中を泳ぐ', ts: iso(began - 100_000) };
  const previous = await lifeOf([old], false);
  const history = [first, { asleep: true, at: began, activity: previous.activity },
    { asleep: false, at: began + 40_000 }];
  const choiceAt = began + 120_000;
  const newLife = await lifeOf([{ kind: 'activity', value: '砂地で休む', ts: iso(choiceAt) }, old], false);
  const before = placeAt(route(history, previous.activity, choiceAt).activity, choiceAt, view);
  const next = route(history, newLife.activity, choiceAt);
  const after = placeAt(next.activity, choiceAt, view);
  close(before.x, after.x); close(before.y, after.y); close(before.z, after.z);
  assert.equal(next.activity.name, '砂地で休む');
  assert.ok(placeAt(next.activity, choiceAt + 500, view).moving);
});

test('砂地で休む選択なら起床後もそのまま座り続ける', () => {
  const history = [first, { asleep: true, at: start, activity: sand },
    { asleep: false, at: start + 1000 }];
  const after = route(history, sand, start + 7000);
  assert.equal(after.activity.from, null);
  assert.equal(after.activity.name, sand.name);
});

test('短い眠りと目覚めの繰り返しでも逆再生を引き継ぐ', () => {
  const history = [first, { asleep: true, at: start, activity: sand },
    { asleep: false, at: start + 1200 },
    { asleep: true, at: start + 1600, activity: sand },
    { asleep: false, at: start + 2200 }];
  const atWake = route(history, float, start + 2200);
  close(atWake.rest.level, .35);
  const complete = route(history, float, atWake.releaseAt);
  close(complete.rest.level, 0);
  assert.equal(complete.activity.name, float.name);
});

test('泳ぎから砂地へ眠り、起きて泳ぎ直す全行程で50msの位置飛びがない', () => {
  const history = [
    first,
    { asleep: true, at: start, activity: swim },
    { asleep: false, at: start + 35_000 },
  ];
  let previous = placeAt(route(history, swim, start - 50).activity, start - 50, view);
  for (let at = start; at <= start + 70_000; at += 50) {
    const state = route(history, swim, at);
    assert.ok(state, `sleep route missing at ${at - start} ms`);
    const current = placeAt(state.activity, at, view);
    const jump = Math.hypot(current.x - previous.x, current.y - previous.y, current.z - previous.z);
    assert.ok(jump <= .1, `position jump ${jump.toFixed(4)}m at ${at - start} ms`);
    previous = current;
  }
});

test('寝返る途中の起床と短時間での再入眠でも位置は飛ばない', () => {
  const history = [
    first,
    { asleep: true, at: start, activity: swim },
    { asleep: false, at: start + 300 },
    { asleep: true, at: start + 450, activity: swim },
    { asleep: false, at: start + 20_000 },
  ];
  let previous = placeAt(route(history, swim, start - 50).activity, start - 50, view);
  for (let at = start; at <= start + 45_000; at += 50) {
    const state = route(history, swim, at);
    const current = placeAt(state.activity, at, view);
    const jump = Math.hypot(current.x - previous.x, current.y - previous.y, current.z - previous.z);
    assert.ok(jump <= .1, `position jump ${jump.toFixed(4)}m at ${at - start} ms`);
    previous = current;
  }
});

test('履歴の形が壊れていたら活動を捏造しない', () => {
  assert.equal(route([], swim, start), null);
  assert.equal(route([{ asleep: true, at: 1 }], swim, start), null);
  assert.equal(route([first, { asleep: true, at: start }, { asleep: false, at: start - 1 }], swim, start), null);
});
