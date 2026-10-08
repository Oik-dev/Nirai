import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { parseMotion } from './motions.js';

for (const name of ['泳ぐ', '浮く', '座る', '眠る']) {
  test(`世界の${name}が外部参照なしのVRMAとして読み込める`, async () => {
    const bytes = await readFile(new URL(`../assets/motions/${name}.vrma`, import.meta.url));
    assert.equal(bytes.readUInt32LE(0), 0x46546c67);
    const animation = await parseMotion(bytes);
    assert.ok(animation.duration > 1);
    assert.ok(animation.humanoidTracks.rotation.has('hips'));
    for (const track of animation.humanoidTracks.rotation.values()) {
      const values = track.values;
      let dot = 0;
      for (let i = 0; i < 4; i++) dot += values[i] * values[values.length - 4 + i];
      assert.ok(Math.abs(dot) > .99999, `${name}: rotation seam`);
    }
    const translation = animation.humanoidTracks.translation.get('hips').values;
    const drift = Math.hypot(...[0, 1, 2].map(i => translation[i] - translation[translation.length - 3 + i]));
    assert.ok(drift < .003, `${name}: translation seam ${drift}`);
  });
}
