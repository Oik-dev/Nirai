import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { CAMERA_CENTER, CAMERA_LIMITS, CAMERA_FOV, ENCLOSURE_ASPECT, ENCLOSURE_RADIUS, withinEnclosure } from './habitat.js';

test('囲いの縁の床・水面の近くの体の上端が、いちばん引いた家のカメラの画角に収まる', () => {
  const camera = new THREE.PerspectiveCamera(CAMERA_FOV, ENCLOSURE_ASPECT, .05, 220);
  camera.position.set(CAMERA_CENTER.x, 1.4, CAMERA_CENTER.z + CAMERA_LIMITS.radius);
  camera.lookAt(new THREE.Vector3(CAMERA_CENTER.x, CAMERA_CENTER.y, CAMERA_CENTER.z));
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  assert.ok(ENCLOSURE_RADIUS > 3.5 && ENCLOSURE_RADIUS < 4.5);
  for (let degree = 0; degree < 360; degree += 2) {
    const theta = degree * Math.PI / 180;
    const x = CAMERA_CENTER.x + ENCLOSURE_RADIUS * Math.cos(theta);
    const z = CAMERA_CENTER.z + ENCLOSURE_RADIUS * Math.sin(theta);
    for (const y of [.85, 2.5, 4.6]) {
      const p = new THREE.Vector3(x, y, z).project(camera);
      assert.ok(Math.abs(p.x) <= 1.001 && Math.abs(p.y) <= 1.001, `縁${degree}度、高さ${y}: ${p.x}, ${p.y}`);
    }
  }
  // Serinaの今の泳ぐ輪の奥まで覆う。
  for (let theta = 0; theta < 2 * Math.PI; theta += .01) {
    assert.ok(withinEnclosure({ x: 2.6 * Math.sin(theta), z: -2.4 + 1.6 * Math.cos(theta) }, 0));
  }
});
