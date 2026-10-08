import * as THREE from 'three';
import { WATER_OPTICS } from '../sea/optics.js';

// 体の骨と、足裏のかかと〜つま先を同じ規則で検査する。
export function groundSampler(vrm) {
  const names = ['leftFoot', 'leftToes', 'rightFoot', 'rightToes'];
  const footBones = new Set(names.map(name => vrm.humanoid.getRawBoneNode(name)).filter(Boolean));
  const samples = [];
  const point = new THREE.Vector3();
  vrm.scene.updateMatrixWorld(true);
  vrm.scene.traverse(mesh => {
    if (!mesh.isSkinnedMesh || !mesh.geometry.attributes.skinWeight) return;
    const { position, skinIndex, skinWeight } = mesh.geometry.attributes;
    const candidates = [];
    for (let i = 0; i < position.count; i++) {
      let strongest = 0;
      for (let j = 1; j < 4; j++) if (skinWeight.getComponent(i, j) > skinWeight.getComponent(i, strongest)) strongest = j;
      if (!footBones.has(mesh.skeleton.bones[skinIndex.getComponent(i, strongest)])) continue;
      candidates.push({ index: i, p: mesh.getVertexPosition(i, point).applyMatrix4(mesh.matrixWorld).clone() });
    }
    for (const c of candidates) samples.push({ mesh, index: c.index });
  });
  const bones = Object.values(vrm.humanoid.normalizedHumanBones).map(value => value?.node).filter(Boolean);
  return () => {
    vrm.scene.updateMatrixWorld(true);
    let low = Infinity;
    for (const bone of bones) low = Math.min(low, bone.getWorldPosition(point).y);
    for (const { mesh, index } of samples) low = Math.min(low, mesh.getVertexPosition(index, point).applyMatrix4(mesh.matrixWorld).y);
    return Number.isFinite(low) ? Math.max(0, WATER_OPTICS.floorY - low) : 0;
  };
}
