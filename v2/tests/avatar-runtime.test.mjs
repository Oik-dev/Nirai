import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { VRMExpression, VRMExpressionManager } from '@pixiv/three-vrm';
import { createAvatarAppearance } from '../src/renderer/world/appearance.js';
import { defaultAppearance } from '../src/shared/appearance.ts';
import { completeMorphDeltas } from '../src/renderer/world/morph-deltas.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

function model(names = ['happy', 'angry', 'blink', 'lookLeft', 'aa', 'customSmile']) {
  const expressionManager = new VRMExpressionManager();
  const bindings = {};
  for (const name of names) {
    const expression = new VRMExpression(name);
    bindings[name] = { applied: 0, applyWeight(value) { this.applied = value; }, clearAppliedWeight() { this.applied = 0; } };
    expression.addBind(bindings[name]); expressionManager.registerExpression(expression);
  }
  return { expressionManager, bindings };
}

test('actual GLTFLoader preserves base normals when a negative morph lacks NORMAL deltas', async () => {
  // No external buffers are needed to reproduce the pinned loader's attribute aliasing.
  const json = { asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    accessors: [
      { componentType: 5126, count: 3, type: 'VEC3', min: [0,0,0], max: [1,1,1] },
      { componentType: 5126, count: 3, type: 'VEC3' },
      { componentType: 5126, count: 3, type: 'VEC3', min: [0,0,0], max: [0,0,0] },
      { componentType: 5126, count: 3, type: 'VEC3' },
    ],
    meshes: [{ primitives: [{ attributes: { POSITION: 0, NORMAL: 1 }, targets: [{ POSITION: 2 }, { POSITION: 2, NORMAL: 3 }] }] }],
  };
  const loader = new GLTFLoader();
  loader.register(parser => ({ name: 'NiraiMorphDeltas', beforeRoot() { completeMorphDeltas(parser.json); } }));
  const gltf = await loader.parseAsync(JSON.stringify(json), '');
  const mesh = gltf.scene.children[0];
  const geometry = mesh.geometry;
  geometry.attributes.normal.setXYZ(0, 0, 0, 1);
  mesh.morphTargetInfluences[0] = -1;
  const normal = new THREE.Vector3().fromBufferAttribute(geometry.attributes.normal, 0);
  normal.addScaledVector(new THREE.Vector3().fromBufferAttribute(geometry.morphAttributes.normal[0], 0), -1);
  assert.deepEqual(normal.toArray(), [0,0,1], 'a source-absent delta cannot cancel lighting normals');
  assert.deepEqual(json.meshes[0].primitives[0].targets[0], { POSITION: 2 }, 'input artifact is not modified');
});

function artifact() {
  const scene = new THREE.Group();
  const objects = ['Body', 'Shirt', 'Glasses'].map(name => {
    const object = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    object.name = name; scene.add(object); return object;
  });
  const json = {
    nodes: objects.map((object, mesh) => ({ name: object.name, mesh })), meshes: [{}, {}, {}],
    extras: { nirai: { capabilities: { wardrobe: {
      schemaVersion: 1, mode: 'visibility', bodyBaseNodes: [{ node: 0, nodeName: 'Body' }],
      items: [
        { id: 'top.shirt.1', node: 1, nodeName: 'Shirt', category: 'top', tags: ['shirt'], defaultVisible: true, removable: false },
        { id: 'accessory.glasses.1', node: 2, nodeName: 'Glasses', category: 'accessory', tags: ['glasses'], defaultVisible: false, removable: true },
      ],
    } } } },
  };
  const requests = [];
  return { scene, objects, requests, parser: { json, async getDependency(type, index) { requests.push([type, index]); return objects[index]; } } };
}

function compositeArtifact() {
  const gltf = artifact();
  delete gltf.parser.json.extras.nirai.capabilities.wardrobe;
  gltf.objects[0].morphTargetInfluences = [0, 0];
  gltf.parser.json.meshes[0] = { extras: { targetNames: ['Fit', 'Size'] }, primitives: [{ targets: [{}, {}] }] };
  const visibility = (node, value) => ({ node, nodeName: gltf.parser.json.nodes[node].name, value });
  const morph = (index, weight) => ({ node: 0, nodeName: 'Body', index, morphName: ['Fit', 'Size'][index], weight });
  gltf.parser.json.extras.nirai.capabilities.appearance = { schemaVersion: 1, controls: [
    { id: 'outfit', label: 'Clothes', category: 'outfit', defaultOption: 'normal', options: [
      { id: 'normal', label: 'Normal', visibility: [visibility(1, true), visibility(2, false)], morphs: [morph(0, 0)] },
      { id: 'light', label: 'Light', visibility: [visibility(1, false), visibility(2, true)], morphs: [morph(0, -1)] },
      { id: 'layered', label: 'Layered', visibility: [visibility(1, true), visibility(2, true)], morphs: [morph(0, 1)] },
    ] },
    { id: 'size', label: 'Size', category: 'body', defaultOption: 'normal', options: [
      { id: 'normal', label: 'Normal', visibility: [], morphs: [morph(1, 0)] },
      { id: 'large', label: 'Large', visibility: [], morphs: [morph(1, 1)] },
    ] },
  ] };
  return gltf;
}

test('semantic controls compose mesh and baked morph changes without residue or affecting independent choices', async () => {
  const gltf = compositeArtifact();
  const vrm = model();
  const runtime = await createAvatarAppearance(vrm, gltf);
  assert.deepEqual(runtime.warnings, []);
  assert.ok(!JSON.stringify(runtime.catalog).includes('nodeName'), 'AI sees semantic choices only');
  const choice = defaultAppearance(runtime.catalog);
  choice.choices.size = 'large';
  for (const [outfit, visible, fit] of [
    ['light', [false, true], -1], ['layered', [true, true], 1], ['normal', [true, false], 0],
    ['layered', [true, true], 1], ['light', [false, true], -1], ['normal', [true, false], 0],
  ]) {
    choice.choices.outfit = outfit;
    runtime.apply(choice, { immediate: true });
    runtime.blink(1); runtime.update(1); vrm.expressionManager.update();
    assert.deepEqual(gltf.objects.slice(1).map(node => node.visible), visible);
    assert.deepEqual(gltf.objects[0].morphTargetInfluences, [fit, 1]);
    assert.equal(runtime.settled, true);
  }
  gltf.objects[0].morphTargetInfluences[0] = .5;
  assert.equal(runtime.settled, false, 'accepting a choice is insufficient if actual targets differ');
});

test('invalid semantic metadata is disabled before any target mutation; expression and legacy wardrobe survive', async () => {
  for (const corrupt of [
    meta => { meta.controls[0].options[1].morphs = []; },
    meta => { meta.controls[0].options[1].morphs[0].weight = NaN; },
    meta => { meta.controls[0].options[1].morphs[0].morphName = 'wrong'; },
    meta => { meta.controls[0].options[1].visibility[0].node = 999; },
    meta => { meta.controls[1] = structuredClone(meta.controls[0]); meta.controls[1].id = 'conflict'; },
    (_meta, gltf) => { gltf.parser.json.extensions = { VRMC_vrm: { expressions: { preset: { blink: { morphTargetBinds: [{ node: 0, index: 0 }] } } } } }; },
  ]) {
    const gltf = compositeArtifact();
    const meta = gltf.parser.json.extras.nirai.capabilities.appearance;
    corrupt(meta, gltf);
    const runtime = await createAvatarAppearance(model(), gltf);
    assert.equal(runtime.catalog.controls, undefined);
    assert.ok(runtime.catalog.expressions.length);
    assert.ok(runtime.warnings.length);
    assert.deepEqual(gltf.objects.map(node => node.visible), [true, true, true]);
    assert.deepEqual(gltf.objects[0].morphTargetInfluences, [0, 0]);
  }
});

test('invalid or incomplete semantic choice rejects atomically, and reload restores the same whole choice', async () => {
  const gltf = compositeArtifact();
  const runtime = await createAvatarAppearance(model(), gltf);
  const initial = runtime.appearance;
  for (const choices of [{ outfit: 'light' }, { outfit: 'missing', size: 'normal' }, { outfit: 'light', size: 'normal', node: 'Body' }]) {
    assert.throws(() => runtime.apply({ ...initial, choices }));
    assert.deepEqual(runtime.appearance, initial);
    assert.deepEqual(gltf.objects.slice(1).map(node => node.visible), [true, false]);
  }
  const choice = { ...initial, choices: { outfit: 'light', size: 'large' } };
  runtime.apply(choice);
  const reloaded = compositeArtifact();
  const next = await createAvatarAppearance(model(), reloaded);
  next.apply(choice);
  assert.deepEqual(reloaded.objects.map(node => node.visible), gltf.objects.map(node => node.visible));
  assert.deepEqual(reloaded.objects[0].morphTargetInfluences, gltf.objects[0].morphTargetInfluences);
});

test('catalog exposes actual semantic emotion presets and resolves clothing by glTF node index', async () => {
  const vrm = model();
  const empty = new VRMExpression('sad'); vrm.expressionManager.registerExpression(empty);
  const gltf = artifact();
  // Object names can be rewritten by the loader; runtime must use node indices, not these names.
  gltf.objects[1].name = 'Shirt_renamed';
  const runtime = await createAvatarAppearance(vrm, gltf);
  assert.deepEqual(runtime.catalog.expressions, [{ id: 'happy', is_binary: false }, { id: 'angry', is_binary: false }]);
  assert.deepEqual(gltf.requests, [['node', 0], ['node', 1], ['node', 2]]);
  assert.deepEqual(runtime.catalog.wardrobe[1], {
    id: 'accessory.glasses.1', category: 'accessory', tags: ['glasses'], default_visible: false, removable: true,
  });
  assert.equal(gltf.objects[2].visible, false, 'artifact default visibility is applied');
  assert.equal(gltf.objects[0].visible, true, 'body is not a wardrobe target');
});

test('invalid clothing metadata disables only wardrobe without partially changing visibility', async () => {
  const corruptions = [
    metadata => { metadata.items[1].id = metadata.items[0].id; },
    metadata => { metadata.items[1].node = 1; metadata.items[1].nodeName = 'Shirt'; },
    metadata => { metadata.items[1].node = 0; metadata.items[1].nodeName = 'Body'; },
    metadata => { metadata.items[1].node = 999; },
    metadata => { metadata.items[1].nodeName = 'NotGlasses'; },
    metadata => { metadata.schemaVersion = 2; },
    (_metadata, gltf) => { gltf.objects[1].add(gltf.objects[2]); },
    (_metadata, gltf) => { gltf.objects[1].add(gltf.objects[0]); },
    (metadata, gltf) => {
      metadata.bodyBaseNodes = [];
      gltf.parser.json.nodes[1].children = [0];
      gltf.objects[1].add(gltf.objects[0]);
    },
    (metadata, gltf) => {
      metadata.bodyBaseNodes = [];
      gltf.parser.json.nodes.push({ children: [0] });
      gltf.parser.json.nodes[1].children = [3];
      const group = new THREE.Group(); gltf.objects[1].add(group); group.add(gltf.objects[0]);
    },
    (_metadata, gltf) => { gltf.scene.remove(gltf.objects[2]); },
  ];
  for (const corrupt of corruptions) {
    const gltf = artifact(); corrupt(gltf.parser.json.extras.nirai.capabilities.wardrobe, gltf);
    const runtime = await createAvatarAppearance(model(), gltf);
    assert.deepEqual(runtime.catalog.wardrobe, []);
    assert.equal(runtime.catalog.expressions.length, 2);
    assert.equal(runtime.warnings.length, 1);
    assert.equal(gltf.objects[2].visible, true, 'failed validation must not apply a partial default');
  }
});

test('multi-primitive clothing nodes are allowed while a separate descendant node is not', async () => {
  const gltf = artifact();
  const shirt = new THREE.Group();
  shirt.add(new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial()));
  shirt.add(new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial()));
  gltf.objects[1].removeFromParent(); gltf.objects[1] = shirt; gltf.scene.add(shirt);
  const runtime = await createAvatarAppearance(model(), gltf);
  assert.equal(runtime.catalog.wardrobe.length, 2);
  assert.deepEqual(runtime.warnings, []);
});

test('appearance validates the whole request before changing clothes or expression', async () => {
  const vrm = model(), gltf = artifact();
  const runtime = await createAvatarAppearance(vrm, gltf);
  const valid = { expression: { id: 'happy', weight: .7 }, wardrobe: { 'top.shirt.1': true, 'accessory.glasses.1': true } };
  runtime.apply(valid, { immediate: true });
  assert.equal(gltf.objects[2].visible, true);
  assert.equal(vrm.expressionManager.getValue('happy'), .7);
  const invalid = [
    { ...valid, expression: { id: 'customSmile', weight: 1 } },
    { ...valid, wardrobe: { 'top.shirt.1': false, 'accessory.glasses.1': false } },
    { ...valid, wardrobe: { 'top.shirt.1': true } },
    { ...valid, expression: { id: 'angry', weight: NaN } },
  ];
  for (const value of invalid) {
    assert.throws(() => runtime.apply(value));
    assert.deepEqual(runtime.appearance, valid);
    assert.equal(gltf.objects[2].visible, true);
    assert.equal(vrm.expressionManager.getValue('happy'), .7);
  }
  const copy = runtime.appearance; copy.wardrobe['top.shirt.1'] = false;
  assert.equal(runtime.appearance.wardrobe['top.shirt.1'], true, 'returned values do not mutate controller state');
});

test('expression changes blend from current weights and neutral resets only emotional expression', async () => {
  const vrm = model(), gltf = artifact();
  const runtime = await createAvatarAppearance(vrm, gltf);
  const wardrobe = runtime.appearance.wardrobe;
  runtime.apply({ expression: { id: 'happy', weight: 1 }, wardrobe });
  assert.equal(runtime.settled, false);
  runtime.update(.125);
  assert.equal(vrm.expressionManager.getValue('happy'), .5);
  runtime.apply({ expression: { id: 'happy', weight: 1 }, wardrobe });
  runtime.update(.125);
  assert.equal(vrm.expressionManager.getValue('happy'), 1, 'repeated same target does not restart blend');
  assert.equal(runtime.settled, true);
  runtime.apply({ expression: { id: 'angry', weight: .8 }, wardrobe });
  runtime.update(.125);
  assert.equal(vrm.expressionManager.getValue('happy'), .5);
  assert.equal(vrm.expressionManager.getValue('angry'), .4);
  runtime.blink(.6);
  runtime.apply({ expression: null, wardrobe }, { immediate: true });
  assert.equal(vrm.expressionManager.getValue('happy'), 0);
  assert.equal(vrm.expressionManager.getValue('angry'), 0);
  assert.equal(vrm.expressionManager.getValue('blink'), .6);
  assert.equal(runtime.settled, true, 'paused/reduced-motion can settle immediately');
});

test('binary expressions and authored blink overrides are preserved by the VRM expression manager', async () => {
  const vrm = model(['happy', 'blinkLeft', 'blinkRight']);
  const happy = vrm.expressionManager.getExpression('happy');
  happy.isBinary = true; happy.overrideBlink = 'block';
  const runtime = await createAvatarAppearance(vrm, artifact());
  assert.equal(runtime.catalog.expressions[0].is_binary, true);
  assert.throws(() => runtime.apply({ ...runtime.appearance, expression: { id: 'happy', weight: .4 } }));
  runtime.blink(1); vrm.expressionManager.update();
  assert.equal(vrm.bindings.blinkLeft.applied, 1); assert.equal(vrm.bindings.blinkRight.applied, 1);
  runtime.apply({ ...runtime.appearance, expression: { id: 'happy', weight: 1 } }, { immediate: true });
  runtime.blink(1); vrm.expressionManager.update();
  assert.equal(vrm.bindings.happy.applied, 1);
  assert.equal(vrm.bindings.blinkLeft.applied, 0); assert.equal(vrm.bindings.blinkRight.applied, 0);
  runtime.apply({ ...runtime.appearance, expression: null }, { immediate: true });
  vrm.expressionManager.update();
  assert.equal(vrm.bindings.blinkLeft.applied, 1, 'body function resumes when authored override ends');
});
