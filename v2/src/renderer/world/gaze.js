import * as THREE from 'three';

const rad = THREE.MathUtils.degToRad;
export const GAZE_LIMITS = Object.freeze({ yaw: rad(35), pitch: rad(22), neckShare: .28, eyeYaw: 20, eyePitch: 12 });

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

export class NaturalGaze {
  constructor(vrm, root) {
    this.vrm = vrm;
    this.root = root;
    this.yaw = 0; this.pitch = 0; this.eyeYaw = 0; this.eyePitch = 0;
    this.joints = ['neck', 'head'].map(name => vrm.humanoid.getNormalizedBoneNode(name)).filter(Boolean);
    this.rest = this.joints.map(joint => joint.quaternion.clone());
    root.updateMatrixWorld(true);
    this.front = vrm.lookAt?.getLookAtWorldDirection(new THREE.Vector3()) ?? new THREE.Vector3(0, 0, 1);
    this.front.y = 0; this.front.normalize();
    this.right = new THREE.Vector3(0, 1, 0).cross(this.front).normalize();
    if (vrm.lookAt) vrm.lookAt.autoUpdate = false;
  }

  update(delta, camera, active) {
    const rawHead = this.vrm.humanoid.getRawBoneNode('head');
    if (!rawHead) return;
    const headPosition = rawHead.getWorldPosition(new THREE.Vector3());
    const direction = camera.position.clone().sub(headPosition);
    const local = new THREE.Vector3(direction.dot(this.right), direction.y, direction.dot(this.front));
    const goal = gazeAngles(local, active);
    const blend = 1 - Math.exp(-Math.min(delta, .05) * 4);
    this.yaw += (goal.yaw - this.yaw) * blend;
    this.pitch += (goal.pitch - this.pitch) * blend;
    this.joints.forEach((joint, index) => {
      const share = this.joints.length === 1 ? .72 : index === 0 ? GAZE_LIMITS.neckShare : 1 - GAZE_LIMITS.neckShare;
      const rotation = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), this.yaw * share)
        .multiply(new THREE.Quaternion().setFromAxisAngle(this.right, -this.pitch * share));
      const parentRotation = joint.parent.getWorldQuaternion(new THREE.Quaternion());
      joint.quaternion.copy(parentRotation.clone().invert().multiply(rotation).multiply(parentRotation)).multiply(this.rest[index]);
      joint.updateMatrixWorld(true);
    });
    // Update head transforms before asking the model's own eye rig for its angles.
    this.vrm.humanoid.update();
    this.root.updateMatrixWorld(true);
    const lookAt = this.vrm.lookAt;
    if (lookAt) {
      lookAt.lookAt(camera.position);
      const frontAttention = active && local.z > 0;
      const eyeYaw = frontAttention ? THREE.MathUtils.clamp(lookAt.yaw, -GAZE_LIMITS.eyeYaw, GAZE_LIMITS.eyeYaw) : 0;
      const eyePitch = frontAttention ? THREE.MathUtils.clamp(lookAt.pitch, -GAZE_LIMITS.eyePitch, GAZE_LIMITS.eyePitch) : 0;
      const eyeBlend = 1 - Math.exp(-Math.min(delta, .05) * 8);
      this.eyeYaw += (eyeYaw - this.eyeYaw) * eyeBlend;
      this.eyePitch += (eyePitch - this.eyePitch) * eyeBlend;
      lookAt.yaw = this.eyeYaw; lookAt.pitch = this.eyePitch;
    }
  }
}
