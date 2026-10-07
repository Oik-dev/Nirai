import * as THREE from 'three';

const rad = THREE.MathUtils.degToRad;
export const GAZE_LIMITS = Object.freeze({ yaw: rad(35), pitch: rad(22), neckShare: .28, eyeYaw: 20, eyePitch: 12 });
// 目の小さな動き（度）。向き合っている間は相手の顔の中で細かく、そうでない間は少し大きくさまよう。
export const SACCADE = Object.freeze({ attending: [1.2, .8], wandering: [5, 3], min: .5, max: 2.8 });

export function gazeAngles(direction, active) {
  if (!active || direction.lengthSq() < .0001) return { yaw: 0, pitch: 0 };
  const yaw = Math.atan2(direction.x, direction.z);
  // Behind the shoulders, release attention smoothly instead of turning through 180 degrees.
  const attention = 1 - THREE.MathUtils.smoothstep(Math.abs(yaw), rad(95), rad(140));
  return {
    yaw: THREE.MathUtils.clamp(yaw, -GAZE_LIMITS.yaw, GAZE_LIMITS.yaw) * attention,
    pitch: THREE.MathUtils.clamp(Math.atan2(direction.y, Math.hypot(direction.x, direction.z)), -GAZE_LIMITS.pitch, GAZE_LIMITS.pitch) * attention,
  };
}

// 視線（首・頭・目）。今の姿勢（基準姿勢・身振り・揺らぎ）の上に、親の向きで前から掛ける。
export class NaturalGaze {
  constructor(vrm, root, random = Math.random) {
    this.vrm = vrm;
    this.root = root;
    this.random = random;
    this.yaw = 0; this.pitch = 0; this.eyeYaw = 0; this.eyePitch = 0;
    this.saccade = { yaw: 0, pitch: 0, eyeYaw: 0, eyePitch: 0, wait: 0 };
    this.joints = ['neck', 'head'].map(name => vrm.humanoid.getNormalizedBoneNode(name)).filter(Boolean);
    root.updateMatrixWorld(true);
    const front = vrm.lookAt?.getLookAtWorldDirection(new THREE.Vector3()) ?? new THREE.Vector3(0, 0, 1);
    front.y = 0;
    // 住人の前の向きを、root から見た向きで覚える（root が動いても、毎フレーム今の向きに直す）。
    this.frontInRoot = front.normalize().applyQuaternion(root.getWorldQuaternion(new THREE.Quaternion()).invert());
    if (vrm.lookAt) vrm.lookAt.autoUpdate = false;
  }

  update(delta, camera, active) {
    const rawHead = this.vrm.humanoid.getRawBoneNode('head');
    if (!rawHead) return;
    const front = this.frontInRoot.clone().applyQuaternion(this.root.getWorldQuaternion(new THREE.Quaternion()));
    front.y = 0; front.normalize();
    const right = new THREE.Vector3(0, 1, 0).cross(front).normalize();
    const headPosition = rawHead.getWorldPosition(new THREE.Vector3());
    const direction = camera.position.clone().sub(headPosition);
    const local = new THREE.Vector3(direction.dot(right), direction.y, direction.dot(front));
    const goal = gazeAngles(local, active);
    const blend = 1 - Math.exp(-Math.min(delta, .05) * 4);
    this.yaw += (goal.yaw - this.yaw) * blend;
    this.pitch += (goal.pitch - this.pitch) * blend;
    this.joints.forEach((joint, index) => {
      const share = this.joints.length === 1 ? .72 : index === 0 ? GAZE_LIMITS.neckShare : 1 - GAZE_LIMITS.neckShare;
      const rotation = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), this.yaw * share)
        .multiply(new THREE.Quaternion().setFromAxisAngle(right, -this.pitch * share));
      const parentRotation = joint.parent.getWorldQuaternion(new THREE.Quaternion());
      joint.quaternion.premultiply(parentRotation.clone().invert().multiply(rotation).multiply(parentRotation));
      joint.updateMatrixWorld(true);
    });
    // Update head transforms before asking the model's own eye rig for its angles.
    this.vrm.humanoid.update();
    this.root.updateMatrixWorld(true);
    const lookAt = this.vrm.lookAt;
    if (!lookAt) return;
    lookAt.lookAt(camera.position);
    const frontAttention = active && local.z > 0;
    const eyeYaw = frontAttention ? THREE.MathUtils.clamp(lookAt.yaw, -GAZE_LIMITS.eyeYaw, GAZE_LIMITS.eyeYaw) : 0;
    const eyePitch = frontAttention ? THREE.MathUtils.clamp(lookAt.pitch, -GAZE_LIMITS.eyePitch, GAZE_LIMITS.eyePitch) : 0;
    const eyeBlend = 1 - Math.exp(-Math.min(delta, .05) * 8);
    this.eyeYaw += (eyeYaw - this.eyeYaw) * eyeBlend;
    this.eyePitch += (eyePitch - this.eyePitch) * eyeBlend;
    this.updateSaccade(delta, frontAttention);
    lookAt.yaw = THREE.MathUtils.clamp(this.eyeYaw + this.saccade.eyeYaw, -GAZE_LIMITS.eyeYaw, GAZE_LIMITS.eyeYaw);
    lookAt.pitch = THREE.MathUtils.clamp(this.eyePitch + this.saccade.eyePitch, -GAZE_LIMITS.eyePitch, GAZE_LIMITS.eyePitch);
  }

  // 目は、決まった間でなく跳ぶように小さく動く（跳ぶ動きは速く、30ms ほどで着く）。delta が 0 なら止まったまま。
  updateSaccade(delta, attending) {
    const saccade = this.saccade;
    if (delta <= 0) return;
    saccade.wait -= delta;
    if (saccade.wait <= 0) {
      const [yaw, pitch] = attending ? SACCADE.attending : SACCADE.wandering;
      saccade.yaw = (this.random() * 2 - 1) * yaw;
      saccade.pitch = (this.random() * 2 - 1) * pitch;
      saccade.wait = SACCADE.min + this.random() * (SACCADE.max - SACCADE.min);
    }
    const jump = 1 - Math.exp(-delta / .03);
    saccade.eyeYaw += (saccade.yaw - saccade.eyeYaw) * jump;
    saccade.eyePitch += (saccade.pitch - saccade.eyePitch) * jump;
  }
}
