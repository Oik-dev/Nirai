import assert from 'node:assert/strict';
import test from 'node:test';
import { appearanceLabels, inspectAppearance } from './appearance-metadata.js';

function document() {
  return {
    nodes: [{ name: 'Body', mesh: 0 }, { name: 'Coat', mesh: 1 }],
    meshes: [
      { extras: { targetNames: ['Fit'] }, primitives: [{ targets: [{}] }] },
      { primitives: [{}] },
    ],
    extras: { nirai: { capabilities: { appearance: { schemaVersion: 1, controls: [{
      id: 'outfit', label: '服装', defaultOption: 'normal',
      options: [
        { id: 'normal', label: '普段着', visibility: [{ node: 1, nodeName: 'Coat', value: false }],
          morphs: [{ node: 0, nodeName: 'Body', index: 0, morphName: 'Fit', weight: -1 }] },
        { id: 'coat', label: 'コート', visibility: [{ node: 1, nodeName: 'Coat', value: true }],
          morphs: [{ node: 0, nodeName: 'Body', index: 0, morphName: 'Fit', weight: 1 }] },
      ],
    }] } } } },
  };
}

test('検査したVRM Metadataの表示名と選択肢だけを精神に渡す', () => {
  const doc = document();
  assert.equal(inspectAppearance(doc).length, 1);
  assert.deepEqual(appearanceLabels(doc), [{ name: '服装', options: ['普段着', 'コート'] }]);
  assert.deepEqual(appearanceLabels({}), []);
  assert.deepEqual(appearanceLabels({ ...doc, extras: undefined }), []);
  // Older metadata can still apply a default, but cannot teach the mind an unnamed choice.
  delete doc.extras.nirai.capabilities.appearance.controls[0].label;
  assert.equal(inspectAppearance(doc).length, 1);
  assert.deepEqual(appearanceLabels(doc), []);
});

test('アクセサリーの選択肢「なし」は有効だが、項目名「なし」と「そのまま」は使えない', () => {
  const doc = document();
  const control = doc.extras.nirai.capabilities.appearance.controls[0];
  control.label = 'チョーカー';
  control.options[0].label = 'あり';
  control.options[1].label = 'なし';
  assert.deepEqual(appearanceLabels(doc), [{ name: 'チョーカー', options: ['あり', 'なし'] }]);
  control.label = 'なし';
  assert.deepEqual(appearanceLabels(doc), []);
  control.label = 'チョーカー';
  control.options[1].label = 'そのまま';
  assert.deepEqual(appearanceLabels(doc), []);
});

test('壊れた参照・表情と競合するMorph・不完全な選択肢を拒否する', () => {
  const cases = [
    doc => { doc.extras.nirai.capabilities.appearance.controls[0].options[0].visibility[0].node = 99; },
    doc => { doc.extras.nirai.capabilities.appearance.controls[0].options[0].visibility[0].nodeName = '別物'; },
    doc => { doc.extras.nirai.capabilities.appearance.controls[0].options[1].morphs[0].weight = Infinity; },
    doc => { doc.extras.nirai.capabilities.appearance.controls[0].options[1].morphs = []; },
    doc => { doc.extras.nirai.capabilities.appearance.controls[0].options[1].morphs[0].morphName = 'wrong'; },
    doc => { doc.extensions = { VRMC_vrm: { expressions: { preset: { happy:
      { morphTargetBinds: [{ node: 0, index: 0 }] } } } } }; },
    doc => { doc.nodes[1].children = [0]; },
  ];
  for (const mutate of cases) {
    const doc = document();
    mutate(doc);
    assert.throws(() => inspectAppearance(doc), /外見/, String(mutate));
  }
});

test('選択肢名が欠落・重複・不正ならカタログから外し、操作範囲を漏らさない', () => {
  for (const name of ['そのまま', '', '長'.repeat(41), '行\n替', '普段着']) {
    const doc = document();
    doc.extras.nirai.capabilities.appearance.controls[0].options[1].label = name;
    assert.deepEqual(appearanceLabels(doc), [], name);
  }
  const doc = document();
  const duplicate = structuredClone(doc.extras.nirai.capabilities.appearance.controls[0]);
  duplicate.id = 'other';
  duplicate.label = '同じ服';
  doc.extras.nirai.capabilities.appearance.controls.push(duplicate);
  assert.throws(() => inspectAppearance(doc), /同じ対象/);
});
