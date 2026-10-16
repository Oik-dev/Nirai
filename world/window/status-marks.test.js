import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { limitLabel, markLabels, markAnchor } from './status-marks.js';

test('残り時間は秒で進み、推定のときは約を付ける', () => {
  const now = Date.UTC(2026, 9, 16, 2);
  const until = new Date(now + 2 * 3600_000 + 10 * 60_000 + 5000).toISOString();
  assert.equal(limitLabel(until, true, now), '2:10:05');
  assert.equal(limitLabel(until, false, now), '約2:10');
  assert.equal(limitLabel(until, true, now + 1000), '2:10:04');
  assert.equal(limitLabel(until, true, now + 10 * 3600_000), null);
});

test('稼働と停止をstateの優先順位に左右されず一緒に表示する', () => {
  assert.deepEqual(markLabels({ awake: true, stuck: true, unread: 2 }, true).map(x => x.text), ['?', '!', '…']);
  assert.deepEqual(markLabels(undefined).map(x => x.text), []);
});

test('立ち姿も寝姿も腰の真上で、いちばん高い骨の上へ表示する', () => {
  const hips = new THREE.Object3D();
  const head = new THREE.Object3D();
  const hand = new THREE.Object3D();
  hips.position.set(3, 1, 2);
  head.position.set(4, 2.5, 3);
  hand.position.set(5, 3, 4);
  const bones = { hips, head, leftHand: hand };
  const body = { vrm: { humanoid: { getNormalizedBoneNode: name => bones[name] ?? null } } };
  const standing = markAnchor(body);
  assert.equal(standing.x, 3);
  assert.equal(standing.z, 2);
  assert.ok(standing.y > 3);
  head.position.y = 0.6;
  hand.position.y = 0.7;
  const lying = markAnchor(body);
  assert.equal(lying.x, 3);
  assert.ok(lying.y > 1);
  assert.ok(lying.y < standing.y);
});
