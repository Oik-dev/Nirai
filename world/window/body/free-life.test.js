import assert from 'node:assert/strict';
import test from 'node:test';
import { lifeWithoutMind } from '../../sea/life.ts';
import { activityAt } from './activity-route.js';
import { placeAt } from './place.js';
import { avoidBodies } from './avoidance.js';
import { withinEnclosure } from '../sea/habitat.js';

const view = { x: 0, y: 1.4, z: 3.65, yaw: 0 };

test('Serinaの泳ぐ輪を変えずに、3人の暮らしを2分連写して重なりと位置飛びを確かめる', () => {
  const start = Date.UTC(2026, 9, 16, 7);
  const serina = { name: '海の中を泳ぐ', since: new Date(start - 60000).toISOString(), from: null };
  const choices = { Holo: ['海の中を泳ぐ'], Claude: ['居場所でくつろぐ'], Codex: ['砂地で休む'] };
  let prev = null;
  for (let ms = 0; ms <= 120000; ms += 100) {
    const now = start + ms;
    const original = placeAt(serina, now, view);
    const items = [{ name: 'Serina', asleep: false, activity: serina, pose: original }];
    for (const [name, pastimes] of Object.entries(choices)) {
      const life = lifeWithoutMind({ awake: false }, now, { name, pastimes });
      const activity = activityAt(life.activity, now);
      items.push({ name, asleep: false, activity, pose: placeAt(activity, now, view) });
    }
    const resolved = avoidBodies(items);
    assert.deepEqual(resolved.Serina, original);
    for (let i = 1; i < items.length; i++) {
      const a = items[i], p = resolved[a.name];
      assert.ok(withinEnclosure(p, .4), `${a.name}が囲いの外にいる: ${ms}`);
      for (let j = 0; j < i; j++) {
        const b = items[j], q = resolved[b.name];
        if (Math.abs(p.y - q.y) < 1.65) assert.ok(Math.hypot(p.x - q.x, p.z - q.z) >= .78,
          `${a.name}と${b.name}が重なる: ${ms}`);
      }
      if (prev) assert.ok(Math.hypot(p.x - prev[a.name].x, p.y - prev[a.name].y, p.z - prev[a.name].z) < .24,
        `${a.name}の位置が跳んだ: ${ms} ${JSON.stringify({previous:prev[a.name],point:p,raw:a.pose,others:items.map(v=>[v.name,v.pose])})}`);
    }
    prev = resolved;
  }
});

test('Codex指摘の囲い端とHolo到着の前後で、位置が跳ばずに体が重ならない', () => {
  const serina = { name: '海の中を泳ぐ', since: '2026-10-16T06:59:00.000Z', from: null };
  const at = (name, now) => {
    const life = lifeWithoutMind({ awake: false }, now, { name });
    const activity = activityAt(life.activity, now);
    const input = [
      { name: 'Serina', asleep: false, activity: serina, pose: placeAt(serina, now, view) },
      { name, asleep: life.asleep, activity: life.activity, pose: placeAt(activity, now, view) },
    ];
    return { input, output: avoidBodies(input) };
  };
  for (const [name, stamp] of [['Codex', '2026-10-16T07:47:48.000Z'],
    ['Holo', '2026-10-16T07:55:53.083Z']]) {
    const center = Date.parse(stamp);
    let previous = null;
    for (let now = center - 3_000; now <= center + 3_000; now += 10) {
      const { input, output } = at(name, now);
      const p = output[name], s = output.Serina;
      assert.ok(withinEnclosure(p, .4), `${name}囲い外 ${now}`);
      if (Math.abs(p.y - s.y) < 1.65) assert.ok(Math.hypot(p.x - s.x, p.z - s.z) >= .785,
        `${name}とSerinaが重なる ${now}`);
      if (previous) assert.ok(Math.hypot(p.x - previous.x, p.z - previous.z) < .045,
        `${name}が10msで跳んだ ${now}: ${JSON.stringify({ previous, point: p, input })}`);
      previous = p;
    }
  }
});
