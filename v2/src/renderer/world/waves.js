// Random sea adapted from ScottieFox/caustic-volume (MIT, copyright 2026 Scottie).
// See THIRD_PARTY_NOTICES.md. Directions, phases and slow amplitude changes are
// independent; bending each crest avoids the stripes of a few regular sine waves.
export const WAVE_PERIOD = 12;
export const WATER_INDEX = 1.333;

const count = 24;
const unit = 2 * Math.PI / WAVE_PERIOD;
let seed = 5;
const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const number = value => value.toFixed(9);
const waveTerms = Array.from({ length: count }, (_, i) => {
  const length = 1.8 * (0.28 / 1.8) ** (i / (count - 1));
  const direction = i * 2.39996 + (random() - 0.5) * 0.9;
  // Quantize both directions to a periodic lattice so the light field has no seams.
  const kx = Math.round(WAVE_PERIOD / length * Math.cos(direction)) * unit;
  const kz = Math.round(WAVE_PERIOD / length * Math.sin(direction)) * unit;
  const k = Math.hypot(kx, kz);
  const bendX = Math.round(-kz * 0.37 / unit) * unit;
  const bendZ = Math.round(kx * 0.37 / unit) * unit;
  const phase = random() * Math.PI * 2;
  const amplitude = 0.50 * Math.min(1, (0.7 / length) ** 2) / (k * k) * (0.6 + 0.8 * random());
  const f1 = 0.05 + 0.22 * random(), f2 = 0.09 + 0.3 * random();
  const p1 = random() * Math.PI * 2, p2 = random() * Math.PI * 2;
  const shortness = i / (count - 1);
  return `{
    vec2 k = vec2(${number(kx)}, ${number(kz)});
    vec2 bend = vec2(${number(bendX)}, ${number(bendZ)});
    float b = dot(bend, p) + time * 0.29 + ${number(i * 7.1)};
    float phase = dot(k, p) - time * ${number(Math.sqrt(9.81 * k + 0.074 * k ** 3) * 0.38)} + ${number(phase)} + 1.6 * sin(b);
    float a = ${number(amplitude)} * (1.0 + 0.48 * (0.6 * sin(time * ${number(f1)} + ${number(p1)}) + 0.4 * sin(time * ${number(f2)} + ${number(p2)})));
    float detailWeight = mix(1.0, uwWaveDetail, ${number(shortness)});
    wave += a * detailWeight * vec3(sin(phase), cos(phase) * (k + 1.6 * cos(b) * bend));
  }`;
}).join('\n');

// Evaluate once into a height/slope texture, shared by refraction and the surface.
export const WATER_WAVES_GLSL = /* glsl */ `
vec3 waterWave(vec2 p, float time) {
  vec3 wave = vec3(0.0);
  ${waveTerms}
  return wave;
}
`;

export const WATER_NORMAL_GLSL = /* glsl */ `
vec3 waterNormal(vec3 wave) { return normalize(vec3(-wave.y, 1.0, -wave.z)); }
`;

export const WATER_FIELD_GLSL = /* glsl */ `
uniform sampler2D uwWaves;
${WATER_NORMAL_GLSL}
vec2 waterUV(vec2 p) { return p / ${WAVE_PERIOD.toFixed(1)} + 0.5; }
`;
