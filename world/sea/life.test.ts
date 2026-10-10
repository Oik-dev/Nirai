import assert from 'node:assert/strict';
import test from 'node:test';
import { HOME_ACTIVITY } from '../window/body/activities.js';
import { activityAt } from '../window/body/activity-route.js';
import { placeAt } from '../window/body/place.js';
import type { BodyRecord } from './body.ts';
import { lifeOf } from './life.ts';

const at = (minute: number) => new Date(Date.UTC(2026, 9, 8, 3, minute)).toISOString();
const line = (minute: number, kind: string, value: string): BodyRecord => ({ ts: at(minute), kind, value, by: 'reply', ref: `ref-${minute}` });

test('何も選んでいなければ、居場所でくつろいでいる', async () => {
  assert.deepEqual(await lifeOf([], true), {
    activity: { name: HOME_ACTIVITY, since: null, from: null }, expression: null, expressionAt: null, gesture: null, asleep: true,
  });
});

test('種類ごとに最後の選択を使い、活動はひとつ前の活動から移る', async () => {
  const newestFirst = [
    line(9, 'gesture', 'うなずく'),
    line(8, 'activity', '海の中を泳ぐ'),
    line(7, 'expression', 'なし'),
    line(6, 'activity', '世界から消えた活動'),
    line(5, 'activity', '水面の近くで漂う'),
    line(4, 'expression', '喜び'),
    line(3, 'gesture', '首をかしげる'),
  ];
  assert.deepEqual(await lifeOf(newestFirst, false), {
    activity: { name: '海の中を泳ぐ', since: at(8), from: { name: '水面の近くで漂う', since: at(5) } },
    expression: null,
    expressionAt: at(7),
    gesture: { name: 'うなずく', at: at(9) },
    asleep: false,
  });
});

test('初めて選んだ活動へは、居場所から移る', async () => {
  const life = await lifeOf([line(2, 'activity', '砂地で休む')], false);
  assert.deepEqual(life.activity, { name: '砂地で休む', since: at(2), from: { name: HOME_ACTIVITY, since: null } });
});

test('要るものがそろったら、それより古い記録は読まない', async () => {
  function* records() {
    yield line(9, 'activity', '窓辺にいる');
    yield line(8, 'expression', '喜び');
    yield line(7, 'gesture', 'おじぎ');
    yield line(6, 'activity', '居場所でくつろぐ');
    throw new Error('読みすぎ');
  }
  assert.equal((await lifeOf(records(), false)).activity.from?.name, '居場所でくつろぐ');
});

test('最後の表情と選んだ時刻を保持して、窓を再起動しても経過時間を判定できる', async () => {
  const life = await lifeOf([line(8, 'expression', '喜び')], false);
  assert.equal(life.expression, '喜び');
  assert.equal(life.expressionAt, at(8));
});

test('服はカタログの各項目ごとに最新選択を復元し、VRM変更で知らない選択は既定へ戻す', async () => {
  const catalog = { expressions: [], gestures: [], appearance: [
    { name: '衣装', options: ['普段着', '上着'] }, { name: '飾り', options: ['花', '星'] },
  ] };
  const records: BodyRecord[] = [
    { ...line(8, 'appearance', '存在しない'), control: '衣装' },
    { ...line(7, 'appearance', '星'), control: '飾り' },
    { ...line(6, 'appearance', '上着'), control: '衣装' },
    { ...line(5, 'appearance', '花'), control: '飾り' },
  ];
  const life = await lifeOf(records, false, catalog);
  assert.deepEqual(life.appearance, { 飾り: '星' }, '最新選択が現VRMに無ければ古い服は復活させない');
  const changed = await lifeOf(records, false, { ...catalog, appearance: [{ name: '別の服', options: ['A', 'B'] }] });
  assert.deepEqual(changed.appearance, {});
  assert.deepEqual((await lifeOf(records, false)).appearance, undefined);
});

const approach = (minute: number, value: string, ref = 'pulse-1'): BodyRecord =>
  ({ ts: at(minute), kind: 'approach', value, by: 'pulse', ref });
const on = (activity: ReturnType<typeof activityAt>, minute: number) => activityAt(activity, Date.parse(at(minute)));
const view = { x: 0, y: 2.2, z: 3.65, yaw: Math.PI };

test('Pulseが窓辺へ来て3分で戻り、通知がなくても時計だけで泳ぎ直す', async () => {
  const life = await lifeOf([
    line(9, 'gesture', 'うなずく'), line(8, 'expression', '喜び'),
    approach(5, 'start'), line(2, 'activity', '海の中を泳ぐ'),
  ], false);
  const before = on(life.activity, 4);
  const visiting = on(life.activity, 6);
  const returned = on(life.activity, 8);
  assert.equal(before.name, '海の中を泳ぐ');
  assert.equal(visiting.name, '窓辺にいる');
  assert.equal(visiting.from?.name, '海の中を泳ぐ');
  assert.equal(returned.name, '海の中を泳ぐ');
  assert.equal(returned.since, at(8));
  assert.equal(returned.from?.name, '窓辺にいる');
  // Same route, no new snapshot: the last point of the visit is the start of the return.
  const justBefore = placeAt(on(life.activity, 7), Date.parse(at(8)) - 10, view);
  const justAfter = placeAt(returned, Date.parse(at(8)), view);
  assert.ok(Math.hypot(justAfter.x - justBefore.x, justAfter.y - justBefore.y, justAfter.z - justBefore.z) < .1);
});

test('Pulse失敗は対応するrefのときだけ戻し、本人の活動選択は訪問を終える', async () => {
  const failed = await lifeOf([
    approach(7, 'failed', 'unrelated'), approach(6, 'failed'),
    approach(5, 'start'), line(2, 'activity', '砂地で休む'),
  ], false);
  assert.equal(on(failed.activity, 5).name, '窓辺にいる');
  assert.equal(on(failed.activity, 6).name, '砂地で休む');
  assert.equal(on(failed.activity, 6).since, at(6));

  const chosen = await lifeOf([
    line(7, 'activity', '海の中を泳ぐ'), approach(5, 'start'),
    line(2, 'activity', '海の中を泳ぐ'),
  ], false);
  assert.equal(on(chosen.activity, 6).name, '窓辺にいる');
  assert.equal(on(chosen.activity, 7).name, '海の中を泳ぐ');
  assert.equal(on(chosen.activity, 8).since, at(7), '3分経過で二重の戻りを挿さない');
});

test('古いPulseと最新Pulseを取り違えず、2回目の訪問にも帰り道がある', async () => {
  const life = await lifeOf([
    approach(11, 'start', 'p2'), approach(8, 'failed', 'p1'),
    approach(5, 'start', 'p1'), line(2, 'activity', '海の中を泳ぐ'),
  ], false);
  assert.equal(on(life.activity, 10).name, '海の中を泳ぐ');
  assert.equal(on(life.activity, 12).name, '窓辺にいる');
  assert.equal(on(life.activity, 14).name, '海の中を泳ぐ');
  assert.equal(on(life.activity, 14).since, at(14));
});

test('長期のPulse訪問2100回でも古い移動経路を再帰せず、過去の記録から帰り道を再現する', async t => {
  const count = 2100;
  const lastStart = 2 + (count - 1) * 4;
  t.mock.timers.enable({ apis: ['Date'], now: Date.parse(at(lastStart + 4)) });
  const records = [line(0, 'activity', '居場所でくつろぐ'), line(1, 'activity', '海の中を泳ぐ')];
  for (let i = 0; i < count; i++) records.push(approach(2 + i * 4, 'start', `pulse-${i}`));
  const life = await lifeOf(records.reverse(), false);
  const visiting = on(life.activity, lastStart + 1);
  const returned = on(life.activity, lastStart + 3);
  assert.equal(visiting.name, '窓辺にいる');
  assert.equal(visiting.from?.name, '海の中を泳ぐ');
  assert.equal(returned.name, '海の中を泳ぐ');
  assert.equal(returned.from?.name, '窓辺にいる');
  assert.equal(returned.from?.from, null, '着地済みの経路を捨て、履歴そのものは保つ');
  for (const choice of [visiting, returned]) {
    const pose = placeAt(choice, Date.parse(at(lastStart + 3)), view);
    assert.ok(Number.isFinite(pose.x) && Number.isFinite(pose.y) && Number.isFinite(pose.z));
  }
  assert.equal(on(life.activity, 3).name, '窓辺にいる', '古い訪問の時刻へも戻れる');
  assert.equal(records.length, count + 2, '入力の記録は削除・変更しない');
});

test('短時間に重なった大量の選択も位置計算でスタックを使い切らない', () => {
  const start = Date.parse(at(2));
  const changes = [];
  for (let i = 0; i < 2100; i++) {
    changes.push({ name: '窓辺にいる', since: new Date(start + i * 2).toISOString() });
    changes.push({ name: '海の中を泳ぐ', since: new Date(start + i * 2 + 1).toISOString() });
  }
  const activity = { name: '海の中を泳ぐ', since: at(1), from: null,
    route: { origin: { name: '海の中を泳ぐ', since: at(1) }, changes } };
  const chosen = activityAt(activity, start + 4200);
  const pose = placeAt(chosen, start + 4200, view);
  assert.equal(chosen.name, '海の中を泳ぐ');
  assert.ok(Number.isFinite(pose.x) && Number.isFinite(pose.z));
});
