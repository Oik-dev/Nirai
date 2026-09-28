import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { CAMERA_LIMITS, WorldCamera, installCameraInput } from '../src/renderer/world/camera.js';
import { WorldFrameLoop } from '../src/renderer/world/frame-loop.js';
import { validateAvatar } from '../src/main/avatar-files.mjs';
import { gazeAngles, GAZE_LIMITS } from '../src/renderer/world/gaze.js';

test('gaze respects anatomical bounds and releases a target behind the body', () => {
  for (let i = -180; i <= 180; i++) {
    const angle = THREE.MathUtils.degToRad(i);
    const gaze = gazeAngles(new THREE.Vector3(Math.sin(angle), 9, Math.cos(angle)), true);
    assert.ok(Math.abs(gaze.yaw) <= GAZE_LIMITS.yaw && Math.abs(gaze.pitch) <= GAZE_LIMITS.pitch);
  }
  assert.deepEqual(gazeAngles(new THREE.Vector3(0, 0, -1), true), { yaw: 0, pitch: 0 });
  assert.deepEqual(gazeAngles(new THREE.Vector3(1, 1, 1), false), { yaw: 0, pitch: 0 });
});

const assertBounds = rig => {
  const p = rig.camera.position;
  assert.ok(p.y >= CAMERA_LIMITS.floor - 1e-6 && p.y <= CAMERA_LIMITS.ceiling + 1e-6, `vertical boundary ${p.y}`);
  assert.ok(Math.hypot(p.x, p.z + .55) <= CAMERA_LIMITS.radius + 1e-6, 'horizontal boundary');
};

test('free camera stays underwater and within the stage after sustained movement and zoom', () => {
  const rig = new WorldCamera(new THREE.PerspectiveCamera(55, 1, .05, 140));
  for (let i = 0; i < 4000; i++) {
    rig.rotate(i % 3 ? 9 : -47, i % 7 ? 15 : -100);
    rig.move(Math.sin(i), Math.cos(i / 31), 1, .05, true);
    rig.zoom(i % 2 ? 1e8 : -1e8);
    assertBounds(rig);
  }
});

test('Focus maintains lock and distance bounds at the surface, floor and maximum zoom', () => {
  const rig = new WorldCamera(new THREE.PerspectiveCamera());
  const root = new THREE.Group(); root.position.set(1.4, .12, -.55);
  rig.lock({ root });
  for (let i = 0; i < 2000; i++) {
    rig.rotate(39, i % 17 ? 40 : -600);
    rig.zoom(i % 120 < 60 ? -1e6 : 1e6);
    rig.move(i % 2 ? -1 : 1, 1, i % 2 ? 1 : -1, .05);
    assertBounds(rig);
    const target = rig.target();
    const actualDistance = rig.camera.position.distanceTo(target);
    assert.ok(actualDistance >= CAMERA_LIMITS.focusNear - 1e-6 && actualDistance <= CAMERA_LIMITS.focusFar + 1e-6);
    const direction = rig.camera.getWorldDirection(new THREE.Vector3());
    assert.ok(direction.dot(target.sub(rig.camera.position).normalize()) > .99999, 'lost lock-on');
  }
  const before = rig.camera.quaternion.clone();
  rig.unlock(); rig.rotate(0, 0);
  assert.ok(before.angleTo(rig.camera.quaternion) < 1e-6, 'unlock jumps');
});

function cameraInput(t, rig, invalidate) {
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
  const previousDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const window = new EventTarget(), document = new EventTarget(), canvas = new EventTarget();
  Object.assign(canvas, { focus() {}, setPointerCapture() {}, releasePointerCapture() {} });
  Object.assign(globalThis, { window, document });
  const input = installCameraInput(canvas, rig, { avatars: () => [], changed() {}, invalidate });
  t.after(() => {
    input.dispose();
    for (const [key, descriptor] of [['window', previousWindow], ['document', previousDocument]]) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  const send = (target, type, properties = {}) => target.dispatchEvent(Object.assign(new Event(type, { cancelable: true }), properties));
  return { input, canvas, window, document, send };
}

function frameClock(options) {
  let now = 0, serial = 0;
  const callbacks = new Map();
  const clock = new WorldFrameLoop({
    available: () => true, continuous: () => false, ...options,
    now: () => now,
    request: callback => { callbacks.set(++serial, callback); return serial; },
    cancel: id => callbacks.delete(id),
  });
  return {
    clock,
    step(time) {
      now = time;
      const batch = [...callbacks.values()]; callbacks.clear();
      for (const callback of batch) callback(time);
    },
    get scheduled() { return callbacks.size; },
  };
}

test('WASD and frequent right-drag input share one camera update per rendered frame', t => {
  const rig = new WorldCamera(new THREE.PerspectiveCamera());
  const expected = new WorldCamera(new THREE.PerspectiveCamera());
  let input, draws = 0;
  const { clock, step } = frameClock({
    continuous: () => input.active,
    draw: delta => { input.update(delta); draws++; },
  });
  const controls = cameraInput(t, rig, () => clock.invalidate());
  ({ input } = controls);
  const { canvas, send } = controls;
  clock.invalidate(); step(0);
  send(canvas, 'keydown', { code: 'KeyW' });
  send(canvas, 'pointerdown', { pointerId: 1, button: 2, clientX: 0, clientY: 0 });
  for (let i = 1; i <= 100; i++) send(canvas, 'pointermove', { pointerId: 1, clientX: i, clientY: i * .25 });
  assert.equal(draws, 1, 'pointer events must not draw intermediate poses');
  assert.ok(rig.camera.quaternion.angleTo(expected.camera.quaternion) < 1e-6, 'rotation waits for the movement frame');
  step(16);
  assert.equal(draws, 1, 'input cannot bypass the frame limit');
  step(34);
  expected.rotate(100, 25); expected.move(0, 0, 1, .034);
  assert.equal(draws, 2);
  assert.ok(rig.camera.quaternion.angleTo(expected.camera.quaternion) < 1e-6);
  assert.ok(rig.camera.position.distanceTo(expected.camera.position) < 1e-9, 'movement uses the same new view direction');
  send(canvas, 'pointerup', { pointerId: 1, clientX: 100, clientY: 25 });
  controls.send(controls.window, 'keyup', { code: 'KeyW' });
  step(68);
  assert.equal(clock.frame, 0);
});

test('frame timing uses elapsed time once, limits event redraws and survives a slow frame', () => {
  let total = 0;
  const times = [];
  const { clock, step } = frameClock({ continuous: () => true, draw: delta => { total += delta; times.push(delta); } });
  clock.invalidate(); step(0);
  for (let time = 1; time <= 1000; time++) {
    clock.invalidate();
    if (time % 17 === 0) step(time);
  }
  assert.equal(times.length, 30, '1000 input events remain within the 30 fps budget');
  assert.ok(Math.abs(total - .986) < 1e-9, 'frame remainder must not be counted again as movement');
  step(2000);
  assert.equal(times.at(-1), .05, 'slow frames cannot teleport the camera');
});

test('paused or reduced-motion camera input redraws on demand and blur discards held and queued input', t => {
  const rig = new WorldCamera(new THREE.PerspectiveCamera());
  let input, available = true, frames = 0;
  const { clock, step } = frameClock({
    available: () => available,
    continuous: () => input.active,
    draw: delta => { input.update(delta); frames++; },
  });
  const controls = cameraInput(t, rig, () => clock.invalidate());
  ({ input } = controls);
  const { canvas, send } = controls;
  clock.invalidate(); step(0);
  assert.equal(clock.frame, 0, 'static world has no continuous RAF');
  send(canvas, 'pointerdown', { pointerId: 1, button: 2, clientX: 0, clientY: 0 });
  send(canvas, 'pointermove', { pointerId: 1, clientX: 20, clientY: 10 });
  send(canvas, 'pointerup', { pointerId: 1, clientX: 20, clientY: 10 });
  step(40);
  assert.equal(frames, 2, 'released short drag still gets a frame while paused');
  assert.equal(clock.frame, 0);
  const settled = rig.camera.position.clone(), orientation = rig.camera.quaternion.clone();
  send(canvas, 'keydown', { code: 'KeyW' });
  send(canvas, 'wheel', { deltaY: 1 });
  send(controls.window, 'blur');
  step(80);
  assert.equal(input.active, false);
  assert.ok(rig.camera.position.equals(settled), 'blur cancels both movement and queued zoom');
  assert.ok(rig.camera.quaternion.equals(orientation));
  assert.equal(clock.frame, 0);
  available = false; clock.sync();
  clock.invalidate(); step(1000);
  assert.equal(frames, 3, 'hidden or lost context cannot draw');
  available = true; clock.sync(); step(1020);
  assert.equal(frames, 4, 'context restoration draws once even when motion is paused');
  assert.equal(clock.frame, 0);
});

function glb(document) {
  const json = Buffer.from(JSON.stringify(document));
  const length = Math.ceil(json.length / 4) * 4;
  const bytes = Buffer.alloc(20 + length, 32);
  bytes.writeUInt32LE(0x46546c67, 0); bytes.writeUInt32LE(2, 4); bytes.writeUInt32LE(bytes.length, 8);
  bytes.writeUInt32LE(length, 12); bytes.writeUInt32LE(0x4e4f534a, 16); json.copy(bytes, 20);
  return bytes;
}

test('avatar boundary rejects non-VRM, truncated GLB and external texture or buffer paths', () => {
  assert.throws(() => validateAvatar(Buffer.from('not a model')));
  assert.throws(() => validateAvatar(glb({ asset: { version: '2.0' } })));
  const base = { asset: { version: '2.0' }, extensions: { VRMC_vrm: { specVersion: '1.0' } } };
  assert.doesNotThrow(() => validateAvatar(glb(base)));
  assert.throws(() => validateAvatar(glb(base).subarray(0, 20)));
  for (const uri of ['https://example.com/image.png', 'file:///C:/secret', '../texture.png']) {
    for (const field of ['buffers', 'images']) assert.throws(() => validateAvatar(glb({ ...base, [field]: [{ uri }] })));
  }
});
