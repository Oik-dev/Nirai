import { createSeededRandom } from '../sea/sea-random.js';

// なめらかな1次元の雑音（Perlin の勾配雑音を2つの細かさで重ねたもの）。同じ種なら同じ揺れ。値はおよそ -1〜1。
export function smoothNoise(seed) {
  const random = createSeededRandom(seed);
  const slopes = Float32Array.from({ length: 256 }, () => random() * 2 - 1);
  const octave = t => {
    const i = Math.floor(t);
    const f = t - i;
    const fade = f * f * f * (f * (f * 6 - 15) + 10);
    const a = slopes[i & 255] * f;
    const b = slopes[(i + 1) & 255] * (f - 1);
    return (a + (b - a) * fade) * 2;
  };
  return t => Math.max(-1, Math.min(1, octave(t) * .75 + octave(t * 2.17 + 31.4) * .25));
}
