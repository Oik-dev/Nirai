import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/renderer/app.js', import.meta.url), 'utf8');

function section(start, end) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first, 'production renderer section must exist');
  return source.slice(first, last);
}

function renderer() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) {
      const classes = new Set(id === 'dashboard' ? ['is-open'] : []);
      const attributes = new Map();
      elements.set(id, {
        hidden: ['residentSettingsPanel', 'residentDeleteConfirm', 'holoPresentation'].includes(id),
        dataset: {}, textContent: '', focus() {},
        classList: {
          contains: value => classes.has(value),
          remove: value => classes.delete(value),
          toggle: (value, force) => force ? classes.add(value) : classes.delete(value),
        },
        getClientRects() { return this.hidden ? [] : [{}]; },
        getAttribute: name => attributes.get(name) ?? null,
        setAttribute: (name, value) => attributes.set(name, value),
        removeAttribute: name => attributes.delete(name),
      });
    }
    return elements.get(id);
  };
  let selected = { id: 'selected-task', resident_id: 'holo' };
  const context = vm.createContext({
    dashboardConnected: true,
    snapshot: { holo: { state: 'ready' } },
    bridge: null,
    $: element,
    getSelectedTask: () => selected,
    requestAnimationFrame: callback => callback(),
    showNotice: () => assert.fail('unexpected renderer error notice'),
  });
  // Execute the actual state and presentation functions. The small DOM facade
  // keeps these tests independent from Electron windows and browser rendering.
  vm.runInContext(section('let holoSurfaceSignature', 'let taskListOpen'), context);
  vm.runInContext(section('function canPresentHolo(', 'function renderChat('), context);
  vm.runInContext(section('function scheduleHoloSurfaceSync(', 'function renderAll('), context);
  vm.runInContext(section('function setDashboardOpen(', 'function finishResidentStripDrag('), context);
  return {
    context, element,
    run: code => vm.runInContext(code, context),
    select: id => { selected = { id, resident_id: 'holo' }; },
    present: value => {
      context.incoming = { task_id: selected.id, visible: false, phase: 'loading', ...value };
      vm.runInContext('applyHoloPresentation(incoming)', context);
    },
  };
}

test('Holo presentation accepts only the selected Task and cannot revive an old Task state', () => {
  const ui = renderer();
  ui.present();
  assert.equal(ui.element('holoPresentation').dataset.taskId, 'selected-task');
  ui.present({ task_id: 'another-task', visible: true });
  assert.equal(ui.element('holoPresentation').hidden, false);
  assert.equal(ui.element('holoPresentation').dataset.taskId, 'selected-task');

  ui.select('another-task');
  ui.run('clearHoloPresentation()');
  ui.present({ task_id: 'selected-task' });
  assert.equal(ui.run('holoPresentation'), null);
  assert.equal(ui.element('holoPresentation').hidden, true);
});

test('native Holo remains visible during preparation and load failures stay visible', () => {
  const ui = renderer();
  ui.present();
  assert.equal(ui.element('holoPresentation').hidden, false);
  ui.present({ visible: true, phase: 'preparing' });
  assert.equal(ui.element('holoPresentation').hidden, true, 'preparation must not cover the native conversation');
  assert.equal(ui.element('holoSurfaceStatus').hidden, false);
  assert.equal(ui.element('holoSurfaceStatus').textContent, '送信中…');
  ui.present({ visible: true });
  assert.equal(ui.element('holoPresentation').hidden, true);
  assert.equal(ui.run('holoPresentation.phase'), null);
  assert.equal(ui.element('holoSurfaceStatus').hidden, true);

  ui.context.snapshot.holo = { state: 'unavailable', reason: 'ChatGPTを読み込めません' };
  ui.present();
  assert.equal(ui.element('holoPresentation').hidden, true, 'loading must not conceal an observed failure');
  assert.equal(ui.element('holoSurfaceStatus').hidden, false);
  assert.equal(ui.element('holoSurfaceStatus').textContent, 'ChatGPTを読み込めません');
});

test('folding the Dashboard or losing the Hub discards Holo presentation state', () => {
  const ui = renderer();
  ui.present();
  ui.run('setDashboardOpen(false)');
  assert.equal(ui.run('holoPresentation'), null);
  assert.equal(ui.element('holoPresentation').hidden, true);
  ui.present();
  assert.equal(ui.run('holoPresentation'), null, 'a late event while folded is ignored');

  ui.run('setDashboardOpen(true)');
  ui.present();
  ui.context.dashboardConnected = false;
  ui.run('renderHoloPresentation()');
  assert.equal(ui.run('holoPresentation'), null);
  assert.equal(ui.element('holoPresentation').hidden, true);
  ui.present();
  assert.equal(ui.run('holoPresentation'), null, 'a disconnected Hub cannot restore the presentation');
});

test('new Holo presentation events and cleared state survive older IPC responses', async () => {
  const ui = renderer();
  let respond;
  let calls = 0;
  ui.context.bridge = {
    holoSurface: () => {
      calls += 1;
      return new Promise(resolve => { respond = resolve; });
    },
  };
  let spec = { payload: { visible: true, task_id: 'selected-task' }, signature: 'first' };
  ui.context.holoSurfaceSpec = () => spec;
  ui.run('scheduleHoloSurfaceSync()');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 1);
  ui.present({ visible: true, phase: 'preparing' });
  respond({ presentation: { task_id: 'selected-task', visible: false, phase: 'loading' } });
  await ui.run('holoSurfaceSerial');
  assert.equal(ui.run('holoPresentation.phase'), 'preparing');
  assert.equal(ui.element('holoPresentation').hidden, true);
  assert.equal(ui.element('holoSurfaceStatus').textContent, '送信中…');

  spec = { ...spec, signature: 'second' };
  ui.run('scheduleHoloSurfaceSync()');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 2);
  const oldResponse = respond;
  ui.run('setDashboardOpen(false); setDashboardOpen(true)');
  oldResponse({ presentation: { task_id: 'selected-task', visible: false, phase: 'loading' } });
  await ui.run('holoSurfaceSerial');
  assert.equal(ui.run('holoPresentation'), null, 'cleared state stays discarded after reopening');
  assert.equal(ui.element('holoPresentation').hidden, true);
});

test('Task changes and hiding bypass a pending Holo surface response', async () => {
  for (const changeTask of [true, false]) {
    const ui = renderer();
    let respond;
    let calls = 0;
    let hides = 0;
    ui.context.bridge = {
      holoHide: () => { hides += 1; },
      holoSurface: payload => {
        calls += 1;
        if (calls === 1) return new Promise(resolve => { respond = resolve; });
        return Promise.resolve({ presentation: { task_id: payload.task_id, visible: false, phase: null } });
      },
    };
    let spec = { payload: { visible: true, task_id: 'selected-task' }, signature: 'initial' };
    ui.context.holoSurfaceSpec = () => spec;
    ui.run('scheduleHoloSurfaceSync()');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 1);
    const initialHides = hides;

    if (changeTask) {
      ui.select('next-task');
      ui.run('clearHoloPresentation()');
      spec = { payload: { visible: true, task_id: 'next-task' }, signature: 'next' };
      ui.run('scheduleHoloSurfaceSync()');
    } else {
      spec = { payload: { visible: false, task_id: 'selected-task' }, signature: 'folded' };
      ui.run('setDashboardOpen(false)');
    }
    assert.equal(hides, initialHides + 1, 'native hide is sent before the pending surface call resolves');
    assert.equal(calls, 1, 'conversation selection still waits in the serial queue');
    assert.equal(ui.run('holoPresentation'), null);

    respond({ presentation: { task_id: 'selected-task', visible: true, phase: 'preparing' } });
    await ui.run('holoSurfaceSerial');
    assert.equal(calls, 2);
    assert.equal(ui.element('holoPresentation').hidden, true);
    assert.notEqual(ui.run('holoPresentation?.phase'), 'preparing', 'the older response cannot revive preparation');
    assert.equal(ui.run('holoSurfaceVisible'), false);
  }
});
