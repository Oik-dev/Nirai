import assert from 'node:assert/strict';
import test from 'node:test';
import { restAt } from './rest.js';

const at = Date.parse('2026-10-09T03:00:00.000Z');
const resting = [{ asleep: false, at: 0 }];
const alreadyAsleep = [{ asleep: true, at: 0 }];
const events = (...changes) => [resting[0], ...changes.map(([asleep, ms]) => ({ asleep, at: at + ms }))];
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test('眠ったまま窓を開いたら砂地で寝ており、開き直しても始めから寝る', () => {
  for (const t of [at, at + 100, at + 60000]) {
    const state = restAt(alreadyAsleep, t, at + 1000, 3);
    near(state.level, 1);
    near(state.clipTime, 3);
    assert.equal(state.phase, 'sleeping');
  }
});

test('泳ぐ途中で眠くなっても、砂地で座り終えるまで横にならない', () => {
  const history = events([true, 0]);
  const seatedAt = at + 7000;
  near(restAt(history, at + 6000, seatedAt, 4).level, 0);
  near(restAt(history, at + 8000, seatedAt, 4).level, .25);
  near(restAt(history, at + 11000, seatedAt, 4).level, 1);
});

test('砂地で座っていれば、眠り始めた時刻から寝転ぶ', () => {
  const history = events([true, 0]);
  const half = restAt(history, at + 1500, at - 4000, 3);
  near(half.level, .5);
  near(half.clipTime, 1.5);
  assert.equal(half.phase, 'reclining');
});

test('途中で目覚めたら今の横になり具合から逆方向に動く', () => {
  const history = events([true, 0], [false, 1250]);
  const before = restAt(history, at + 1249, at, 4);
  const atWake = restAt(history, at + 1250, at, 4);
  const after = restAt(history, at + 1750, at, 4);
  near(before.level, 1249 / 4000);
  near(atWake.level, 1250 / 4000);
  assert.equal(atWake.phase, 'getting-up', '目覚めのちょうどその時刻から逆再生側を選ぶ');
  near(after.level, 750 / 4000);
  assert.equal(after.phase, 'getting-up');
  assert.equal(restAt(history, at + 2500, at, 4).phase, 'sitting');
});

test('短い眠りと目覚めを繰り返しても境目の位置が飛ばない', () => {
  const history = events([true, 0], [false, 950], [true, 1200],
    [false, 1700], [true, 1850], [false, 3000]);
  let previous = restAt(history, at, at, 3).level;
  for (let t = 20; t < 7000; t += 20) {
    const current = restAt(history, at + t, at, 3).level;
    assert.ok(Math.abs(current - previous) <= 20 / 3000 + 1e-9, t);
    previous = current;
  }
  near(restAt(history, at + 7000, at, 3).level, 0);
});

test('眠っても座る前に起きたら、寝転ぶ動きは始まらない', () => {
  const history = events([true, 0], [false, 500]);
  near(restAt(history, at + 5000, at + 3000, 3).level, 0);
});

test('履歴が不正なら位置を推測しない', () => {
  assert.equal(restAt([], at, at, 3), null);
  assert.equal(restAt([{ asleep: true, at: 1 }], at, at, 3), null);
  assert.equal(restAt(resting, at, at, 0), null);
  assert.equal(restAt(events([true, 1200], [false, 400]), at + 5000, at, 3), null);
});
