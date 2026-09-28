import { SEA_CONTROLS, SEA_DEFAULTS, normalizeSeaSettings } from './sea-settings.js';

// Local renderer preferences only; these controls never change Hub/Task settings.
const STORAGE_KEY = 'nirai.world.sea-debug.v1';
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
