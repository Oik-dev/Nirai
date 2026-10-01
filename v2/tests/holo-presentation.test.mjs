import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// Execute HoloView with only the Electron native boundary replaced.
// These tests neither show nor focus a window.
const moduleUrl = new URL('../src/main/holo-view.mjs', import.meta.url)
const source = readFileSync(moduleUrl, 'utf8').replace(/^import .*\n/gm, '')
  .replaceAll('import.meta.url', JSON.stringify(moduleUrl.href))
  .replace('export class HoloView', 'class HoloView')
const loadView = new Function('WebContentsView', 'readFileSync', 'holoPageOperation', 'Date', 'setTimeout', `${source}\nreturn HoloView`)

function fixture({ fast = false } = {}) {
  let clock = 0
  const events = [], loads = [], requests = []
  const wc = Object.assign(new EventEmitter(), {
    url: 'https://chatgpt.com/c/one', getURL() { return this.url },
    isDestroyed: () => false, setWindowOpenHandler() {},
    executeJavaScript: async () => ({ cleared: false, stopped: true, presenting: true }),
    async loadURL(url) { loads.push(url); this.url = url }, stop() {}, focus() {},
  })
  wc.session = Object.assign(new EventEmitter(), { setPermissionRequestHandler() {}, setPermissionCheckHandler() {} })
  class NativeView { webContents = wc; setBackgroundColor() {}; setBounds() {} }
  const host = { isDestroyed: () => false, getContentSize: () => [1000, 800],
    webContents: { send(channel, payload) { events.push({ channel, ...payload }) } },
    contentView: { addChildView() {}, removeChildView() {} } }
  const timeout = fast ? (callback, ms) => setTimeout(() => { clock += ms; callback() }, 0) : setTimeout
  const HoloView = loadView(NativeView, readFileSync, () => {}, fast ? { now: () => clock } : Date, timeout)
  const view = new HoloView(async (type, value) => { requests.push({ type, ...value }); return {} }, host)
  clearInterval(view.poll)
  view.surface = { visible: true, bounds: { x: 0, y: 0, width: 900, height: 700 }, task_id: 'task-one', external_conversation_id: 'one' }
  view.displayReady = true
  view.attached = true
  view.refresh = async () => {}
  const active = view.active = { dispatch: { turn_id: 'turn-one', task_id: 'task-one' }, conversation_id: 'one', aborted: false }
  const observation = extra => ({ received: true, busy: false, waiting: true, draft: false, text: '', url: wc.url, conversation_id: 'one', ...extra })
  return { view, wc, events, loads, requests, active, observation, clock: () => clock }
}

test('sending preparation keeps the native conversation visible and ends before generation', async () => {
  const f = fixture()
  await f.view.setPresentationPhase('preparing')
  assert.equal(f.view.attached, true)
  assert.equal(f.events.at(-1).phase, 'preparing')
  await f.view.endPresentation('preparing')
  assert.equal(f.view.attached, true)
  assert.equal(f.events.at(-1).phase, null)
  assert.deepEqual(f.loads, [])
})

test('late presentation calls cannot revive preparation after cancellation, hiding or disconnect', async () => {
  for (const stop of [f => f.view.endPresentation('preparing'), f => f.view.setSurface({ ...f.view.surface, visible: false }), f => f.view.disconnected()]) {
    const f = fixture()
    let finish
    f.view.page = ({ preparing }) => preparing ? new Promise(resolve => { finish = resolve }) : Promise.resolve({})
    const pending = f.view.setPresentationPhase('preparing')
    await stop(f)
    const last = f.events.length
    finish({ presenting: true })
    await pending
    assert.equal(f.view.presentation, null)
    assert.equal(f.events.length, last)
  }
})

test('Task switching removes the previous surface before opening the new conversation', async () => {
  const f = fixture()
  await f.view.setPresentationPhase('preparing')
  await f.view.setSurface({ ...f.view.surface, task_id: 'task-two', external_conversation_id: 'two', external_url: 'https://chatgpt.com/c/two' })
  assert.equal(f.view.attached, false)
  assert.equal(f.view.presentation, null)
  f.view.release(f.active.dispatch.turn_id)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.view.surface.task_id, 'task-two')
  assert.deepEqual(f.loads, ['https://chatgpt.com/c/two'])
})

test('idle Task switching hides the old conversation before a slow Provider page responds', async () => {
  const f = fixture()
  f.view.active = null
  let finish
  const response = new Promise(resolve => { finish = resolve })
  f.view.page = async ({ operation }) => {
    if (operation === 'presentation') await response
    return {}
  }
  const switching = f.view.setSurface({ ...f.view.surface, task_id: 'task-two', external_conversation_id: 'two', external_url: 'https://chatgpt.com/c/two' })
  await new Promise(resolve => setImmediate(resolve))
  try {
    assert.equal(f.view.surface.task_id, 'task-two')
    assert.equal(f.view.attached, false, 'the previous Task must not remain visible during the page response wait')
    assert.equal(f.events.at(-1).task_id, 'task-two')
    assert.equal(f.events.at(-1).visible, false)
    assert.equal(f.events.at(-1).phase, 'loading')
    assert.deepEqual(f.loads, [], 'navigation has not begun while the Provider page is stalled')
  } finally {
    finish()
    await switching
  }
  assert.deepEqual(f.loads, ['https://chatgpt.com/c/two'])
})

test('failed navigation clears the loading notice so the error can be displayed', async () => {
  const f = fixture()
  f.wc.loadURL = async () => { f.view.displayReady = false; throw new Error('offline') }
  await assert.rejects(f.view.loadUrl(f.wc.url), /offline/)
  assert.equal(f.view.presentationStatus().phase, null)
  assert.equal(f.view.loadFailure, 'offline')
})

test('immediate hiding prevents a pending Task load or Turn release from exposing old content', async () => {
  const f = fixture()
  await f.view.setSurface({ ...f.view.surface, task_id: 'task-two', external_conversation_id: 'two', external_url: 'https://chatgpt.com/c/two' })
  await f.view.hideSurface()
  assert.equal(f.view.pendingSurface.visible, false)
  assert.equal(f.view.active, f.active, 'visibility must not revoke the active Turn')
  assert.deepEqual(f.requests, [], 'hiding must not mutate Hub authority')
  f.view.release(f.active.dispatch.turn_id)
  await new Promise(resolve => setImmediate(resolve))
  await f.view.displayReadySurface()
  assert.equal(f.view.attached, false, 'an older navigation completion cannot reopen the hidden native surface')
  assert.equal(f.view.surface.visible, false)
})

test('trusted hide invalidates a stale visible IPC before snapshot or view initialization completes', async () => {
  const main = readFileSync(new URL('../src/main/index.mjs', import.meta.url), 'utf8')
  const start = main.indexOf('  ipcMain.handle("nirai:holo-hide"')
  const end = main.indexOf('\n}\n\nfunction createWindow()', start)
  assert.ok(start >= 0 && end > start)
  const install = new Function('ipcMain', 'isTrustedRenderer', 'request', 'ensureHoloView', 'holoView', 'hubReady',
    `let holoSurfaceGeneration = 0;\n${main.slice(start, end)}`)
  const snapshot = { tasks: [{ id: 'task-one', resident_id: 'holo', state: 'Running' }], provider_bindings: [] }
  for (const waitOn of ['snapshot', 'view']) {
    const handlers = new Map(), shown = []
    let hideCount = 0, finish
    const gate = new Promise(resolve => { finish = resolve })
    const view = { hideSurface: async () => { hideCount++ }, setSurface: async surface => { shown.push(surface); return {} } }
    install({ handle: (name, handler) => handlers.set(name, handler) }, event => event.trusted === true,
      async () => waitOn === 'snapshot' ? gate : snapshot,
      async () => waitOn === 'view' ? gate : view, view, true)
    const hide = handlers.get('nirai:holo-hide')
    const show = handlers.get('nirai:holo-surface')
    await assert.rejects(hide({ trusted: false }), /untrusted renderer/)
    assert.equal(hideCount, 0)
    const pending = show({ trusted: true }, { visible: true, task_id: 'task-one', bounds: { x: 0, y: 0, width: 900, height: 700 } })
    await new Promise(resolve => setImmediate(resolve))
    await hide({ trusted: true })
    finish(waitOn === 'snapshot' ? snapshot : view)
    assert.equal((await pending).visible, false)
    assert.equal(hideCount, 1)
    assert.deepEqual(shown, [], 'old visible IPC must not undo immediate hiding')
    await show({ trusted: true }, { visible: true, task_id: 'task-one', bounds: { x: 0, y: 0, width: 900, height: 700 } })
    assert.equal(shown.length, 1, 'a newer selection can still display its validated Task')
  }
})

test('generation resuming during final observation does not reload or finalize partial text', async () => {
  const f = fixture({ fast: true })
  f.view.page = async () => f.observation({ busy: true, waiting: true, text: 'working' })
  assert.equal((await f.view.stableTurn(f.active, f.observation(), true)).busy, true)
  assert.deepEqual(f.loads, [])
  assert.deepEqual(f.requests, [])
})

test('a proven Provider Turn survives same-document redraw and final text stays stable without reload', async () => {
  const f = fixture({ fast: true })
  let reads = 0
  f.view.page = async request => {
    if (request.operation !== 'turn') return {}
    assert.equal(request.conversation_id, 'one')
    reads++
    if (reads === 1) {
      assert.equal(request.provider_turn_key, null)
      return f.observation({ busy: true, waiting: false, provider_turn_key: 'fallback-turn-0' })
    }
    assert.equal(request.provider_turn_key, 'fallback-turn-0', 'the proven wrapper follows the disappeared user bubble')
    return f.observation({ provider_turn_key: 'fallback-turn-0', text: 'complete answer' })
  }
  const first = await f.view.observeTurn(f.active)
  f.wc.emit('did-start-navigation', { isMainFrame: true, isSameDocument: true, url: f.wc.url })
  const next = await f.view.observeTurn(f.active)
  assert.equal((await f.view.stableTurn(f.active, next, true)).text, 'complete answer')
  assert.equal(first.busy, true)
  assert.deepEqual(f.loads, [])
})

test('full navigation invalidates a Provider Turn even at the same URL; a new marker may bind again', async () => {
  const f = fixture()
  const requests = []
  f.view.page = async request => {
    if (request.operation !== 'turn') return {}
    requests.push(request)
    return f.observation({ provider_turn_key: 'fallback-turn-0' })
  }
  await f.view.observeTurn(f.active)
  f.wc.emit('did-start-navigation', { isMainFrame: false, isSameDocument: false })
  await f.view.observeTurn(f.active)
  assert.equal(requests.at(-1).provider_turn_key, 'fallback-turn-0', 'subframes do not invalidate the main document')
  f.wc.emit('did-start-navigation', { isMainFrame: true, isSameDocument: false })
  await f.view.observeTurn(f.active)
  assert.equal(requests.at(-1).provider_turn_key, null, 'the new document must find the actual user marker')
  await f.view.observeTurn(f.active)
  assert.equal(requests.at(-1).provider_turn_key, 'fallback-turn-0', 'a new marker observation may prove a reused key')
})

test('late Provider observations cannot carry proof across a navigation or a released Turn', async () => {
  for (const invalidate of [f => f.wc.emit('did-start-navigation', { isMainFrame: true, isSameDocument: false }),
    f => f.view.release(f.active.dispatch.turn_id), f => { f.active.aborted = true }]) {
    const f = fixture()
    let finish
    f.view.page = request => request.operation === 'turn' ? new Promise(resolve => { finish = resolve }) : Promise.resolve({})
    const pending = f.view.observeTurn(f.active)
    invalidate(f)
    finish(f.observation({ provider_turn_key: 'fallback-turn-0', text: 'stale answer' }))
    assert.equal(await pending, null)
    assert.ok(!f.active.provider_turn_key)
  }
})

test('leaving and returning to a Conversation cannot reuse its old Provider Turn key', async () => {
  const f = fixture()
  const requests = []
  f.view.page = async request => {
    if (request.operation !== 'turn') return {}
    requests.push(request)
    return f.observation({ provider_turn_key: 'fallback-turn-0' })
  }
  await f.view.observeTurn(f.active)
  f.wc.emit('did-start-navigation', { isMainFrame: true, isSameDocument: true, url: 'https://chatgpt.com/c/other' })
  f.wc.emit('did-start-navigation', { isMainFrame: true, isSameDocument: true, url: f.wc.url })
  await f.view.observeTurn(f.active)
  assert.equal(requests.at(-1).provider_turn_key, null, 'returning must prove the actual marker again')
  f.view.page = async () => f.observation({ received: false, conversation_id: 'other' })
  assert.equal(await f.view.observeTurn(f.active), null)
  assert.equal(f.active.provider_turn_key, null, 'an observed different conversation also invalidates proof')
})

test('a lost user bubble and replaced Provider key reload only proven history, then require the real marker', async () => {
  const f = fixture({ fast: true })
  let restored = false
  const requests = []
  f.view.page = async request => {
    if (request.operation !== 'turn') return {}
    requests.push(request)
    if (restored) {
      assert.equal(request.provider_turn_key, null, 'a reused key cannot establish proof after reload')
      return f.observation({ text: 'persisted final answer' })
    }
    return f.observation({ received: false })
  }
  f.active.provider_turn_key = 'real-key-before-redraw'
  f.wc.loadURL = async url => {
    f.loads.push(url)
    f.wc.emit('did-start-navigation', { isMainFrame: true, isSameDocument: false, url })
    restored = true
  }
  assert.equal((await f.view.stableTurn(f.active, f.observation({ received: false }), true)).text, 'persisted final answer')
  assert.deepEqual(f.loads, [f.wc.url])
  assert.ok(requests.length >= 3)
})

test('missing marker without prior proof, generation or Master draft cannot trigger history recovery', async () => {
  for (const extra of [{}, { busy: true }, { draft: true }]) {
    const f = fixture({ fast: true })
    if (extra.busy || extra.draft) f.active.provider_turn_key = 'proven-key'
    const observation = f.observation({ received: false, ...extra })
    f.view.page = async () => observation
    assert.equal(await f.view.stableTurn(f.active, observation, true), null)
    assert.deepEqual(f.loads, [])
  }
})

test('final text still requires repeated identical observations', async () => {
  const f = fixture({ fast: true })
  const final = f.observation({ text: 'full answer' })
  let reads = 0
  f.view.page = async () => { reads += 1; return final }
  assert.equal((await f.view.stableTurn(f.active, final, true)).text, 'full answer')
  assert.equal(reads, 2)
  assert.deepEqual(f.loads, [])
})

test('final text arriving at the recovery boundary is confirmed without a reload or failure', async () => {
  const f = fixture({ fast: true })
  f.view.page = async () => f.observation({ text: f.clock() >= 5000 ? 'late final answer' : '' })
  assert.equal((await f.view.stableTurn(f.active, f.observation(), true)).text, 'late final answer')
  assert.deepEqual(f.loads, [])
})

test('missing text cannot reload over a Master draft or a different conversation', async () => {
  for (const change of [f => f.observation({ draft: true }), f => f.observation({ url: 'https://chatgpt.com/c/other', conversation_id: 'other' })]) {
    const f = fixture({ fast: true })
    f.view.page = async () => change(f)
    assert.equal(await f.view.stableTurn(f.active, f.observation(), true), null)
    assert.deepEqual(f.loads, [])
  }
})

test('recovery uses the latest matching conversation URL', async () => {
  const f = fixture({ fast: true })
  const initial = f.observation()
  f.wc.url += '?latest=1#answer'
  f.view.page = async () => f.observation({ text: f.loads.length ? 'saved answer' : '' })
  assert.equal((await f.view.stableTurn(f.active, initial, true)).text, 'saved answer')
  assert.deepEqual(f.loads, ['https://chatgpt.com/c/one?latest=1#answer'])
})

test('a draft or Cancel immediately before recovery prevents the load', async () => {
  for (const cancel of [false, true]) {
    const f = fixture({ fast: true })
    let changed = false
    const prepare = f.view.setPresentationPhase.bind(f.view)
    f.view.setPresentationPhase = async phase => { await prepare(phase); changed = true; if (cancel) f.active.aborted = true }
    f.view.page = async () => f.observation({ draft: changed && !cancel })
    if (cancel) await assert.rejects(f.view.stableTurn(f.active, f.observation(), true), /失効/)
    else assert.equal(await f.view.stableTurn(f.active, f.observation(), true), null)
    assert.deepEqual(f.loads, [])
  }
})

test('generation or final text appearing before recovery continues without reload', async () => {
  for (const busy of [true, false]) {
    const f = fixture({ fast: true })
    let changed = false
    const prepare = f.view.setPresentationPhase.bind(f.view)
    f.view.setPresentationPhase = async phase => { await prepare(phase); changed = true }
    f.view.page = async () => f.observation({ busy: changed && busy, text: changed ? 'answer' : '' })
    const result = await f.view.stableTurn(f.active, f.observation(), true)
    assert.equal(result.busy, busy)
    assert.equal(result.text, 'answer')
    assert.deepEqual(f.loads, [])
  }
})

test('input acceptance without a Turn has a bounded handoff notice and keeps the conversation visible', async () => {
  const f = fixture()
  f.view.active = null
  f.view.page = async () => ({ cleared: true })
  await f.view.handleNativeSend({ task_id: 'task-one', conversation_id: 'one', id: 'input-one', content: 'Master input' })
  assert.equal(f.view.nativeSend, null)
  assert.equal(f.view.attached, true)
  assert.equal(f.view.presentation?.phase, 'preparing')
  await new Promise(resolve => setTimeout(resolve, 550))
  assert.equal(f.view.presentation, null)
  assert.equal(f.requests.filter(request => request.type === 'holo-native-send').length, 1)
})

test('overlapping busy and waiting controls resume polling and save only the stable final reply', async () => {
  const f = fixture({ fast: true })
  f.view.active = null
  let reads = 0
  f.view.page = async ({ operation }) => {
    if (operation === 'observe') return { state: 'ready', conversation_id: 'one', ready_to_send: true }
    if (operation === 'fill' || operation === 'send') return { ok: true }
    if (operation === 'turn') {
      assert.equal(f.view.attached, true)
      reads += 1
      assert.ok(reads < 12, 'polling must progress rather than rechecking one observation forever')
      return f.observation(reads === 1 ? {} : reads === 2 ? { busy: true, text: 'working' } : { text: 'full answer' })
    }
    return {}
  }
  await f.view.dispatch({ task_id: 'task-one', turn_id: 'turn-one', target_conversation_id: 'one', prompt: '@Nirai\nturn_id=turn-one\nContinue', settings: { communication_attempts: 1, delivery_confirmation_ms: 1000 } })
  assert.deepEqual(f.requests.filter(request => request.type === 'holo-sync').map(request => [request.content, request.complete]), [['full answer', true]])
  assert.equal(f.view.presentation, null)
  assert.deepEqual(f.loads, [])
})

test('Say and Whisper use one normal Provider conversation with Message identity and no Task reports', async () => {
  const f = fixture({ fast: true })
  f.view.active = null
  f.view.surface = { ...f.view.surface, visible: false, mode: 'chat', task_id: null, capture: false }
  const sent = []
  f.view.page = async request => {
    if (request.operation === 'observe') return { state: 'ready', url: f.wc.url, conversation_id: 'one', ready_to_send: true }
    if (request.operation === 'fill' || request.operation === 'send') {
      assert.equal(request.turn_id, undefined)
      assert.match(request.prompt, new RegExp(`^message_id=${request.message_id}\\n`))
      assert.ok(!request.prompt.includes('@Nirai'))
      if (request.operation === 'send') sent.push(request.message_id)
      return { ok: true }
    }
    if (request.operation === 'turn') {
      assert.equal(request.turn_id, undefined)
      return f.observation({ text: `reply to ${request.message_id}` })
    }
    return {}
  }
  for (const [index, channel] of ['Say', 'Whisper', 'Say'].entries()) {
    const message_id = `message-${index}`
    await f.view.dispatchChat({ message_id, target_conversation_id: 'one', prompt: `message_id=${message_id}\nchannel=${channel}\nMaster body`,
      settings: { communication_attempts: 1, delivery_confirmation_ms: 1000 } })
    assert.equal(f.view.active.kind, 'chat')
    f.view.releaseChat(message_id)
  }
  assert.deepEqual(sent, ['message-0', 'message-1', 'message-2'])
  assert.deepEqual(f.requests.filter(item => item.type === 'holo-chat-sync').map(item => [item.message_id, item.content, item.complete]),
    sent.map(id => [id, `reply to ${id}`, true]))
  assert.equal(f.requests.filter(item => ['holo-sync', 'holo-delivered', 'holo-ended', 'holo-native-send'].includes(item.type)).length, 0)
  assert.deepEqual(f.loads, [], 'changing the audience must not create a different Provider conversation')
  assert.equal(f.view.attached, false, 'ordinary replies belong to the ChatMode window')
})

test('normal dispatch claims the native view before slow observation, and Task reports cannot release it', async () => {
  const f = fixture()
  f.view.active = null
  f.view.surface = { ...f.view.surface, visible: false, mode: 'chat', task_id: null }
  let finish
  f.view.page = request => request.operation === 'observe' ? new Promise(resolve => { finish = resolve }) : Promise.resolve({})
  const pending = f.view.dispatchChat({ message_id: 'chat-one', target_conversation_id: 'one', prompt: 'message_id=chat-one\nMaster body', settings: {} })
  assert.equal(f.view.active.kind, 'chat')
  await f.view.dispatch({ task_id: 'task-one', turn_id: 'turn-one' })
  assert.deepEqual(f.requests.filter(item => item.type === 'holo-ended').map(item => item.sent), [false])
  f.view.release('chat-one')
  assert.equal(f.view.active.kind, 'chat', 'Task release cannot revoke an unrelated normal Message')
  await f.view.cancelChat('chat-one')
  f.view.releaseChat('chat-one')
  finish({ state: 'ready', conversation_id: 'one' })
  await pending
  assert.equal(f.view.active, null)
  assert.equal(f.requests.filter(item => item.type === 'holo-chat-delivered').length, 0)
})

test('normal chat protects a Task draft instead of navigating or sending it', async () => {
  const f = fixture()
  f.view.active = null
  f.view.surface.visible = false
  const operations = []
  f.view.page = async request => {
    operations.push(request.operation)
    return { state: 'blocked', draft: true, conversation_id: 'one', url: f.wc.url }
  }
  await f.view.dispatchChat({ message_id: 'chat-one', target_conversation_id: 'normal', prompt: 'message_id=chat-one\nMaster body', settings: {} })
  assert.deepEqual(f.loads, [])
  assert.ok(!operations.includes('fill') && !operations.includes('send'))
  assert.deepEqual(f.requests.filter(item => item.type === 'holo-chat-ended').map(item => [item.message_id, item.sent]), [['chat-one', false]])
  f.view.releaseChat('chat-one')
})

test('Task selection waits for a normal reply, while chat login visibility preserves its active binding', async () => {
  const f = fixture()
  f.active.kind = 'chat'
  f.active.prepared = true
  f.active.dispatch = { message_id: 'chat-one' }
  f.view.surface = { ...f.view.surface, mode: 'chat', task_id: null, capture: false }
  await f.view.setSurface({ ...f.view.surface, external_conversation_id: 'other', external_url: 'https://chatgpt.com/c/other' })
  assert.equal(f.view.surface.external_conversation_id, 'one')
  assert.deepEqual(f.loads, [], 'opening login may not interrupt the active normal conversation')
  await f.view.setSurface({ ...f.view.surface, mode: 'task', task_id: 'task-two', external_conversation_id: 'two', external_url: 'https://chatgpt.com/c/two' })
  assert.equal(f.view.attached, false)
  assert.deepEqual(f.loads, [], 'Task navigation waits until the normal reply releases the view')
  f.view.releaseChat('chat-one')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.view.surface.task_id, 'task-two')
  assert.deepEqual(f.loads, ['https://chatgpt.com/c/two'])
})

test('Task-free chat login has a native surface and Escape closes its panel without Task authority', async () => {
  const f = fixture()
  f.view.active = null
  await f.view.setSurface({ ...f.view.surface, mode: 'chat', task_id: null, capture: false })
  assert.equal(f.view.attached, true)
  let prevented = 0
  f.wc.emit('before-input-event', { preventDefault() { prevented++ } }, { type: 'keyDown', key: 'Escape' })
  assert.equal(prevented, 1)
  assert.equal(f.events.at(-1).channel, 'nirai:holo-chat-close')
  assert.deepEqual(f.requests, [])
})

test('returning to ChatMode cancels an older pending Task selection while the normal reply finishes', async () => {
  const f = fixture()
  f.active.kind = 'chat'
  f.active.prepared = true
  f.active.dispatch = { message_id: 'chat-one' }
  f.view.surface = { ...f.view.surface, mode: 'chat', task_id: null, capture: false }
  await f.view.setSurface({ ...f.view.surface, mode: 'task', task_id: 'task-two', external_conversation_id: 'two' })
  await f.view.hideSurface()
  await f.view.setSurface({ visible: false, mode: 'chat', task_id: null, capture: false, external_conversation_id: 'one' })
  assert.equal(f.view.pendingSurface, null, 'the newest ChatMode selection replaces the older pending Task')
  f.view.releaseChat('chat-one')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.view.surface.mode, 'chat')
  assert.equal(f.view.surface.task_id, null)
  assert.deepEqual(f.loads, [])
})

test('a released cancellation cannot send a late Stop to the next normal reply', async () => {
  const f = fixture()
  f.active.kind = 'chat'
  f.active.clicked = true
  f.active.dispatch = { message_id: 'chat-one' }
  let stops = 0
  f.view.page = async request => {
    if (request.operation === 'stop') { stops++; return { stopped: false, requested: true } }
    return {}
  }
  const cancelling = f.view.cancelChat('chat-one')
  f.view.releaseChat('chat-one')
  f.view.active = { kind: 'chat', dispatch: { message_id: 'chat-two' }, clicked: true, conversation_id: 'one' }
  await cancelling
  assert.equal(stops, 1, 'the previous response must not stop a newer generation after release')
})

test('ChatMode navigation protects an idle Task draft or local generation and hides its native composer', async () => {
  for (const busy of [false, true]) {
    const f = fixture()
    f.view.active = null
    const operations = []
    f.view.page = async request => {
      operations.push(request.operation)
      return { state: busy ? 'busy' : 'blocked', draft: !busy, busy, conversation_id: 'one', url: f.wc.url }
    }
    const result = await f.view.setSurface({ visible: true, bounds: f.view.surface.bounds, mode: 'chat', task_id: null,
      capture: false, external_conversation_id: 'normal', external_url: 'https://chatgpt.com/c/normal' })
    assert.equal(result.blocked, true)
    assert.equal(f.view.attached, false, 'the old Task composer cannot be exposed as a Task-free login surface')
    assert.equal(f.view.surface.visible, false)
    assert.deepEqual(f.loads, [], 'navigation cannot discard an existing Master draft or generation')
    assert.ok(operations.includes('observe'), 'the current native document must be inspected before navigation')
    assert.equal(f.requests.at(-1).observation.surface_mode, 'chat')
    assert.equal(f.requests.at(-1).observation.state, 'blocked')
    assert.match(result.reason, busy ? /生成終了/ : /下書き/)
    f.view.page = async () => ({ state: 'ready', draft: false, busy: false, conversation_id: 'one', url: f.wc.url })
    await f.view.setSurface({ visible: true, bounds: f.view.surface.bounds, mode: 'chat', task_id: null,
      capture: false, external_conversation_id: 'normal', external_url: 'https://chatgpt.com/c/normal' })
    assert.deepEqual(f.loads, ['https://chatgpt.com/c/normal'], 'retrying the same desired chat binding must navigate away from the protected old document')
    assert.equal(f.wc.url, 'https://chatgpt.com/c/normal')
  }
})

test('a newer Task selection replaces a chat navigation waiting for draft inspection', async () => {
  const f = fixture()
  f.view.active = null
  let finish
  f.view.page = request => request.operation === 'observe' ? new Promise(resolve => { finish = resolve }) : Promise.resolve({})
  const pending = f.view.setSurface({ visible: true, bounds: f.view.surface.bounds, mode: 'chat', task_id: null,
    capture: false, external_conversation_id: 'normal', external_url: 'https://chatgpt.com/c/normal' })
  await new Promise(resolve => setImmediate(resolve))
  await f.view.setSurface({ visible: true, bounds: f.view.surface.bounds, mode: 'task', task_id: 'task-one', external_conversation_id: 'one' })
  finish({ state: 'ready', draft: false, busy: false, conversation_id: 'one', url: f.wc.url })
  await pending
  assert.equal(f.view.surface.task_id, 'task-one')
  assert.equal(f.view.surface.mode, 'task')
  assert.deepEqual(f.loads, [], 'stale draft inspection cannot navigate away from the latest selected Task')
})

test('an aborted older navigation cannot stop or mark the newer navigation as failed', async () => {
  const f = fixture()
  let rejectOld, resolveNew, rejectNew, stops = 0
  f.wc.loadURL = url => {
    f.loads.push(url)
    return url.endsWith('/old') ? new Promise((_resolve, reject) => { rejectOld = reject })
      : new Promise((resolve, reject) => { resolveNew = resolve; rejectNew = reject })
  }
  f.wc.stop = () => { stops++; rejectNew(new Error('new load stopped')) }
  const old = f.view.loadUrl('https://chatgpt.com/c/old')
  await new Promise(resolve => setImmediate(resolve))
  const next = f.view.loadUrl('https://chatgpt.com/c/new')
  await new Promise(resolve => setImmediate(resolve))
  rejectOld(new Error('ERR_ABORTED'))
  assert.equal(await old, false)
  assert.equal(stops, 0, 'a superseded load must not stop the latest native navigation')
  assert.equal(f.view.loadFailure, null)
  assert.equal(f.view.presentation.phase, 'loading', 'the latest loading notice belongs to the latest navigation')
  resolveNew()
  await next
  assert.equal(f.view.loadFailure, null)
  assert.equal(stops, 0)
})

test('older open results cannot erase the latest navigation failure', async () => {
  for (const task of [false, true]) {
    const f = fixture()
    f.wc.url = ''
    let resolveOld, rejectNew
    f.wc.loadURL = url => url.endsWith('/new')
      ? new Promise((_resolve, reject) => { rejectNew = reject })
      : new Promise(resolve => { resolveOld = resolve })
    const old = task ? f.view.openTaskConversation({ task_id: 'task-two', external_conversation_id: 'old' }) : f.view.open()
    await new Promise(resolve => setImmediate(resolve))
    const next = f.view.loadUrl('https://chatgpt.com/c/new')
    await new Promise(resolve => setImmediate(resolve))
    rejectNew(new Error('latest navigation failed'))
    await assert.rejects(next, /latest navigation failed/)
    resolveOld()
    await old
    assert.equal(f.view.loadFailure, 'latest navigation failed', 'an older successful open cannot clear a newer failure')
  }
})
