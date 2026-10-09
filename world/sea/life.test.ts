import assert from 'node:assert/strict';
import test from 'node:test';
import { HOME_ACTIVITY } from '../window/body/activities.js';
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
