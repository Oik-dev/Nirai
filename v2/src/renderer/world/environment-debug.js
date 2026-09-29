import {
  TIME_OF_DAY_HOURS,
  TIME_OF_DAY_VALUES,
  environmentHourFromDate,
  normalizeEnvironmentHour,
} from './environment-profiles.js';

const LABELS = Object.freeze({ morning: '朝', day: '昼', night: '夜' });
const STEP = 0.25;

const close = (a, b) => Math.abs(a - b) < STEP / 2;

function formatHour(hour) {
  const minutes = Math.round(normalizeEnvironmentHour(hour) * 60);
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

export function installEnvironmentDebug(panel, environmentHour, apply, signal) {
  let current = normalizeEnvironmentHour(environmentHour);
  let live = true;
  if (!panel) {
    return {
      syncLive(hour) {
        if (!live) return false;
        current = normalizeEnvironmentHour(hour);
        apply(current, true);
        return true;
      },
    };
  }

  const control = document.createElement('div');
  control.className = 'environment-time-control';

  const header = document.createElement('div');
  header.className = 'environment-time-header';
  const label = document.createElement('span');
  label.textContent = '時間';
  const output = document.createElement('output');
  const liveButton = document.createElement('button');
  liveButton.type = 'button';
  liveButton.id = 'environment-time-live';
  liveButton.textContent = '現在時刻';
  header.append(label, output, liveButton);

  const buttons = document.createElement('div');
  buttons.className = 'environment-time-buttons';
  buttons.setAttribute('role', 'group');
  buttons.setAttribute('aria-label', '時間帯の基準点');

  const entries = TIME_OF_DAY_VALUES.map(key => {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.timeOfDay = key;
    button.dataset.environmentHour = String(TIME_OF_DAY_HOURS[key]);
    button.textContent = LABELS[key];
    button.addEventListener('click', () => {
      live = false;
      current = TIME_OF_DAY_HOURS[key];
      update();
    }, { signal });
    buttons.append(button);
    return [key, button];
  });

  const slider = document.createElement('input');
  Object.assign(slider, {
    type: 'range',
    min: 0,
    max: 23.75,
    step: STEP,
    value: current,
    id: 'environment-hour',
  });
  slider.setAttribute('aria-label', '環境時刻');
  slider.addEventListener('input', () => {
    live = false;
    current = normalizeEnvironmentHour(slider.valueAsNumber);
    update();
  }, { signal });

  liveButton.addEventListener('click', () => {
    live = true;
    current = environmentHourFromDate();
    update();
  }, { signal });

  control.append(header, buttons, slider);
  panel.querySelector('.sea-debug-body')?.prepend(control);

  const update = () => {
    slider.value = String(current);
    output.value = formatHour(current);
    output.textContent = output.value;
    liveButton.classList.toggle('is-selected', live);
    liveButton.setAttribute('aria-pressed', String(live));
    for (const [key, button] of entries) {
      const selected = !live && close(current, TIME_OF_DAY_HOURS[key]);
      button.classList.toggle('is-selected', selected);
      button.setAttribute('aria-pressed', String(selected));
    }
    apply(current, live);
  };
  update();

  return {
    syncLive(hour) {
      if (!live) return false;
      current = normalizeEnvironmentHour(hour);
      update();
      return true;
    },
  };
}
