// Renderer-local sea settings. The underwater world owns these values;
// the debug panel is only one UI for editing them.
export const SEA_CONTROLS = [
  { key: 'waveSpeed', label: '波の動作速度', min: 0, max: 2, step: 0.05, initial: 0.5, format: v => `${v.toFixed(2)} 倍` },
  { key: 'waveSize', label: '波の大きさ', min: 0, max: 250, step: 5, initial: 30, format: v => `${v}%` },
  { key: 'waveDetail', label: '波の細かさ', min: 0, max: 200, step: 5, initial: 35, format: v => `${v}%` },
  { key: 'surfaceClarity', label: '海面の透明感', min: 0, max: 100, step: 1, initial: 0, format: v => `${v}%` },
  { key: 'shaftStrength', label: '光柱の強さ', min: 0, max: 300, step: 5, initial: 150, format: v => `${v}%` },
  { key: 'shaftRange', label: '光柱の距離', min: 0, max: 60, step: 1, initial: 45, format: v => `${v} m` },
  { key: 'shaftDepth', label: '光柱の深さ', min: 0, max: 30, step: 0.5, initial: 8, format: v => `${v.toFixed(1)} m` },
  { key: 'causticTransparency', label: 'コースティクスの透明度', min: 0, max: 100, step: 1, initial: 80, format: v => `${v}%` },
  { key: 'blue', label: '海の青さ', min: 0, max: 100, step: 1, initial: 80, format: v => `${v}%` },
  { key: 'visibility', label: '海の透明度', min: 10, max: 120, step: 1, initial: 55, format: v => `見通し ${v} m` },
  { key: 'particles', label: 'パーティクルの数', min: 0, max: 4000, step: 1, initial: 4000, format: v => `${v}` },
  { key: 'bubbles', label: '泡の数', min: 0, max: 750, step: 1, initial: 250, format: v => `${v}` },
];

export const SEA_DEFAULTS = Object.freeze(
  Object.fromEntries(SEA_CONTROLS.map(control => [control.key, control.initial])),
);

export function seaControl(key) {
  const control = SEA_CONTROLS.find(item => item.key === key);
  if (!control) throw new Error(`Unknown sea setting: ${key}`);
  return control;
}

export function normalizeSeaSettings(value) {
  return Object.fromEntries(SEA_CONTROLS.map(control => {
    const number = value?.[control.key];
    const normalized = typeof number === 'number' && Number.isFinite(number)
      ? Math.min(control.max, Math.max(control.min, Math.round(number / control.step) * control.step))
      : control.initial;
    return [control.key, normalized];
  }));
}
