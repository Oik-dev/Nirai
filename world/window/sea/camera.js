import * as THREE from 'three';

export const CAMERA_LIMITS = Object.freeze({
  floor: .35, ceiling: 3.60, radius: 6,
  focusNear: .9, focusFar: 4.8, pitch: Math.PI * .42,
});
const CENTER = new THREE.Vector3(0, 1.05, -.55);
const UP = new THREE.Vector3(0, 1, 0);

// Pure camera state. Every input (including wheel and focus) uses the same bounds.
export class WorldCamera {
  constructor(camera) {
    this.camera = camera;
    this.focus = null;
    this.yaw = 0;
    this.pitch = 0;
    this.distance = 3.2;
    this.home();
  }

  constrain() {
    const p = this.camera.position;
    p.y = THREE.MathUtils.clamp(p.y, CAMERA_LIMITS.floor, CAMERA_LIMITS.ceiling);
    const x = p.x - CENTER.x, z = p.z - CENTER.z;
    const radius = Math.hypot(x, z);
    if (radius > CAMERA_LIMITS.radius) {
      p.x = CENTER.x + x * CAMERA_LIMITS.radius / radius;
      p.z = CENTER.z + z * CAMERA_LIMITS.radius / radius;
    }
  }

  home() {
    this.focus = null;
    this.camera.position.set(0, 1.40, 3.65);
    this.camera.lookAt(CENTER);
    this.readAngles();
  }

  readAngles() {
    const direction = this.camera.getWorldDirection(new THREE.Vector3());
    this.yaw = Math.atan2(-direction.x, -direction.z);
    this.pitch = Math.asin(THREE.MathUtils.clamp(direction.y, -1, 1));
  }

  lock(avatar) {
    this.focus = avatar;
    const delta = this.camera.position.clone().sub(this.target());
    this.distance = THREE.MathUtils.clamp(delta.length(), CAMERA_LIMITS.focusNear, CAMERA_LIMITS.focusFar);
    this.yaw = Math.atan2(delta.x, delta.z);
    this.pitch = Math.asin(THREE.MathUtils.clamp(delta.y / Math.max(.01, delta.length()), -1, 1));
    this.updateFocus();
  }

  unlock() {
    this.focus = null;
    this.readAngles();
  }

  // 注目した体の胸のあたり（体の中心＝腰の少し上）。体が泳いでも横になっても、体そのものを追う。
  target() {
    return this.focus.body.center.clone().add(new THREE.Vector3(0, .2, 0));
  }

  updateFocus() {
    if (!this.focus) return;
    const target = this.target();
    this.distance = THREE.MathUtils.clamp(this.distance, CAMERA_LIMITS.focusNear, CAMERA_LIMITS.focusFar);
    const minPitch = Math.asin(THREE.MathUtils.clamp((CAMERA_LIMITS.floor - target.y) / this.distance, -1, 1));
    const maxPitch = Math.asin(THREE.MathUtils.clamp((CAMERA_LIMITS.ceiling - target.y) / this.distance, -1, 1));
    this.pitch = THREE.MathUtils.clamp(this.pitch, Math.max(-CAMERA_LIMITS.pitch, minPitch), Math.min(CAMERA_LIMITS.pitch, maxPitch));
    this.camera.position.set(
      target.x + Math.sin(this.yaw) * Math.cos(this.pitch) * this.distance,
      target.y + Math.sin(this.pitch) * this.distance,
      target.z + Math.cos(this.yaw) * Math.cos(this.pitch) * this.distance,
    );
    this.constrain();
    this.camera.lookAt(target);
    this.camera.updateMatrixWorld();
  }

  rotate(dx, dy) {
    this.yaw -= dx * .004;
    this.pitch = THREE.MathUtils.clamp(this.pitch - dy * .004, -CAMERA_LIMITS.pitch, CAMERA_LIMITS.pitch);
    if (this.focus) this.updateFocus();
    else {
      this.camera.quaternion.setFromEuler(new THREE.Euler(this.pitch, this.yaw, 0, 'YXZ'));
      this.camera.updateMatrixWorld();
    }
  }

  move(right, up, forward, delta, fast = false) {
    if (this.focus) {
      this.yaw -= right * delta;
      this.pitch += up * delta * .5;
      this.distance -= forward * delta * 1.8;
      this.updateFocus();
      return;
    }
    const direction = this.camera.getWorldDirection(new THREE.Vector3());
    const sideways = direction.clone().cross(UP).normalize();
    const movement = direction.multiplyScalar(forward).addScaledVector(sideways, right).addScaledVector(UP, up);
    if (movement.lengthSq() > 1) movement.normalize();
    this.camera.position.addScaledVector(movement, delta * (fast ? 3 : 1.35));
    this.constrain();
    this.camera.updateMatrixWorld();
  }

  zoom(amount) {
    if (this.focus) {
      this.distance *= Math.exp(THREE.MathUtils.clamp(amount, -500, 500) * .002);
      this.updateFocus();
    } else this.move(0, 0, -Math.sign(amount), .22);
  }
}

export function installCameraInput(canvas, rig, { avatars, invalidate }) {
  const abort = new AbortController();
  const options = { signal: abort.signal };
  const keys = new Set();
  const raycaster = new THREE.Raycaster();
  const pending = [];
  let pointer = null;
  const clear = () => { keys.clear(); pending.length = 0; pointer = null; };
  const applyPending = () => {
    for (const action of pending) {
      if (action.type === 'rotate') rig.rotate(action.dx, action.dy);
      else rig.zoom(action.amount);
    }
    pending.length = 0;
  };
  canvas.addEventListener('contextmenu', event => event.preventDefault(), options);
  canvas.addEventListener('pointerdown', event => {
    if (event.button !== 0 && event.button !== 2) return;
    canvas.focus({ preventScroll: true });
    pointer = { id: event.pointerId, button: event.button, x: event.clientX, y: event.clientY, travel: 0 };
    canvas.setPointerCapture(event.pointerId);
    event.preventDefault();
  }, options);
  canvas.addEventListener('pointermove', event => {
    if (!pointer || pointer.id !== event.pointerId) return;
    const dx = event.clientX - pointer.x, dy = event.clientY - pointer.y;
    pointer.travel += Math.hypot(dx, dy);
    if (pointer.button === 2) {
      // Focus orbits the character. Dragging up lowers the camera, the reverse of free look.
      const turnY = rig.focus ? -dy : dy;
      const last = pending.at(-1);
      if (last?.type === 'rotate') { last.dx += dx; last.dy += turnY; }
      else pending.push({ type: 'rotate', dx, dy: turnY });
      invalidate();
    }
    pointer.x = event.clientX; pointer.y = event.clientY;
  }, options);
  canvas.addEventListener('pointerup', event => {
    if (!pointer || pointer.id !== event.pointerId) return;
    if (pointer.button === 0 && pointer.travel < 6) {
      applyPending();
      const box = canvas.getBoundingClientRect();
      rig.camera.updateMatrixWorld();
      raycaster.setFromCamera(new THREE.Vector2((event.clientX - box.left) / box.width * 2 - 1, 1 - (event.clientY - box.top) / box.height * 2), rig.camera);
      const candidates = avatars();
      const hits = raycaster.intersectObjects(candidates.map(avatar => avatar.root), true);
      const avatar = hits[0] && candidates.find(item => { let node = hits[0].object; while (node) { if (node === item.root) return true; node = node.parent; } return false; });
      if (avatar) rig.lock(avatar);
      else rig.unlock();
      invalidate();
    }
    pointer = null;
    canvas.releasePointerCapture(event.pointerId);
  }, options);
  canvas.addEventListener('lostpointercapture', () => { pointer = null; }, options);
  canvas.addEventListener('pointercancel', clear, options);
  canvas.addEventListener('blur', clear, options);
  window.addEventListener('blur', clear, options);
  document.addEventListener('visibilitychange', clear, options);
  canvas.addEventListener('wheel', event => {
    event.preventDefault(); pending.push({ type: 'zoom', amount: event.deltaY }); invalidate();
  }, { ...options, passive: false });
  canvas.addEventListener('keydown', event => {
    if (event.code === 'Escape') { applyPending(); rig.unlock(); invalidate(); return; }
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    if (['KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyE', 'ShiftLeft', 'ShiftRight'].includes(event.code)) {
      event.preventDefault(); keys.add(event.code); invalidate();
    }
  }, options);
  window.addEventListener('keyup', event => keys.delete(event.code), options);
  return {
    get active() { return keys.size > 0 || pending.length > 0; },
    update(delta) {
      // Pointer events may arrive much faster than rendering. Apply their total
      // once, then move along that same view direction before drawing the frame.
      applyPending();
      if (!keys.size) return;
      rig.move(Number(keys.has('KeyD')) - Number(keys.has('KeyA')), Number(keys.has('KeyE')) - Number(keys.has('KeyQ')),
        Number(keys.has('KeyW')) - Number(keys.has('KeyS')), delta, keys.has('ShiftLeft') || keys.has('ShiftRight'));
    },
    clear,
    dispose() { clear(); abort.abort(); },
  };
}
