import assert from 'node:assert/strict';
import test from 'node:test';
import { avoidBodies } from './avoidance.js';
import { withinEnclosure, CAMERA_CENTER } from '../sea/habitat.js';

const body = (name, x, y, z, asleep = false, moving = false, yaw = 0, since = '2026-10-16T00:00:00Z') =>
  ({ name, asleep, activity: { name: '居場所でくつろぐ', since }, pose: { x, y, z, yaw, moving } });

function assertSeparated(input, result) {
  for (let i = 0; i < input.length; i++) {
    const a = input[i], pa = result[a.name];
    if (a.name !== 'Serina') assert.ok(withinEnclosure(pa, a.asleep ? .88 : .4), `${a.name}は囲いの外`);
    for (let j = 0; j < i; j++) {
      const b = input[j], pb = result[b.name];
      if (Math.abs(pa.y - pb.y) >= ((a.asleep ? .9 : 1.65) + (b.asleep ? .9 : 1.65)) / 2) continue;
      const gap = (a.asleep ? .88 : .4) + (b.asleep ? .88 : .4);
      assert.ok(Math.hypot(pa.x - pb.x, pa.z - pb.z) >= gap - .015, `${a.name}と${b.name}が重なった`);
    }
  }
}

test('同じ所にいる住人とSerinaの場所をよける。高さが違う体は押さない', () => {
  const input = [body('Serina', 0, .85, -1), body('Holo', 0, .85, -1), body('Claude', 0, .85, -1)];
  const result = avoidBodies(input);
  assert.equal(result.Serina.x, 0);
  assertSeparated(input, result);
  assert.deepEqual(avoidBodies(input), result);
  const high = [body('Serina', 0, .85, -1), body('Holo', 0, 3, -1)];
  assert.equal(avoidBodies(high).Holo.x, 0);
});

test('砂地にSerinaと3人が同じ寝床へ来ても重ならず、囲いに収まる', () => {
  const input = ['Serina', 'Holo', 'Claude', 'Codex'].map(name => body(name, 2.2, .85, -2.6, true));
  const result = avoidBodies(input);
  assertSeparated(input, result);
  for (const name of Object.keys(result)) if (name !== 'Serina') assert.ok(result[name].z <= CAMERA_CENTER.z);
});

test('動く体は進む向きの右によける', () => {
  const input = [body('Serina', 0, .85, -1), body('Holo', 0, .85, -1, false, true, 0)];
  const result = avoidBodies(input);
  assertSeparated(input, result);
  assert.ok(result.Holo.x > 0);
});

test('Serinaを横切る体が細かな時刻でも重ならず、横への変位が急に跳ばない', () => {
  let previous = null;
  for (let t = 0; t <= 8; t += .02) {
    const x = -2 + .5 * t;
    const input = [body('Serina', 0, .85, -1), body('Holo', x, .85, -1, false, true, Math.PI / 2)];
    const result = avoidBodies(input);
    assertSeparated(input, result);
    assert.equal(result.Serina.x, 0);
    if (previous) assert.ok(Math.hypot(result.Holo.x - previous.x, result.Holo.z - previous.z) < .05,
      `時刻${t}で飛んだ`);
    previous = result.Holo;
  }
});

test('二人が交差する道を横へよけ、再計算しても同じ位置', () => {
  for (let t = 0; t <= 8; t += .025) {
    const input = [
      body('Serina', 3, 3.2, -2),
      body('Holo', -2 + .5 * t, .85, -1, false, true, Math.PI / 2,
        '2026-10-16T00:01:00Z'),
      body('Claude', 0, .85, -3 + .5 * t, false, true, 0,
        '2026-10-16T00:02:00Z'),
    ];
    const resolved = avoidBodies(input);
    assertSeparated(input, resolved);
    assert.deepEqual(avoidBodies(input), resolved);
  }
});
