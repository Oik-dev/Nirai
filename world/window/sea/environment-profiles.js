// Renderer-local lighting/optical keyframes. Geometry and user sea controls stay outside this module.
// There is deliberately no "evening" profile: the day keyframe cools directly into night.
export const TIME_OF_DAY_VALUES = Object.freeze(['morning', 'day', 'night']);
export const TIME_OF_DAY_HOURS = Object.freeze({ morning: 7, day: 13, night: 23 });
export const DEFAULT_TIME_OF_DAY = 'day';
export const DEFAULT_ENVIRONMENT_HOUR = TIME_OF_DAY_HOURS[DEFAULT_TIME_OF_DAY];

const rgb = (...values) => Object.freeze(values);
const profile = value => Object.freeze({
  ...value,
  sun: Object.freeze(value.sun),
  hemisphere: Object.freeze(value.hemisphere),
  fill: Object.freeze(value.fill),
  water: Object.freeze(value.water),
  surface: Object.freeze(value.surface),
});

export const ENVIRONMENT_PROFILES = Object.freeze({
  morning: profile({
    sun: {
      lightColor: 0xfff0df,
      lightIntensity: 2.15,
      radiance: rgb(2.45, 2.34, 2.12),
      absorption: rgb(0.084, 0.030, 0.022),
    },
    hemisphere: { skyColor: 0xa9deec, groundColor: 0x829c94, intensity: 1.35 },
    fill: { color: 0xccefff, intensity: 0.82 },
    water: {
      horizon: 0x087ba8,
      zenith: 0x55c4d5,
      abyss: 0x064767,
      glow: 0xa4e5ea,
      floorAverage: 0xb8dfe1,
    },
    surface: {
      horizon: rgb(0.56, 0.73, 0.96),
      zenith: rgb(0.23, 0.44, 0.75),
      glowColor: rgb(1.0, 0.97, 0.92),
      glowWide: 0.20,
      glowTight: 0.78,
      discColor: rgb(1.0, 0.95, 0.86),
      discIntensity: 10.0,
    },
    shaftIntensityMultiplier: 0.82,
    causticsIntensityMultiplier: 0.85,
  }),
  day: profile({
    sun: {
      lightColor: 0xfff3df,
      lightIntensity: 2.7,
      radiance: rgb(3.1, 2.945, 2.604),
      absorption: rgb(0.080, 0.028, 0.022),
    },
    hemisphere: { skyColor: 0x9fe0f2, groundColor: 0x8fa294, intensity: 1.55 },
    fill: { color: 0xc6ecff, intensity: 0.75 },
    water: {
      horizon: 0x078cba,
      zenith: 0x45cfdf,
      abyss: 0x075779,
      glow: 0x9beaf2,
      floorAverage: 0xbde7e8,
    },
    // These are the exact constants previously hard-coded in water-surface.js.
    surface: {
      horizon: rgb(0.50, 0.76, 1.06),
      zenith: rgb(0.18, 0.43, 0.85),
      glowColor: rgb(0.95, 0.98, 1.0),
      glowWide: 0.28,
      glowTight: 1.1,
      discColor: rgb(1.0, 0.98, 0.93),
      discIntensity: 18.0,
    },
    shaftIntensityMultiplier: 1.0,
    causticsIntensityMultiplier: 1.0,
  }),
  night: profile({
    // Keep the water dark, but leave a readable blue-white moon key and scattered front fill
    // so a Resident looks moonlit rather than globally exposure-darkened.
    sun: {
      lightColor: 0xd9e8ff,
      lightIntensity: 1.55,
      radiance: rgb(1.08, 1.38, 1.92),
      absorption: rgb(0.108, 0.043, 0.027),
    },
    hemisphere: { skyColor: 0x274c76, groundColor: 0x17243a, intensity: 0.72 },
    fill: { color: 0x9bc8ff, intensity: 1.0 },
    water: {
      horizon: 0x05233f,
      zenith: 0x0b3159,
      abyss: 0x020b1b,
      glow: 0x4e8fc7,
      floorAverage: 0x34566b,
    },
    surface: {
      horizon: rgb(0.035, 0.075, 0.15),
      zenith: rgb(0.008, 0.018, 0.060),
      glowColor: rgb(0.55, 0.72, 1.0),
      glowWide: 0.07,
      glowTight: 0.55,
      discColor: rgb(0.80, 0.90, 1.0),
      discIntensity: 6.0,
    },
    shaftIntensityMultiplier: 1.22,
    causticsIntensityMultiplier: 0.42,
  }),
});

export function normalizeTimeOfDay(value) {
  return TIME_OF_DAY_VALUES.includes(value) ? value : DEFAULT_TIME_OF_DAY;
}

export function normalizeEnvironmentHour(value) {
  const number = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(number)) return DEFAULT_ENVIRONMENT_HOUR;
  return ((number % 24) + 24) % 24;
}

// 日本時間（夏時間がないので、いつもUTC+9）の時刻。PCの時間帯の設定によらない。
export function environmentHourFromDate(date = new Date()) {
  return normalizeEnvironmentHour(date.getTime() / 3_600_000 + 9);
}

const smoothstep = value => value * value * (3 - 2 * value);
const transition = (hour, start, end, from, to) => ({
  from,
  to,
  mix: smoothstep((hour - start) / (end - start)),
});

// Returns the two visual keyframes and a smooth interpolation amount for a 24-hour clock.
// Night is stable overnight, morning grows from 05:00-07:00, day settles by 11:00,
// and day cools directly into night from 18:00-21:00 without an evening/orange phase.
export function environmentBlendAtHour(value) {
  const hour = normalizeEnvironmentHour(value);
  if (hour < 5) return { hour, from: 'night', to: 'night', mix: 0 };
  if (hour < 7) return { hour, ...transition(hour, 5, 7, 'night', 'morning') };
  if (hour < 11) return { hour, ...transition(hour, 7, 11, 'morning', 'day') };
  if (hour < 18) return { hour, from: 'day', to: 'day', mix: 0 };
  if (hour < 21) return { hour, ...transition(hour, 18, 21, 'day', 'night') };
  return { hour, from: 'night', to: 'night', mix: 0 };
}
