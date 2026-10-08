import assert from 'node:assert/strict';
import test from 'node:test';
import { PLACES, placeAt } from './place.js';

const view = Object.freeze({ x: 0, y: 1.4, z: 3.65, yaw: 0 });
const farView = Object.freeze({ x: 0, y: 1.4, z: -10, yaw: 0 });
const T0 = '2026-10-08T12:00:00.000Z';
const at = Date.parse(T0);
const SWIM_PERIOD = 2 * Math.PI * Math.sqrt((2.6 ** 2 + 1.6 ** 2) / 2) / 0.35; // 秒（仕様の式をここでも書く）
const rest = (name, now = at, v = view) => placeAt({ name, since: null, from: null }, now, v);
const near = (actual, expected, eps = 1e-9) => assert.ok(Math.abs(actual - expected) < eps, `${actual} != ${expected}`);
function samePose(actual, expected, eps = 1e-9) {
  for (const key of ['x', 'y', 'z', 'yaw', 'sit']) near(actual[key], expected[key], eps);
  assert.equal(actual.moving, expected.moving);
}
// 向き yaw が (x, z) の方を向いている（同じ向きで、前のほう）。
function assertFaces(p, x, z) {
  const dx = x - p.x, dz = z - p.z;
  near(Math.sin(p.yaw) * dz - Math.cos(p.yaw) * dx, 0);
  assert.ok(Math.sin(p.yaw) * dx + Math.cos(p.yaw) * dz > 0);
}

test('固定の場所は中心にいて、姿勢や腰の下げ幅は place に含めない', () => {
  assert.ok(Object.isFrozen(PLACES));
  const cases = [
    ['居場所でくつろぐ', [0, 0.85, -0.55], 0],
    ['砂地で休む', [2.2, 0.85, -2.6], 1],
    ['水面の近くで漂う', [-1.6, 3.0, -3.0], 0],
  ];
  for (const [name, [x, y, z], sit] of cases) {
    const p = rest(name);
    near(p.x, x); near(p.y, y); near(p.z, z);
    assert.equal('pitch' in p, false);
    assert.equal(p.sit, sit);
    assertFaces(p, 0, 3.65);
  }
});

test('窓辺は見ている方向の前に出て、カメラの方を向き、海の縁と高さの範囲に収まる', () => {
  const front = rest('窓辺にいる');
  near(front.x, 0); near(front.y, 0.85); near(front.z, 1.85); near(front.yaw, 0);

  // 横を向けば、横の前に出る。
  const side = rest('窓辺にいる', at, { x: 2, y: 2.5, z: 3, yaw: Math.PI / 2 });
  near(side.x, 0.2); near(side.y, 1.9); near(side.z, 3);
  assertFaces(side, 2, 3);

  // 遠くを見ても、居場所から5.5 m の縁で止まり、なおカメラの方を向く。
  const far = rest('窓辺にいる', at, farView);
  near(Math.hypot(far.x, far.z + 0.55), 5.5);
  assertFaces(far, 0, -10);

  // 高いところを見ても、腰の高さは3.2 m まで。
  near(rest('窓辺にいる', at, { x: 0, y: 5, z: 3.65, yaw: 0 }).y, 3.2);
});

test('海の中は周期的に回り、動く向きを向くが root を傾けない', () => {
  const swim = { name: '海の中を泳ぐ', since: T0, from: null };
  const first = placeAt(swim, at, view);
  near(first.x, 0); near(first.y, 1.6); near(first.z, -0.8); assert.equal('pitch' in first, false); assert.equal(first.sit, 0);
  samePose(placeAt(swim, at + SWIM_PERIOD * 1000, view), first, 1e-6);

  for (const ms of [0, 1234, 9876, 20000]) {
    const p = placeAt(swim, at + ms, view);
    const q = placeAt(swim, at + ms + 1, view);
    const dx = q.x - p.x, dz = q.z - p.z;
    // 1 ms 先への動きは、向きと同じ方向。
    assert.ok(Math.abs(Math.sin(p.yaw) * dz - Math.cos(p.yaw) * dx) / Math.hypot(dx, dz) < 1e-3);
    assert.ok(Math.sin(p.yaw) * dx + Math.cos(p.yaw) * dz > 0);
    assert.equal('pitch' in p, false);
  }
});

test('移動は出発点から始まり、途中は立って持ち上がり、着くと目的地に止まって座る', () => {
  const origin = rest('居場所でくつろぐ');
  const goal = rest('砂地で休む');
  const dist = Math.hypot(goal.x - origin.x, goal.z - origin.z); // 道のりは立った高さどうしで測る（どちらも腰0.85 m）
  const seconds = Math.min(30, Math.max(3, dist / 0.5));
  const walk = { name: '砂地で休む', since: T0, from: { name: '居場所でくつろぐ', since: '2026-10-08T11:00:00.000Z' } };

  samePose(placeAt(walk, at, view), origin);
  const middle = placeAt(walk, at + seconds * 500, view);
  near(middle.x, (origin.x + goal.x) / 2, 1e-6);
  near(middle.z, (origin.z + goal.z) / 2, 1e-6);
  near(middle.y, 0.85 + Math.min(0.5, 0.15 * dist), 1e-6);
  near(middle.sit, 0);
  samePose(placeAt(walk, at + seconds * 1000, view), goal, 1e-6);
  assert.equal(placeAt(walk, at + seconds * 1000 - 1, view).arrivedAt, null);
  // moving の丸め許容幅内でも、実際の到着前なら到着時刻を渡さない。
  assert.equal(placeAt(walk, at + seconds * 1000 - 0.00048828125, view).arrivedAt, null);
  near(placeAt(walk, at + seconds * 1000, view).arrivedAt, at + seconds * 1000);
  near(placeAt(walk, at + seconds * 1000 + 60_000, view).arrivedAt, at + seconds * 1000);
  // 座ったところから発つときは、まず立ち上がる。
  const back = { name: '居場所でくつろぐ', since: T0, from: { name: '砂地で休む', since: null } };
  samePose(placeAt(back, at, view), goal);
  near(placeAt(back, at + seconds * 500, view).sit, 0);
  samePose(placeAt(back, at + seconds * 1000, view), origin, 1e-6);
});

test('移動の途中は50 msごとに0.1 mより大きく飛ばない（海の中との行き来を含む）', () => {
  const travels = [
    { name: '砂地で休む', since: T0, from: { name: '居場所でくつろぐ', since: null } },
    { name: '居場所でくつろぐ', since: T0, from: { name: '海の中を泳ぐ', since: '2026-10-08T11:59:00.000Z' } },
    { name: '海の中を泳ぐ', since: T0, from: { name: '砂地で休む', since: null } },
    { name: '海の中を泳ぐ', since: T0, from: { name: '窓辺にいる', since: null } },
    { name: '窓辺にいる', since: T0, from: { name: '水面の近くで漂う', since: null } },
    { name: '水面の近くで漂う', since: T0, from: { name: '海の中を泳ぐ', since: '2026-10-08T11:59:00.000Z' } },
  ];
  for (const activity of travels) {
    for (const v of [view, farView]) {
      let prev = placeAt(activity, at - 2000, v);
      let worst = 0;
      for (let t = at - 1950; t <= at + 31000; t += 50) {
        const next = placeAt(activity, t, v);
        worst = Math.max(worst, Math.hypot(next.x - prev.x, next.y - prev.y, next.z - prev.z));
        prev = next;
      }
      assert.ok(worst < 0.1, `${activity.name}（${activity.from.name}から）: ${worst} m`);
    }
  }
});

test('着いたあとは、いつ開いても目的地にいて、同じ入力には同じ答えを返す', () => {
  const later = at + 60 * 60 * 1000;
  const walk = { name: '水面の近くで漂う', since: T0, from: { name: '海の中を泳ぐ', since: '2026-10-08T11:00:00.000Z' } };
  samePose(placeAt(walk, later, view), rest('水面の近くで漂う', later), 1e-6);

  // 海の中へ着いたあとは、途中の経路を知らなくても同じ位置にいる。
  const swim = { name: '海の中を泳ぐ', since: T0, from: { name: '居場所でくつろぐ', since: null } };
  const fresh = { name: '海の中を泳ぐ', since: T0, from: null };
  samePose(placeAt(swim, later, view), placeAt(fresh, later, view), 1e-6);

  const v = { ...view };
  assert.deepEqual(placeAt(swim, later, v), placeAt(swim, later, v));
  assert.deepEqual(v, view);
});

test('知らない活動は居場所でくつろぐと同じに扱う', () => {
  const home = rest('居場所でくつろぐ');
  for (const name of ['知らない活動', 'toString']) assert.deepEqual(rest(name), home);
  const walk = placeAt({ name: '砂地で休む', since: T0, from: { name: '知らない活動', since: null } }, at, view);
  samePose(walk, home);
});
