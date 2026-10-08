import assert from 'node:assert/strict';
import test from 'node:test';
import { expressionKey, expressionLabel, expressionPresetName, OWNED_EXPRESSIONS } from './catalog.js';

test('表情の日本語名はVRMの世代を越えて一致し、窓が持つ現在の生名へ戻せる', () => {
  assert.equal(expressionLabel('happy'), '喜び');
  assert.equal(expressionLabel('joy'), '喜び');
  assert.equal(expressionLabel('sad'), '悲しみ');
  assert.equal(expressionLabel('sorrow'), '悲しみ');
  assert.equal(expressionLabel('relaxed'), expressionLabel('fun'));
  assert.equal(expressionLabel('照れる'), '照れる');
  assert.equal(expressionKey('喜び', ['happy', 'sad']), 'happy');
  assert.equal(expressionKey('喜び', ['joy']), 'joy');
  assert.equal(expressionKey('照れる', ['照れる']), '照れる');
  assert.equal(expressionKey('joy', ['joy']), 'joy');
  assert.equal(expressionKey('悲しみ', ['happy']), null);
  assert.equal(expressionKey('なし', ['なし']), null);
  assert.equal(expressionKey('そのまま', ['そのまま']), null);
  assert.equal(expressionPresetName('joy', '0'), 'happy');
  assert.equal(expressionPresetName('joy', '1'), null);
  assert.equal(expressionPresetName('blink', '1'), 'blink');
  assert.equal(expressionPresetName('a', '1'), null);
  assert.equal(expressionPresetName('unknown', '0'), null);
});

test('瞬き・視線・発声の表情は、本人が選ぶ名前から除かれる', () => {
  for (const name of OWNED_EXPRESSIONS) assert.equal(expressionKey(name, [name]), null, name);
});
