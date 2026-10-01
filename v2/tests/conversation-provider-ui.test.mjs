import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const rendererSource = readFileSync(new URL('../src/renderer/app.js', import.meta.url), 'utf8');
const providerFunctions = rendererSource.slice(rendererSource.indexOf('function conversationProviderOptions('), rendererSource.indexOf('function renderEdgeStats('));

function renderer(providers, extra = {}) {
  const context = vm.createContext({
    snapshot: { conversation_providers: providers },
    dashboardConnected: true, commandBusy: false,
    refreshingConversationProviders: new Set(),
    escapeHtml: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
    renderResidentSettings() {},
    ...extra,
  });
  vm.runInContext(providerFunctions, context);
  return { context, run: code => vm.runInContext(code, context) };
}

test('Resident connection and Model choices follow provider metadata and retain a saved choice', () => {
  const ui = renderer([
    { id: 'holo', display_name: 'Holo' },
    { id: 'other-ai', display_name: '別のAI', models: [{ id: 'model-a', display_name: 'Model A' }], availability: { state: 'ready' } },
  ]);
  assert.match(ui.run("conversationProviderOptions('other-ai')"), /value="other-ai" selected>別のAI/);
  assert.doesNotMatch(ui.run("conversationProviderOptions('other-ai')"), /value="holo"/);
  assert.match(ui.run("conversationModelOptions('other-ai', '')"), /value="" selected>接続先の既定/);
  assert.match(ui.run("conversationModelOptions('other-ai', 'model-a')"), /value="model-a" selected>Model A/);
  ui.context.snapshot.conversation_providers[1].models = [];
  assert.match(ui.run("conversationModelOptions('other-ai', 'model-a')"), /value="model-a" selected>model-a（保存済み）/);
  ui.context.snapshot.conversation_providers = [];
  assert.match(ui.run("conversationProviderOptions('other-ai')"), /value="other-ai" selected/);
  assert.match(ui.run("conversationModelOptions('other-ai', 'model-a')"), /value="model-a" selected/);
});

test('Resident chips show a ready or busy connection without requiring a provider reason', () => {
  const ui = renderer([], {
    residents: [], selectedResidentId: 'resident-a', expandedTaskId: null, restoreChatFocus: false,
    sortedTasks: () => [], renderAll() {},
  });
  const applySnapshotSource = rendererSource.slice(rendererSource.indexOf('function applySnapshot('), rendererSource.indexOf('function isTaskVisible('));
  vm.runInContext(applySnapshotSource, ui.context);
  for (const [state, label, online] of [['ready', '接続済み', true], ['busy', '応答中', true], ['unavailable', '未接続', false]]) {
    ui.context.incoming = {
      residents: [{ id: 'resident-a', capability_id: 'other-ai' }],
      conversation_providers: [{ id: 'other-ai', availability: { state } }],
    };
    ui.run('applySnapshot(incoming)');
    assert.equal(ui.context.residents[0].connectionLabel, label);
    assert.equal(ui.context.residents[0].online, online);
  }
});

test('connection confirmation runs once, refreshes metadata and releases controls after failure', async () => {
  let resolveRefresh;
  let refreshes = 0;
  const notices = [];
  const ui = renderer([{ id: 'other-ai', availability: { state: 'unavailable' } }], {
    bridge: {
      refreshConversationProvider: async () => { refreshes++; await new Promise(resolve => { resolveRefresh = resolve; }); },
      snapshot: async () => ({ conversation_providers: [{ id: 'other-ai', models: [{ id: 'model-a' }], availability: { state: 'ready' } }] }),
    },
    showNotice: (text, error) => notices.push({ text, error }),
  });
  ui.context.applySnapshot = snapshot => { ui.context.snapshot = snapshot; };
  const pending = ui.run("refreshConversationProvider('other-ai')");
  assert.equal(ui.run("providerRefreshDisabled('other-ai')"), true);
  await ui.run("refreshConversationProvider('other-ai')");
  assert.equal(refreshes, 1);
  resolveRefresh();
  await pending;
  assert.equal(ui.run("providerRefreshDisabled('other-ai')"), false);
  assert.match(ui.run("conversationModelOptions('other-ai', '')"), /model-a/);
  ui.context.bridge.refreshConversationProvider = async () => { throw new Error('接続できません'); };
  await ui.run("refreshConversationProvider('other-ai')");
  assert.equal(ui.run("providerRefreshDisabled('other-ai')"), false);
  assert.equal(notices.at(-1).error, true);
});

test('connection IPC only accepts a trusted Renderer and a bounded provider identifier', async () => {
  const source = readFileSync(new URL('../src/main/provider-ipc.mjs', import.meta.url), 'utf8');
  let handler;
  let calls = 0;
  let available = true;
  const context = vm.createContext({ ipcMain: { handle: (_name, callback) => { handler = callback; } } });
  vm.runInContext(source.replace("import { ipcMain } from 'electron/main';", '').replace('export function', 'function'), context);
  context.options = {
    isTrustedRenderer: event => event.trusted,
    isAvailable: () => available,
    request: async (type, input) => { calls++; return { type, provider: input.provider_id }; },
  };
  vm.runInContext('installConversationProviderIpc(options)', context);
  await assert.rejects(handler({ trusted: false }, 'other-ai'), /untrusted renderer/);
  for (const invalid of [null, '', 'x'.repeat(129), 'bad\0id', {}]) {
    await assert.rejects(handler({ trusted: true }, invalid), /接続先/);
  }
  assert.equal(calls, 0);
  assert.equal((await handler({ trusted: true }, 'other-ai')).provider, 'other-ai');
  assert.equal(calls, 1);
  available = false;
  await assert.rejects(handler({ trusted: true }, 'other-ai'), /Hubに接続/);
  assert.equal(calls, 1);
});
