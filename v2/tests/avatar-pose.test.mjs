import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { VRMHumanoid, VRMUtils } from '@pixiv/three-vrm';
import { setRelaxedArmPose } from '../src/renderer/world/avatar.js';

function model(metaVersion, leftDirection, restAngle, boneRoll) {
  const scene = new THREE.Group();
  const humanBones = {};
  const bone = (name, parent, position) => {
    const node = new THREE.Bone();
    node.position.copy(position);
    parent.add(node);
    humanBones[name] = { node };
    return node;
  };
  const hips = bone('hips', scene, new THREE.Vector3(0, 1, 0));
  const chest = bone('chest', hips, new THREE.Vector3(0, .5, 0));
  for (const [side, direction] of [['left', leftDirection], ['right', -leftDirection]]) {
    const upper = bone(`${side}UpperArm`, chest, new THREE.Vector3(direction * .2, 0, 0));
    upper.quaternion.setFromEuler(new THREE.Euler(.3, boneRoll, -.2));
    const armDirection = new THREE.Vector3(direction * Math.cos(restAngle), -Math.sin(restAngle), 0);
    const localDirection = armDirection.applyQuaternion(upper.quaternion.clone().invert());
    const lower = bone(`${side}LowerArm`, upper, localDirection.clone().multiplyScalar(.3));
    bone(`${side}Hand`, lower, localDirection.clone().multiplyScalar(.25));
  }
  const humanoid = new VRMHumanoid(humanBones);
  scene.add(humanoid.normalizedHumanBonesRoot);
  const vrm = { scene, humanoid, meta: { metaVersion } };
  VRMUtils.rotateVRM0(vrm);
  return vrm;
}

test('relaxed arms point down for both VRM facing conventions and authored bone rotations', () => {
  for (const metaVersion of ['0', '1']) {
    for (const leftDirection of [-1, 1]) {
      for (const restAngle of [0, Math.PI / 6]) {
        const vrm = model(metaVersion, leftDirection, restAngle, Math.PI / 2);
        const bodyBefore = vrm.scene.quaternion.clone();
        for (let repeat = 0; repeat < 2; repeat++) {
          setRelaxedArmPose(vrm.humanoid);
          vrm.humanoid.update();
          vrm.scene.updateMatrixWorld(true);
          assert.ok(vrm.scene.quaternion.equals(bodyBefore), 'relaxing the arms must preserve model facing');
          for (const side of ['left', 'right']) {
            const position = name => vrm.humanoid.getRawBoneNode(`${side}${name}`).getWorldPosition(new THREE.Vector3());
            const shoulder = position('UpperArm'), elbow = position('LowerArm'), hand = position('Hand');
            assert.ok(elbow.y < shoulder.y - .2, `${metaVersion}/${leftDirection}/${restAngle}/${side}: elbow is below shoulder`);
            assert.ok(hand.y < elbow.y - .15, `${side}: hand continues down from elbow`);
            assert.ok(hand.x * shoulder.x > 0, `${side}: hand stays on its own side of the body`);
          }
        }
      }
    }
  }
});
