// Random sea adapted from ScottieFox/caustic-volume (MIT, copyright 2026 Scottie).
// See THIRD_PARTY_NOTICES.md. Directions, phases and slow amplitude changes are
// independent; bending each crest avoids the stripes of a few regular sine waves.
export const WAVE_PERIOD = 12;
export const WATER_INDEX = 1.333;

const WAVE_COUNT = 24;
const WAVE_SEED = 5;
const PARK_MILLER_MULTIPLIER = 16807;
const PARK_MILLER_MODULUS = 2147483647;
const LONGEST_WAVELENGTH = 1.8;
const SHORTEST_WAVELENGTH = 0.28;
const DIRECTION_STEP = 2.39996;
const DIRECTION_JITTER = 0.9;
const CREST_BEND = 0.37;
const AMPLITUDE = 0.50;
const STEEPNESS_LENGTH = 0.7;
const AMPLITUDE_BASE = 0.6;
const AMPLITUDE_VARIATION = 0.8;
const DRIFT_FREQUENCY = 0.05;
const DRIFT_FREQUENCY_SPAN = 0.22;
const DRIFT_FREQUENCY_2 = 0.09;
const DRIFT_FREQUENCY_SPAN_2 = 0.3;
const GRAVITY = 9.81;
const CAPILLARY = 0.074;
const PHASE_SPEED = 0.38;
const BEND_TIME_OFFSET = 7.1;

const unit = 2 * Math.PI / WAVE_PERIOD;
let seed = WAVE_SEED;
const random = () => (seed = (seed * PARK_MILLER_MULTIPLIER) % PARK_MILLER_MODULUS) / PARK_MILLER_MODULUS;
const number = value => value.toFixed(9);
const waveTerms = Array.from({ length: WAVE_COUNT }, (_, i) => {
  const length = LONGEST_WAVELENGTH * (SHORTEST_WAVELENGTH / LONGEST_WAVELENGTH) ** (i / (WAVE_COUNT - 1));
  const direction = i * DIRECTION_STEP + (random() - 0.5) * DIRECTION_JITTER;
  // Quantize both directions to a periodic lattice so the light field has no seams.
  const kx = Math.round(WAVE_PERIOD / length * Math.cos(direction)) * unit;
  const kz = Math.round(WAVE_PERIOD / length * Math.sin(direction)) * unit;
  const k = Math.hypot(kx, kz);
  const bendX = Math.round(-kz * CREST_BEND / unit) * unit;
  const bendZ = Math.round(kx * CREST_BEND / unit) * unit;
  const phase = random() * Math.PI * 2;
  const amplitude = AMPLITUDE * Math.min(1, (STEEPNESS_LENGTH / length) ** 2) / (k * k) * (AMPLITUDE_BASE + AMPLITUDE_VARIATION * random());
  const f1 = DRIFT_FREQUENCY + DRIFT_FREQUENCY_SPAN * random();
  const f2 = DRIFT_FREQUENCY_2 + DRIFT_FREQUENCY_SPAN_2 * random();
  const p1 = random() * Math.PI * 2;
  const p2 = random() * Math.PI * 2;
  const shortness = i / (WAVE_COUNT - 1);
  const phaseSpeed = Math.sqrt(GRAVITY * k + CAPILLARY * k ** 3) * PHASE_SPEED;
  return `{
    vec2 k = vec2(${number(kx)}, ${number(kz)});
    vec2 bend = vec2(${number(bendX)}, ${number(bendZ)});
    float b = dot(bend, p) + time * 0.29 + ${number(i * BEND_TIME_OFFSET)};
    float phase = dot(k, p) - time * ${number(phaseSpeed)} + ${number(phase)} + 1.6 * sin(b);
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
