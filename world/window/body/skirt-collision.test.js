import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { VRMSpringBoneManager, VRMSpringBoneJoint, VRMSpringBoneCollider, VRMSpringBoneColliderShapeSphere } from '@pixiv/three-vrm';
import { reinforceSkirtColliders } from './skirt-collision.js';

function fittedVrm() {
  const scene = new THREE.Group();
  const bones = {};
  const groups = [];
  for (const side of ['left', 'right']) {
    const upper = new THREE.Bone();
    upper.position.set(side === 'left' ? -.16 : .16, .85, 0);
    const lower = new THREE.Bone();
    lower.position.set(0, -.4, 0);
    scene.add(upper);
    upper.add(lower);
    bones[side + 'UpperLeg'] = upper;
    bones[side + 'LowerLeg'] = lower;
    const collider = new THREE.Object3D();
    upper.add(collider);
    groups.push({ colliders: [collider] });
    const vertices = [];
    for (let j = 0; j < 4; j++) for (let i = 0; i < 8; i++) {
      const a = i * Math.PI / 4;
      vertices.push(upper.position.x + .075 * Math.cos(a), .8 - j * .1, .075 * Math.sin(a));
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(Array(32 * 4).fill(0), 4));
    geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(Array.from({ length: 32 }, () => [1, 0, 0, 0]).flat(), 4));
    const mesh = new THREE.SkinnedMesh(geometry, new THREE.MeshBasicMaterial());
    scene.add(mesh);
    scene.updateMatrixWorld(true);
    mesh.bind(new THREE.Skeleton([upper, lower]));
  }
  let resets = 0;
  return {
    scene,
    humanoid: { getRawBoneNode: name => bones[name] },
    springBoneManager: {
      colliderGroups: groups,
      joints: new Set([{ colliderGroups: groups }]),
      deleteJoint: () => {},
      addJoint: () => {},
      reset: () => { resets++; },
      get resets() { return resets; },
    },
  };
}

test('humanoidの太もも全体に頂点から採寸したカプセルを追加し、既存springグループに接続する', () => {
  const vrm = fittedVrm();
  const added = reinforceSkirtColliders(vrm);
  assert.equal(added.length, 2);
  for (const { side, collider, radius, length, groups } of added) {
    assert.equal(groups, 1);
    assert.ok(Math.abs(length - .4) < 1e-6);
    assert.ok(radius > .065 && radius < .14, `${side}: ${radius}`);
    assert.ok(Math.abs(collider.shape.tail.length() - .4) < 1e-6);
    assert.equal(collider.shape.radius, radius);
    assert.equal(collider.parent, vrm.humanoid.getRawBoneNode(side + 'UpperLeg'));
    assert.ok(vrm.springBoneManager.colliderGroups.some(group => group.colliders.includes(collider)));
  }
  assert.equal(vrm.springBoneManager.resets, 1);
});

test('衝突グループがない体には追加を強制しない', () => {
  const vrm = fittedVrm();
  vrm.springBoneManager.colliderGroups.length = 0;
  assert.deepEqual(reinforceSkirtColliders(vrm), []);
});

test('同じスカートが複数のcolliderGroupを参照しても追加衝突は1回だけ', () => {
  const vrm = fittedVrm();
  const groups = vrm.springBoneManager.colliderGroups;
  for (let i = 0; i < 9; i++) {
    groups.push({ colliders: [...groups[0].colliders] });
    groups.push({ colliders: [...groups[1].colliders] });
  }
  const joint = [...vrm.springBoneManager.joints][0];
  joint.colliderGroups = groups;
  const added = reinforceSkirtColliders(vrm);
  assert.equal(added.length, 2);
  for (const item of added) {
    assert.equal(item.groups, 1);
    assert.equal(groups.reduce((total, group) =>
      total + group.colliders.filter(collider => collider === item.collider).length, 0), 1);
  }
});

test('人の高さへ拡大したVRMもコライダー半径を世界座標へ合わせる', () => {
  const vrm = fittedVrm();
  const normal = reinforceSkirtColliders(vrm);
  const scaled = fittedVrm();
  scaled.scene.scale.setScalar(1.5);
  const enlarged = reinforceSkirtColliders(scaled);
  assert.equal(normal.length, enlarged.length);
  for (let i = 0; i < normal.length; i++) {
    assert.ok(Math.abs(enlarged[i].radius - normal[i].radius * 1.5) < 1e-6);
  }
});

test('実spring managerは読み込み後に足したカプセルも更新経路へ組み込み、脚の移動に追従する', () => {
  const vrm = fittedVrm();
  const manager = new VRMSpringBoneManager();
  const originals = [];
  const groups = [];
  for (const side of ['left', 'right']) {
    const upper = vrm.humanoid.getRawBoneNode(side + 'UpperLeg');
    const collider = new VRMSpringBoneCollider(new VRMSpringBoneColliderShapeSphere({ radius: 0.05 }));
    upper.add(collider);
    originals.push(collider);
    groups.push({ colliders: [collider] });
  }
  const cloth = new THREE.Bone();
  const tip = new THREE.Bone();
  cloth.position.set(0, 0.8, 0.1);
  tip.position.set(0, -0.3, 0);
  vrm.scene.add(cloth);
  cloth.add(tip);
  manager.addJoint(new VRMSpringBoneJoint(cloth, tip, {}, groups));
  vrm.springBoneManager = manager;
  vrm.scene.updateMatrixWorld(true);
  manager.setInitState();
  manager.update(0); // 読み込みで既に更新順が確定している状態
  const added = reinforceSkirtColliders(vrm);
  assert.equal(added.length, 2);
  for (let step = 0; step < 6; step++) manager.update(1 / 60);
  for (let frame = 0; frame < 3; frame++) {
    vrm.humanoid.getRawBoneNode('leftUpperLeg').position.x = -0.16 + frame;
    vrm.scene.updateMatrixWorld(true);
    manager.update(1 / 30);
    const expected = new THREE.Vector3().setFromMatrixPosition(originals[0].colliderMatrix);
    const actual = new THREE.Vector3().setFromMatrixPosition(added[0].collider.colliderMatrix);
    assert.ok(actual.distanceTo(expected) < 1e-7, `frame ${frame} is tracking thigh`);
  }
});
