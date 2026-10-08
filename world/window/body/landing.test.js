import assert from 'node:assert/strict';
import test from 'node:test';
import { landingMix, LANDING_SPEED, LANDING_FADE_IN, LANDING_FADE_TO_SIT } from './landing.js';

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);

test('砂地に着いた瞬間は泳ぎ、次に腰を下ろし、最後に座る輪へフェードする', () => {
  const clip = 19 / 30;
  const end = clip / LANDING_SPEED;
  const beginning = landingMix(0, clip);
  assert.equal(beginning.swim, 1);
  assert.equal(beginning.entry, 0);
  assert.equal(beginning.sit, 0);
  const halfway = landingMix(LANDING_FADE_IN / 2, clip);
  near(halfway.swim, .5); near(halfway.entry, .5); near(halfway.sit, 0);
  const lowered = landingMix(end, clip);
  near(lowered.swim, 0); near(lowered.entry, 1); near(lowered.entryTime, clip);
  const curling = landingMix(end + LANDING_FADE_TO_SIT / 2, clip);
  near(curling.entry, .5); near(curling.sit, .5);
  const seated = landingMix(end + LANDING_FADE_TO_SIT, clip);
  near(seated.swim, 0); near(seated.entry, 0); near(seated.sit, 1);
});

test('初回起動でも再読み込みでも経過時刻だけで位置が決まり、古い移動や時刻不明では始めない', () => {
  assert.deepEqual(landingMix(100, .63), landingMix(100, .63));
  assert.equal(landingMix(-1, .63), null);
  assert.equal(landingMix(NaN, .63), null);
  assert.equal(landingMix(0, 0), null);
});
