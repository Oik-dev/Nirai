import * as THREE from 'three';
import { VRMSpringBoneCollider, VRMSpringBoneColliderShapeCapsule } from '@pixiv/three-vrm';

// Fit one capsule per thigh from vertices bound to its humanoid upper-leg bone.
// This changes only runtime spring physics. The underlying VRM stays untouched.
const _vertex = new THREE.Vector3();
const _kneeWorld = new THREE.Vector3();
const _offset = new THREE.Vector3();
const _axis = new THREE.Vector3();

const ancestorOf = (ancestor, node) => {
  for (let parent = node; parent; parent = parent.parent) if (parent === ancestor) return true;
  return false;
};

function thighRadius(vrm, upper, knee) {
  const length = knee.length();
  if (!(length > 0.01)) return null;
  const axis = _axis.copy(knee).normalize();
  const values = [];
  vrm.scene.updateMatrixWorld(true);
  upper.updateWorldMatrix(true, false);
  vrm.scene.traverse(mesh => {
    if (!mesh.isSkinnedMesh) return;
    const geometry = mesh.geometry;
    const positions = geometry.getAttribute('position');
    const indices = geometry.getAttribute('skinIndex');
    const weights = geometry.getAttribute('skinWeight');
    if (!positions || !indices || !weights) return;
    const upperIndices = mesh.skeleton.bones
      .flatMap((bone, index) => bone === upper ? [index] : []);
    if (!upperIndices.length) return;
    mesh.updateWorldMatrix(true, false);
    for (let i = 0; i < positions.count; i++) {
      let influence = 0;
      for (let j = 0; j < 4; j++) {
        if (upperIndices.includes(indices.getComponent(i, j))) influence += weights.getComponent(i, j);
      }
      if (influence < 0.5) continue;
      _vertex.fromBufferAttribute(positions, i);
      mesh.applyBoneTransform(i, _vertex);
      mesh.localToWorld(_vertex);
      upper.worldToLocal(_vertex);
      const along = _vertex.dot(axis);
      if (along < -length * 0.05 || along > length * 1.05) continue;
      _offset.copy(_vertex).addScaledVector(axis, -along);
      values.push(_offset.length());
    }
  });
  if (values.length < 8) return null;
  values.sort((a, b) => a - b);
  // A robust surface estimate excludes stray cloth and unusually wide weights.
  const surface = values[Math.floor((values.length - 1) * 0.8)];
  return Math.min(length * 0.34, Math.max(length * 0.12, surface + length * 0.02));
}

export function reinforceSkirtColliders(vrm) {
  const manager = vrm.springBoneManager;
  if (!manager) return [];
  const added = [];
  for (const side of ['left', 'right']) {
    const upper = vrm.humanoid.getRawBoneNode(side + 'UpperLeg');
    const lower = vrm.humanoid.getRawBoneNode(side + 'LowerLeg');
    if (!upper || !lower) continue;
    vrm.scene.updateMatrixWorld(true);
    const knee = upper.worldToLocal(lower.getWorldPosition(_kneeWorld)).clone();
    const measured = thighRadius(vrm, upper, knee);
    // SpringBone's capsule radius is in world metres, unlike its bone-local
    // offset and tail. The window may scale the loaded VRM to human height.
    const worldScale = upper.getWorldScale(new THREE.Vector3());
    const radius = measured === null ? null
      : measured * Math.max(worldScale.x, worldScale.y, worldScale.z);
    if (radius === null) continue;

    // Only springs that already collide with the thigh receive the extra
    // capsule. Identify them by the humanoid hierarchy, not avatar names.
    const candidates = manager.colliderGroups.filter(group =>
      group.colliders.some(collider => ancestorOf(upper, collider)));
    const affected = [...manager.joints].filter(joint =>
      joint.colliderGroups.some(group => candidates.includes(group)));
    if (!affected.length) continue;
    const collider = new VRMSpringBoneCollider(new VRMSpringBoneColliderShapeCapsule({
      offset: new THREE.Vector3(),
      tail: knee,
      radius,
    }));
    upper.add(collider);
    // An avatar can list the same physical collider in many groups used by
    // every skirt spring. Cover each affected joint once, not ten times.
    const uncovered = new Set(affected);
    let groupCount = 0;
    while (uncovered.size) {
      const group = candidates.reduce((best, candidate) =>
        [...uncovered].filter(joint => joint.colliderGroups.includes(candidate)).length
          > [...uncovered].filter(joint => joint.colliderGroups.includes(best)).length
          ? candidate : best);
      group.colliders.push(collider);
      groupCount++;
      for (const joint of uncovered) if (joint.colliderGroups.includes(group)) uncovered.delete(joint);
    }
    // The manager caches the dependency traversal on its first update.
    // Re-register affected joints through its public API so the new collider
    // joins that traversal and its colliderMatrix follows the moving thigh.
    for (const joint of affected) {
      manager.deleteJoint(joint);
      manager.addJoint(joint);
    }
    added.push({ side, collider, radius, length: knee.length(), groups: groupCount });
  }
  manager.reset();
  return added;
}
