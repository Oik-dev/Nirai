// Local renderer preferences only; these controls never change Hub/Task settings.
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
export const SEA_DEFAULTS = Object.freeze(Object.fromEntries(SEA_CONTROLS.map(c => [c.key, c.initial])));
const STORAGE_KEY = 'nirai.world.sea-debug.v1';
export function normalizeSeaSettings(value) {
  return Object.fromEntries(SEA_CONTROLS.map(c => {
    const n = value?.[c.key];
    return [c.key, typeof n === 'number' && Number.isFinite(n)
      ? Math.min(c.max, Math.max(c.min, Math.round(n / c.step) * c.step)) : c.initial];
  }));
}
export function readSeaSettings() {
  try { return normalizeSeaSettings(JSON.parse(localStorage.getItem(STORAGE_KEY))); }
  catch { return { ...SEA_DEFAULTS }; }
}
export function installSeaDebug(panel, settings, apply, signal) {
  if (!panel) return;
  const rows = new Map();
  for (const control of SEA_CONTROLS) {
    const label = document.createElement('label');
    const text = document.createElement('span'); text.textContent = control.label;
    const output = document.createElement('output');
    const input = document.createElement('input');
    Object.assign(input, { type: 'range', id: `sea-${control.key}`, min: control.min, max: control.max, step: control.step });
    label.htmlFor = input.id; output.htmlFor = input.id;
    label.append(text, output, input); panel.querySelector('.sea-sliders').append(label);
    rows.set(control.key, { input, output, control });
    input.addEventListener('input', () => {
      settings = normalizeSeaSettings({ ...settings, [control.key]: input.valueAsNumber });
      update();
    }, { signal });
  }
  const update = () => {
    for (const [key, { input, output, control }] of rows) {
      input.value = settings[key]; output.value = control.format(settings[key]);
      input.setAttribute('aria-valuetext', output.value);
    }
    apply(settings);
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)); } catch { /* Session-only if storage is unavailable. */ }
  };
  panel.querySelector('button').addEventListener('click', () => { settings = { ...SEA_DEFAULTS }; update(); }, { signal });
  update();
}
