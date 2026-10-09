import assert from 'node:assert/strict';
import test from 'node:test';
import { createGateSampler } from './gate-samples.js';

test('目視用と工房の関門で共通の骨・足裏フレームを採る', () => {
  class Vector3 {
    constructor(x = 0, y = 0, z = 0) { Object.assign(this, { x, y, z }); }
    toArray() { return [this.x, this.y, this.z]; }
    applyMatrix4() { return this; }
  }
  const bones = {};
  let offset = 0;
  for (const side of ['left', 'right']) {
    const sign = side === 'left' ? 1 : -1;
    bones[side + 'Foot'] = {
      getWorldPosition(out) { Object.assign(out, { x: sign * .1 + offset, y: .1, z: 0 }); return out; },
    };
  }
  const meshes = Object.entries(bones).map(([name, bone]) => {
    const sign = name === 'leftFoot' ? 1 : -1;
    return {
      isSkinnedMesh: true,
      matrixWorld: {},
      geometry: {
        attributes: {
          position: { count: 1 },
          skinIndex: { getX: () => 0, getY: () => 0, getZ: () => 0, getW: () => 0 },
          skinWeight: { getX: () => 1, getY: () => 0, getZ: () => 0, getW: () => 0 },
        },
      },
      skeleton: { bones: [bone] },
      getVertexPosition(_index, out) {
        Object.assign(out, { x: sign * .1 + offset, y: 0, z: .04 });
        return out;
      },
    };
  });
  const vrm = {
    scene: { traverse: fn => meshes.forEach(fn) },
    humanoid: {
      humanBones: bones,
      getRawBoneNode: name => bones[name],
      getNormalizedBoneNode: () => ({ quaternion: { toArray: () => [0, 0, 0, 1] } }),
    },
  };
  const sampler = createGateSampler({ Vector3 }, vrm, -.2);
  const sampled = sampler.sample(30, 2, frame => { offset = frame * .01; });
  assert.deepEqual(sampled.rest.leftFoot, [.1, .3, 0]);
  assert.deepEqual(sampled.bones.rightFoot.position, [[-.1, .3, 0], [-.09, .3, 0]]);
  assert.deepEqual(sampled.bones.leftFoot.rotation, [[0, 0, 0, 1], [0, 0, 0, 1]]);
  assert.deepEqual(sampled.skin.leftFoot.rest, [[.1, .2, .04]]);
  assert.deepEqual(sampled.skin.leftFoot.position, [[[.1, .2, .04]], [[.11, .2, .04]]]);
  assert.equal(sampled.fps, 30);
});
