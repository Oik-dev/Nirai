import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { appendBodyApproach, appendBodyChoice, bodyRecordsNewestFirst } from './body.ts';
import { lifeOf } from './life.ts';
import { appearanceLabels } from '../window/body/appearance-metadata.js';
import { applyDefaultAppearance } from '../window/body/appearance.js';
import { activityAt } from '../window/body/activity-route.js';
import { placeAt } from '../window/body/place.js';
import { sleepRouteAt } from '../window/body/sleep-route.js';

const iso = (t: number) => new Date(t).toISOString();
const ts = Date.parse('2026-10-09T03:00:00.000Z');
const catalog = { expressions: [], gestures: [], activities: ['海の中を泳ぐ', '砂地で休む', '居場所でくつろぐ'] };
const view = { x: 0, y: 2.2, z: 3.65, yaw: Math.PI };
const record = (t: number, kind: string, value: string, by = 'reply', ref = 'r') => ({ ts: iso(t), kind, value, by, ref });
const distance = (a: any, b: any) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);

test('C2独立: 訪問中のwaking同一活動は窓辺に居残らない', async t => {
  const idea = await mkdtemp(join(tmpdir(), 'nirai-c2-independent-'));
  t.after(() => rm(idea, { recursive: true, force: true }));
  t.mock.timers.enable({ apis: ['Date'], now: ts });
  await appendBodyChoice(idea, { by: 'reply', ref: 'r', activity: '海の中を泳ぐ' }, catalog);
  t.mock.timers.setTime(ts + 1_000);
  await appendBodyApproach(idea, { type: 'approach', kind: 'connection', ref: 'p' });
  t.mock.timers.setTime(ts + 2_000);
  const result = await appendBodyChoice(idea, { by: 'waking', ref: 'w', activity: '海の中を泳ぐ' }, catalog);
  const life = await lifeOf(bodyRecordsNewestFirst(idea), false);
  assert.ok(result.some(r => r.kind === 'activity'), 'wakingで訪問終了の記録が必要');
  assert.equal(activityAt(life.activity, ts + 2_000).name, '海の中を泳ぐ');
});

test('C2独立: 同じ表示名の外見項目を片方だけ選んでも他方に波及しない', async () => {
  const root = {};
  const objects = [{ parent: root, visible: false }, { parent: root, visible: false }].map(o => ({
    ...o, isMesh: true, traverse(visitor: (v: unknown) => void) { visitor(this); },
  }));
  const controls = [0, 1].map(i => ({
    id: `outfit-${i}`, label: '衣装', defaultOption: 'off', options: [
      { id: 'off', label: 'なし', visibility: [{ node: i, nodeName: `Item${i}`, value: false }], morphs: [] },
      { id: 'on', label: '着る', visibility: [{ node: i, nodeName: `Item${i}`, value: true }], morphs: [] },
    ],
  }));
  const json = { nodes: [{ name: 'Item0', mesh: 0 }, { name: 'Item1', mesh: 1 }],
    meshes: [{ primitives: [{}] }, { primitives: [{}] }], extras: { nirai: { capabilities: { appearance: { schemaVersion: 1, controls } } } } };
  assert.deepEqual(appearanceLabels(json), [], '重複表示名は曖昧なので全件無効');
  const appearance = await applyDefaultAppearance({ scene: root, parser: { json, getDependency: async (_: string, i: number) => objects[i] } });
  appearance.apply({ 衣装: '着る' });
  assert.deepEqual(objects.map(o => o.visible), [false, false], '重複項目は一括適用しない');
});

test('C2独立: 起床後の砂地からの帰路でPulse来訪・帰還しても瞬間移動しない', async () => {
  const swim = record(ts - 120_000, 'activity', '海の中を泳ぐ');
  const before = await lifeOf([swim], false);
  const pulse = record(ts + 45_000, 'approach', 'start', 'pulse', 'p');
  const after = await lifeOf([pulse, swim], false);
  const history = [{ asleep: false, at: 0 }, { asleep: true, at: ts, activity: before.activity },
    { asleep: false, at: ts + 40_000 }];
  const render = (choice: any, t: number) => {
    const activity = activityAt(choice, t);
    const route = sleepRouteAt(history, activity, t, view, 3, 0);
    return placeAt(route!.activity, t, view);
  };
  const atPulse = ts + 45_000;
  const a = render(before.activity, atPulse);
  const b = render(after.activity, atPulse);
  assert.ok(distance(a, b) < 0.1, `訪問開始時に ${distance(a, b).toFixed(3)}m 飛ぶ`);
  let prior = b;
  for (let t = atPulse + 50; t <= atPulse + 185_000; t += 50) {
    const now = render(after.activity, t);
    assert.ok(distance(prior, now) < 0.1, `${t - atPulse}ms後に ${distance(prior, now).toFixed(3)}m 飛ぶ`);
    prior = now;
  }
});
