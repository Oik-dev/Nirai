import assert from 'node:assert/strict';
import test from 'node:test';
import { applyDefaultAppearance } from './appearance.js';

function object({ morphs = 0 } = {}) {
  const mesh = {
    isMesh: true,
    morphTargetInfluences: Array(morphs).fill(0),
    traverse(visitor) { visitor(this); },
  };
  return mesh;
}

test('appearanceの既定optionをvisibilityとmorphへ反映する', async () => {
  const scene = {};
  const shirt = object({ morphs: 1 });
  const jacket = object();
  shirt.parent = scene;
  jacket.parent = scene;
  const nodes = [shirt, jacket];
  const gltf = {
    scene,
    parser: {
      json: {
        nodes: [{ name: 'shirt', mesh: 0 }, { name: 'jacket', mesh: 1 }],
        meshes: [
          { extras: { targetNames: ['fit'] }, primitives: [{ targets: [{}] }] },
          { primitives: [{}] },
        ],
        extras: { nirai: { capabilities: { appearance: {
          schemaVersion: 1,
          controls: [{
            id: 'outfit', label: '衣装', defaultOption: 'normal', options: [
              { id: 'normal', label: '普段着', visibility: [{ node: 0, nodeName: 'shirt', value: true }, { node: 1, nodeName: 'jacket', value: false }], morphs: [{ node: 0, nodeName: 'shirt', index: 0, morphName: 'fit', weight: -1 }] },
              { id: 'jacket', label: '上着', visibility: [{ node: 0, nodeName: 'shirt', value: true }, { node: 1, nodeName: 'jacket', value: true }], morphs: [{ node: 0, nodeName: 'shirt', index: 0, morphName: 'fit', weight: 0 }] },
            ],
          }],
        } } } },
      },
      async getDependency(kind, index) {
        assert.equal(kind, 'node');
        return nodes[index];
      },
    },
  };

  jacket.visible = true;
  const appearance = await applyDefaultAppearance(gltf);
  assert.equal(shirt.visible, true);
  assert.equal(jacket.visible, false);
  assert.equal(shirt.morphTargetInfluences[0], -1);
  appearance.apply({ 衣装: '上着' });
  assert.equal(shirt.visible, true);
  assert.equal(jacket.visible, true);
  assert.equal(shirt.morphTargetInfluences[0], 0);
  appearance.apply({ 衣装: '存在しない' });
  assert.equal(jacket.visible, false, 'カタログにない名前では既定衣装へ戻る');
  assert.equal(shirt.morphTargetInfluences[0], -1);
  appearance.apply({ 衣装: '上着' });
  appearance.apply({});
  assert.equal(jacket.visible, false, '窓を開き直すと記録のない項目は既定');
});

test('appearanceの選択肢が同じ対象を完全指定しなければ拒否する', async () => {
  const scene = {};
  const shirt = object();
  shirt.parent = scene;
  const gltf = {
    scene,
    parser: {
      json: {
        nodes: [{ name: 'shirt', mesh: 0 }], meshes: [{ primitives: [{}] }],
        extras: { nirai: { capabilities: { appearance: {
          schemaVersion: 1,
          controls: [{ id: 'outfit', defaultOption: 'a', options: [
            { id: 'a', visibility: [{ node: 0, nodeName: 'shirt', value: true }], morphs: [] },
            { id: 'b', visibility: [], morphs: [] },
          ] }],
        } } } },
      },
      async getDependency() { return shirt; },
    },
  };
  await assert.rejects(applyDefaultAppearance(gltf), /完全な指定/);
});
