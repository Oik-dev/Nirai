import { WebContentsView } from 'electron/main'
import { readFileSync } from 'node:fs'
import { holoPageOperation } from './holo-dom.mjs'

const home = 'https://chatgpt.com/'
const hosts = new Set(['chatgpt.com', 'auth.openai.com', 'accounts.google.com', 'login.microsoftonline.com', 'login.live.com', 'appleid.apple.com', 'account.apple.com'])
const allowed = url => { try { const parsed = new URL(url); return parsed.protocol === 'https:' && hosts.has(parsed.hostname) && !parsed.username && !parsed.password } catch { return false } }
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const PAGE_RESPONSE_MS = 10_000
const PAGE_LOAD_MS = 10_000
const TARGET_READY_MS = 5_000
const FINAL_TURN_MS = 5_000
const FINAL_TURN_POLL_MS = 100
const FINAL_TURN_STABLE_READS = 3
const NATIVE_POLL_MS = 250
const SKIN_PROBE_ATTEMPTS = 20
const SKIN_PROBE_INTERVAL_MS = 250
const NIRAI_THEME_CSS = readFileSync(new URL('../renderer/theme.css', import.meta.url), 'utf8')

const HOLO_SKIN_CSS = `${NIRAI_THEME_CSS}\n${readFileSync(new URL('./holo-skin.css', import.meta.url), 'utf8')}`

const clampBounds = (bounds, width, height) => {
  const x = Math.max(0, Math.min(Math.round(bounds?.x ?? 0), Math.max(0, width - 1)))
  const y = Math.max(0, Math.min(Math.round(bounds?.y ?? 0), Math.max(0, height - 1)))
  return {
    x,
    y,
    width: Math.max(1, Math.min(Math.round(bounds?.width ?? width), Math.max(1, width - x))),
    height: Math.max(1, Math.min(Math.round(bounds?.height ?? height), Math.max(1, height - y))),
  }
}

export class HoloView {
  constructor(request, hostWindow, { partition = 'persist:nirai-v2-holo' } = {}) {
    this.request = request
    this.hostWindow = hostWindow
    this.active = null
    this.lastObservation = null
    this.loadFailure = null
    this.refreshing = null
    this.disposed = false
    this.attached = false
    this.surface = { visible: false, bounds: null, task_id: null, capture: false, external_conversation_id: null, external_url: null }
    this.pendingSurface = null
    this.nativeSend = null
    this.skinCssKey = null
    this.skinGeneration = 0
    this.turnObservationGeneration = 0
    this.skinApplying = null
    this.displayReady = false
    this.presentation = null
    this.presentationGeneration = 0
    this.presentationTimer = null
    this.lastPresentation = null

    this.view = new WebContentsView({
      webPreferences: {
        partition,
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true,
        backgroundThrottling: false,
      },
    })
    this.view.setBackgroundColor('#00000000')

    const wc = this.view.webContents
    const guard = contents => {
      contents.on('will-navigate', (event, url) => { if (!allowed(url)) event.preventDefault() })
      contents.on('will-redirect', (event, url) => { if (!allowed(url)) event.preventDefault() })
      contents.setWindowOpenHandler(({ url }) => allowed(url) ? {
        action: 'allow',
        overrideBrowserWindowOptions: {
          autoHideMenuBar: true,
          webPreferences: { partition, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true },
        },
      } : { action: 'deny' })
      contents.on('did-create-window', popup => guard(popup.webContents))
    }
    guard(wc)
    wc.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))
    wc.session.setPermissionCheckHandler(() => false)
    wc.session.on('will-download', event => event.preventDefault())
    wc.on('did-start-navigation', event => {
      if (!event.isMainFrame) return
      if (!event.isSameDocument || (this.active?.conversation_id && this.conversationId(event.url) !== this.active.conversation_id)) {
        // Provider keys can be reused after a reload or a different conversation.
        // Returning to the same URL must prove the user marker again.
        this.turnObservationGeneration += 1
        if (this.active) this.active.provider_turn_key = null
      }
      if (event.isSameDocument) return
      this.displayReady = false
      this.resetSkin()
      this.lastObservation = null
      void this.setPresentationPhase('loading')
      void this.applySurface()
    })
    wc.on('did-fail-load', (_event, code, description, _validatedURL, isMainFrame) => {
      if (!isMainFrame || code === -3) return
      this.loadFailure = description || `error ${code}`
      this.clearPresentation()
      this.notifyPresentation()
      void this.refresh()
    })
    wc.on('did-finish-load', () => {
      this.loadFailure = null
      this.lastObservation = null
      void this.applySkin()
      void this.refresh()
    })
    wc.on('render-process-gone', () => {
      this.loadFailure = 'HoloのWeb表示が終了しました'
      this.clearPresentation()
      this.notifyPresentation()
      void this.refresh()
    })

    this.poll = setInterval(() => void this.refresh(), NATIVE_POLL_MS)
  }

  async open() {
    const current = this.view.webContents.getURL()
    if (!current || this.loadFailure || !allowed(current)) {
      try {
        await this.loadUrl(home)
        this.loadFailure = null
      } catch (error) {
        this.loadFailure = error instanceof Error ? error.message : String(error)
      }
    }
    await this.applySurface()
    this.lastObservation = null
    await this.refresh()
  }

  async loadUrl(url, beforeLoad) {
    let timer
    let started = false
    try {
      await this.setPresentationPhase('loading')
      if (beforeLoad && !await beforeLoad()) { await this.endPresentation('loading'); return false }
      started = true
      return await Promise.race([
        this.view.webContents.loadURL(url),
        new Promise((_resolve, reject) => {
          timer = setTimeout(() => reject(new Error('ChatGPT page load timed out')), PAGE_LOAD_MS)
        }),
      ])
    } catch (error) {
      if (started) try { this.view.webContents.stop() } catch {}
      if (started) this.loadFailure = error instanceof Error ? error.message : String(error)
      await this.endPresentation('loading')
      throw error
    } finally {
      clearTimeout(timer)
    }
  }

  page(request) {
    let timer
    return Promise.race([
      this.view.webContents.executeJavaScript(`(${holoPageOperation.toString()})(${JSON.stringify(request)})`, true),
      new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error('ChatGPT page did not respond')), PAGE_RESPONSE_MS) }),
    ]).finally(() => clearTimeout(timer))
  }

  async observeTurn(active) {
    if (active.aborted || this.active !== active) return null
    const generation = this.turnObservationGeneration
    const turn = await this.page({
      operation: 'turn',
      turn_id: active.dispatch.turn_id,
      conversation_id: active.conversation_id,
      provider_turn_key: active.provider_turn_key ?? null,
    })
    if (active.aborted || this.active !== active || generation !== this.turnObservationGeneration) return null
    if (turn && active.conversation_id && turn.conversation_id !== active.conversation_id) {
      this.turnObservationGeneration += 1
      active.provider_turn_key = null
      return null
    }
    if (turn?.received && turn.conversation_id && (!active.conversation_id || turn.conversation_id === active.conversation_id)) {
      active.provider_turn_key = typeof turn.provider_turn_key === 'string' && turn.provider_turn_key.length <= 512
        ? turn.provider_turn_key : null
    }
    return turn
  }

  async setSurface(next) {
    const surface = {
      visible: Boolean(next?.visible),
      bounds: next?.bounds ?? null,
      task_id: typeof next?.task_id === 'string' ? next.task_id : null,
      capture: next?.capture === true,
      external_conversation_id: typeof next?.external_conversation_id === 'string' ? next.external_conversation_id : null,
      external_url: typeof next?.external_url === 'string' ? next.external_url : null,
    }

    if (this.nativeSend && surface.task_id !== this.nativeSend.task_id) {
      this.pendingSurface = surface
      this.surface = { ...this.surface, visible: false }
      this.clearPresentation()
      await this.applySurface()
      return this.status()
    }

    if (this.active && surface.task_id !== this.active.dispatch.task_id) {
      this.pendingSurface = surface
      this.surface = { ...this.surface, visible: false }
      this.clearPresentation()
      await this.applySurface()
      return this.status()
    }

    const taskChanged = this.surface.task_id !== surface.task_id
    if (taskChanged || !surface.visible) this.clearPresentation()
    this.surface = surface
    this.pendingSurface = null
    if (taskChanged && surface.task_id && !this.active) {
      if (surface.visible) await this.setPresentationPhase('loading')
      else await this.applySurface()
      await this.openTaskConversation(surface)
      if (this.displayReady) await this.endPresentation('loading')
      if (surface.visible && this.attached) this.view.webContents.focus()
    } else await this.applySurface()

    this.lastObservation = null
    await this.refresh()
    return this.status()
  }

  async hideSurface() {
    // Visibility can change while a previous Task navigation is still waiting.
    // Do not wait for that navigation or change the active Turn's authority.
    this.surface = { ...this.surface, visible: false }
    if (this.pendingSurface) this.pendingSurface = { ...this.pendingSurface, visible: false }
    this.clearPresentation()
    await this.applySurface()
  }

  status() {
    return {
      visible: this.attached,
      task_id: this.surface.task_id,
      url: this.view.webContents.getURL() || null,
      presentation: this.presentationStatus(),
    }
  }

  presentationStatus() {
    const pending = this.presentation?.task_id === this.surface.task_id ? this.presentation : null
    return {
      task_id: this.surface.task_id,
      visible: this.attached,
      phase: this.loadFailure ? null : pending?.phase ?? (this.surface.visible && !this.displayReady ? 'loading' : null),
    }
  }

  notifyPresentation() {
    if (this.disposed || this.hostWindow?.isDestroyed?.()) return
    const payload = this.presentationStatus()
    const signature = JSON.stringify(payload)
    if (signature === this.lastPresentation) return
    this.hostWindow.webContents.send('nirai:holo-presentation', payload)
    this.lastPresentation = signature
  }

  clearPresentation() {
    clearTimeout(this.presentationTimer)
    this.presentationTimer = null
    this.presentationGeneration += 1
    this.presentation = null
    if (!this.view.webContents.isDestroyed()) void this.page({ operation: 'presentation', preparing: false }).catch(() => {})
  }

  async setPresentationPhase(phase) {
    if (this.disposed || !this.surface.task_id || !this.surface.visible) return
    clearTimeout(this.presentationTimer)
    this.presentationTimer = null
    const taskId = this.surface.task_id
    const generation = ++this.presentationGeneration
    // Remove the previous Task's native content before any Provider response
    // can delay navigation or the loading notice.
    if (phase === 'loading') {
      this.presentation = { task_id: taskId, phase }
      await this.applySurface()
    }
    await this.page({ operation: 'presentation', preparing: phase === 'preparing', conversation_id: this.surface.external_conversation_id }).catch(() => {})
    if (generation !== this.presentationGeneration || taskId !== this.surface.task_id || this.disposed || !this.surface.visible) return
    this.presentation = { task_id: taskId, phase }
    await this.applySurface()
  }

  async endPresentation(phase) {
    if (phase && this.presentation && this.presentation.phase !== phase) return
    this.clearPresentation()
    await this.applySurface()
  }

  async displayReadySurface() {
    this.displayReady = true
    if (this.presentation?.phase === 'loading') await this.endPresentation('loading')
    else await this.applySurface()
  }

  async openTaskConversation(surface) {
    if (!surface.task_id || this.disposed) return
    const target = surface.external_url && allowed(surface.external_url)
      ? surface.external_url
      : surface.external_conversation_id
        ? `https://chatgpt.com/c/${surface.external_conversation_id}`
        : home

    const current = this.view.webContents.getURL()
    const currentId = this.conversationId(current)
    if (surface.external_conversation_id
      ? currentId === surface.external_conversation_id
      : current === home || current === 'https://chatgpt.com') return

    try {
      await this.loadUrl(target)
      this.loadFailure = null
    } catch (error) {
      this.loadFailure = error instanceof Error ? error.message : String(error)
    }
  }

  async applySurface() {
    if (this.disposed || this.hostWindow?.isDestroyed?.()) return
    const currentUrl = this.view.webContents.getURL()
    const waitingForChatGptSkin = currentUrl.startsWith('https://chatgpt.com') && !this.displayReady
    if (!this.surface.visible || !this.surface.bounds || !this.surface.task_id || waitingForChatGptSkin || this.presentation?.phase === 'loading') {
      if (this.attached) {
        try { this.hostWindow.contentView.removeChildView(this.view) } catch {}
        this.attached = false
      }
      this.notifyPresentation()
      return
    }

    const [width, height] = this.hostWindow.getContentSize()
    this.view.setBounds(clampBounds(this.surface.bounds, width, height))
    if (!this.attached) {
      this.hostWindow.contentView.addChildView(this.view)
      this.attached = true
    }
    this.notifyPresentation()
  }

  conversationId(url) {
    try {
      const parsed = new URL(url)
      if (parsed.origin !== 'https://chatgpt.com') return null
      return /^\/(?:g\/[^/]+\/)?c\/([a-zA-Z0-9-]+)\/?$/.exec(parsed.pathname)?.[1] ?? null
    } catch {
      return null
    }
  }

  async publish(observation) {
    const value = {
      state: observation.state,
      reason: observation.reason,
      url: observation.url,
      conversation_id: observation.conversation_id,
      task_id: this.surface.task_id,
      busy: observation.busy,
      draft: observation.draft,
    }
    const serialized = JSON.stringify(value)
    if (serialized === this.lastObservation) return
    await this.request('holo-observe', { observation: value })
    this.lastObservation = serialized
  }

  async refresh() {
    if (this.disposed || this.view.webContents.isDestroyed()) return
    if (this.refreshing) return this.refreshing
    const refresh = (async () => {
      try {
        await this.drainNativeEvents()
        if (this.loadFailure) {
          await this.publish({
            state: 'unavailable',
            reason: `ChatGPTを読み込めません: ${this.loadFailure}`,
            url: null,
            conversation_id: null,
            busy: false,
            draft: false,
          })
          return
        }

        const observation = await this.page({ operation: 'observe' })
        if (!this.surface.task_id && !this.active) {
          await this.publish({ ...observation, state: 'blocked', reason: 'Holo Taskを選択してください' })
        } else if (this.surface.task_id && !this.active && (
          this.surface.external_conversation_id
            ? observation.conversation_id !== this.surface.external_conversation_id
            : observation.conversation_id !== null
        )) {
          await this.publish({
            ...observation,
            state: 'blocked',
            reason: this.surface.external_conversation_id
              ? '選択中TaskのConversationではありません'
              : '新しいHolo Taskは新しいConversationで開始してください',
          })
        } else {
          await this.publish(observation)
        }
      } catch {
        this.lastObservation = null
      }
    })()
    this.refreshing = refresh
    try { await refresh }
    finally { if (this.refreshing === refresh) this.refreshing = null }
  }

  async drainNativeEvents() {
    const capture = Boolean(this.surface.task_id) && this.surface.capture
    const result = await this.page({
      operation: 'native-events',
      capture,
      task_id: capture ? this.surface.task_id : null,
      conversation_id: this.surface.external_conversation_id,
      turn_id: this.active?.dispatch.turn_id ?? null,
    }).catch(() => ({ events: [] }))
    for (const event of result?.events ?? []) {
      if (event.kind === 'send') await this.handleNativeSend(event)
      else if (event.kind === 'stop') await this.handleNativeStop(event)
    }
    if (this.nativeSend && !this.active) await this.handleNativeSend(this.nativeSend)
  }

  async handleNativeSend(event) {
    const taskId = this.surface.task_id
    if (!taskId || event?.task_id !== taskId || this.active || !event?.content?.trim?.()) return
    if (this.surface.external_conversation_id
      ? event.conversation_id !== this.surface.external_conversation_id
      : event.conversation_id !== null) {
      this.lastObservation = null
      return
    }
    const pending = this.nativeSend ?? {
      id: event.id,
      task_id: taskId,
      content: event.content,
      issued_at: event.issued_at,
      url: event.url ?? null,
      conversation_id: event.conversation_id ?? null,
    }
    if (pending.task_id !== taskId || pending.content !== event.content) return
    this.nativeSend = pending

    try {
      await this.setPresentationPhase('preparing')
      await this.publish({
        state: 'blocked',
        reason: 'Master入力をHubへ保存しています',
        url: pending.url ?? this.view.webContents.getURL() ?? null,
        conversation_id: pending.conversation_id ?? null,
        busy: false,
        draft: true,
      })
      let accepted = null
      try {
        accepted = await this.request('holo-native-send', {
          event_id: pending.id,
          task_id: pending.task_id,
          content: pending.content,
          issued_at: pending.issued_at,
        })
      } catch {
        accepted = await this.request('receipt', { command_id: pending.id }).catch(() => null)
      }
      if (!accepted) { await this.endPresentation('preparing'); return }
      const cleared = await this.page({ operation: 'clear-native', content: pending.content }).catch(() => null)
      if (cleared?.cleared) {
        this.nativeSend = null
        this.deferPendingSurface()
      } else await this.endPresentation('preparing')
    } finally {
      if (!this.active) {
        if (!this.nativeSend && this.presentation?.phase === 'preparing') {
          const generation = this.presentationGeneration
          // Cover the short Hub-to-dispatch handoff, without keeping a paused
          // or safety-blocked Task's input covered indefinitely.
          this.presentationTimer = setTimeout(() => {
            if (!this.active && generation === this.presentationGeneration) void this.endPresentation('preparing')
          }, NATIVE_POLL_MS * 2)
        } else await this.endPresentation('preparing')
      }
      this.lastObservation = null
    }
  }

  deferPendingSurface() {
    if (!this.pendingSurface || this.active || this.nativeSend) return
    const next = this.pendingSurface
    this.pendingSurface = null
    setTimeout(() => void this.setSurface(next).catch(() => {}), 0)
  }

  async handleNativeStop(event) {
    const active = this.active
    if (!active || event?.task_id !== active.dispatch.task_id || active.dispatch.task_id !== this.surface.task_id) return
    await delay(50)
    const turn = await this.observeTurn(active).catch(() => null)
    if (turn?.text?.trim?.()) {
      await this.request('holo-sync', {
        turn_id: active.dispatch.turn_id,
        content: turn.text,
        complete: false,
      }).catch(() => {})
    }
    await this.request('holo-ended', {
      turn_id: active.dispatch.turn_id,
      reason: 'master_stop',
      sent: true,
    }).catch(() => {})
  }

  async stableTurn(active, initial, allowReload) {
    let current = initial
    let stableText = null
    let stableReads = 0
    const deadline = Date.now() + FINAL_TURN_MS

    while (Date.now() < deadline) {
      this.check(active)
      if (!current) current = await this.observeTurn(active).catch(() => null)
      this.check(active)
      if (current?.received && (current.busy || !current.waiting)) return current
      const text = current?.text?.trim?.() ?? ''
      const valid = current?.received && !current.busy && current.waiting && text.length > 0
      if (valid) {
        if (text === stableText) stableReads += 1
        else {
          stableText = text
          stableReads = 1
        }
        if (stableReads >= FINAL_TURN_STABLE_READS) return current
      } else {
        stableText = null
        stableReads = 0
      }
      await delay(FINAL_TURN_POLL_MS)
      current = await this.observeTurn(active).catch(() => null)
    }

    if (!allowReload) return null
    const latest = await this.observeTurn(active).catch(() => null)
    this.check(active)
    if (latest?.received && (latest.busy || !latest.waiting)) return latest
    if (latest?.received && latest.waiting && latest.text?.trim?.()) return this.stableTurn(active, latest, false)
    const url = this.view.webContents.getURL()
    // ChatGPT may replace both the user bubble and its Provider key. Only a
    // previously proven Turn in this conversation/document may reload history;
    // the new document must then prove the real user marker again.
    const canRecover = observation => Boolean((observation?.received || active.provider_turn_key)
      && !observation?.busy && observation?.waiting && !observation?.draft && !observation?.text?.trim?.()
      && observation?.url === url && observation?.conversation_id === active.conversation_id)
    if (!canRecover(latest)
      || latest.url !== url || !allowed(url) || !active.conversation_id
      || this.conversationId(url) !== active.conversation_id) return null
    this.check(active)
    let before = null
    const loaded = await this.loadUrl(url, async () => {
      before = await this.observeTurn(active).catch(() => null)
      this.check(active)
      return Boolean(canRecover(before) && this.view.webContents.getURL() === url
        && this.conversationId(url) === active.conversation_id)
    })
    this.check(active)
    if (loaded === false) {
      if (before?.received && (before.busy || !before.waiting)) return before
      if (before?.received && before.text?.trim?.()) return this.stableTurn(active, before, false)
      return null
    }
    return this.stableTurn(active, null, false)
  }

  check(active) {
    if (active.aborted || this.active !== active) throw new Error('Holo送信許可が失効しました')
  }

  async waitUntilReady(active, prompt) {
    const deadline = Date.now() + TARGET_READY_MS
    let state = null
    while (Date.now() < deadline) {
      this.check(active)
      state = await this.page({ operation: 'observe', prompt }).catch(() => null)
      if (!state) {
        await delay(100)
        continue
      }
      if (active.conversation_id
        ? state.conversation_id !== active.conversation_id
        : state.conversation_id !== null) {
        return { ...state, state: 'blocked', retryable: false, reason: '送信先Conversationが変更されました' }
      }
      if (state.state === 'ready') return state
      if (state.retryable !== true) return state
      await delay(100)
    }
    return state ?? { state: 'blocked', retryable: true, reason: 'Holo Conversationの準備を待っています' }
  }

  async dispatch(dispatch) {
    const ended = (reason, sent) => this.request('holo-ended', { turn_id: dispatch.turn_id, reason, sent })
    if (this.active) { await ended('Holo表示は使用中です', false); return }
    if (this.surface.task_id !== dispatch.task_id) { await ended('このTaskのHolo Conversationが選択されていません', false); return }

    const observation = await this.page({ operation: 'observe' }).catch(() => null)
    const expected = dispatch.target_conversation_id
    if (!observation) {
      await ended('Holo Conversationを利用できません', false)
      return
    }
    if (expected ? observation.conversation_id !== expected : observation.conversation_id !== null) {
      await ended('選択中TaskのConversationではありません', false)
      return
    }

    const active = this.active = {
      dispatch,
      aborted: false,
      clicked: false,
      preparing: false,
      conversation_id: observation.conversation_id,
    }
    const { prompt, settings } = dispatch
    const attempts = Math.max(1, Number(settings.communication_attempts) || 1)
    const retryDelays = Array.isArray(settings.communication_retry_ms) ? settings.communication_retry_ms : []

    try {
      await this.setPresentationPhase('preparing')
      this.check(active)
      let failure = null
      for (let attempt = 0; attempt < attempts; attempt++) {
        if (failure) {
          await this.page({ operation: 'clear', conversation_id: active.conversation_id, prompt, preparing: active.preparing }).catch(() => {})
          active.preparing = false
          await delay(Number(retryDelays[attempt - 1] ?? retryDelays.at(-1) ?? 1000))
        }
        this.check(active)
        failure = null
        const before = await this.waitUntilReady(active, prompt)
        if (!before || before.state !== 'ready') {
          failure = { retryable: Boolean(before?.retryable), reason: before?.reason ?? 'Holo Conversationを利用できません' }
          if (failure.retryable) continue
          break
        }

        let ready = null
        let filled = null
        const prepareDeadline = Date.now() + TARGET_READY_MS
        while (Date.now() < prepareDeadline) {
          this.check(active)
          filled = await this.page({ operation: 'fill', conversation_id: active.conversation_id, prompt, preparing: active.preparing })
            .catch(() => ({ ok: false, retryable: true, reason: 'Composerへ入力できません' }))
          // Cancellation may arrive after the page inserted the app query but
          // before its result reached Main. Keep ownership for cleanup first.
          if (filled.started) active.preparing = true
          this.check(active)
          if (!filled.ok) break
          ready = await this.page({ operation: 'observe', prompt, preparing: active.preparing })
          this.check(active)
          if (ready.draft || ready.ready_to_send) break
          await delay(100)
        }
        if (!filled?.ok) { failure = filled; if (filled?.retryable === true) continue; break }
        if (!ready?.ready_to_send) {
          failure = { retryable: !ready?.draft, reason: filled?.pending ? filled.reason : '送信ボタンを確認できません' }
          if (failure.retryable) continue
          break
        }

        active.clicked = true
        const sent = await this.page({ operation: 'send', conversation_id: active.conversation_id, turn_id: dispatch.turn_id, prompt })
        if (!sent.ok) {
          active.clicked = false
          failure = sent
          if (sent.retryable === true) continue
          break
        }
        await this.endPresentation('preparing')
        break
      }
      if (!active.clicked) { await ended(failure?.reason ?? 'Holoへ送信できません', false); return }

      const deadline = Date.now() + settings.delivery_confirmation_ms
      let turn = null
      while (!turn?.received) {
        if (Date.now() >= deadline) { await ended('送信の受領を確認できません', true); return }
        await delay(100)
        this.check(active)
        turn = await this.observeTurn(active)
      }

      active.conversation_id = turn.conversation_id
      this.surface = {
        ...this.surface,
        external_conversation_id: turn.conversation_id,
        external_url: turn.url,
      }
      await this.request('holo-delivered', { turn_id: dispatch.turn_id, url: turn.url })
      await this.publish({ ...turn, task_id: dispatch.task_id })

      for (;;) {
        this.check(active)
        if (turn.waiting && !turn.busy) {
          const final = await this.stableTurn(active, turn, true)
          this.check(active)
          if (final?.received && (final.busy || !final.waiting)) { turn = final; continue }
          if (!final?.text) {
            await ended('ChatGPTの最終回答を取得できません', true)
            return
          }
          await this.request('holo-sync', {
            turn_id: dispatch.turn_id,
            content: final.text,
            complete: true,
          })
          return
        }
        await delay(250)
        turn = await this.observeTurn(active)
      }
    } catch {
      if (active.aborted || this.active !== active) return
      await ended(active.clicked ? 'ChatGPTとの接続が失われました' : '送信前に接続が失われました', active.clicked).catch(() => {})
    } finally {
      if (this.active === active || !this.active) {
        if (!active.clicked) await this.page({ operation: 'clear', conversation_id: active.conversation_id, prompt, preparing: active.preparing }).catch(() => {})
        await this.endPresentation('preparing')
      }
      await this.refresh()
    }
  }

  async cancel(turnId) {
    const active = this.active
    if (active?.dispatch.turn_id !== turnId) return
    active.aborted = true
    if (!active.clicked) return
    for (let i = 0; i < 10; i++) {
      const result = await this.page({ operation: 'stop', conversation_id: active.conversation_id }).catch(() => ({ stopped: true }))
      if (result.stopped || !result.requested) break
      await delay(100)
    }
  }

  release(turnId) {
    if (this.active?.dispatch.turn_id !== turnId) return
    const active = this.active
    active.aborted = true
    if (!active.clicked) void this.page({ operation: 'clear', conversation_id: active.conversation_id, prompt: active.dispatch.prompt, preparing: active.preparing }).catch(() => {})
    this.active = null
    void this.endPresentation('preparing')

    if (this.pendingSurface) {
      const next = this.pendingSurface
      this.pendingSurface = null
      void this.setSurface(next).catch(() => {})
    } else {
      void this.refresh()
    }
  }

  disconnected() {
    this.lastObservation = null
    this.surface = { ...this.surface, visible: false }
    this.clearPresentation()
    void this.applySurface()
    const id = this.active?.dispatch.turn_id
    if (id) void this.cancel(id).finally(() => this.release(id))
  }

  resetSkin() {
    const key = this.skinCssKey
    this.skinCssKey = null
    this.skinApplying = null
    this.skinGeneration += 1
    if (key && !this.view.webContents.isDestroyed()) void this.view.webContents.removeInsertedCSS(key).catch(() => {})
  }

  async waitForSkinProbe(generation) {
    for (let attempt = 0; attempt < SKIN_PROBE_ATTEMPTS; attempt++) {
      if (generation !== this.skinGeneration || this.view.webContents.isDestroyed()) return 'cancelled'
      try {
        const probe = await this.view.webContents.executeJavaScript(`(() => {
          const visible = element => {
            if (!(element instanceof HTMLElement) || !element.isConnected) return false;
            const style = getComputedStyle(element);
            return !element.hidden && element.getClientRects().length > 0
              && style.display !== 'none' && style.visibility !== 'hidden';
          };
          const login = [...document.querySelectorAll('button,a')].some(element =>
            visible(element) && /^(?:Log in|Sign in|ログイン)$/.test(element.textContent?.trim() ?? '')
          );
          return {
            main: Boolean(document.querySelector('main')),
            composer: Boolean(document.querySelector('#prompt-textarea,textarea[placeholder],[contenteditable="true"][data-virtualkeyboard="true"],[contenteditable="true"]')),
            login
          };
        })()`, true)
        if (probe?.main && probe?.composer) return 'healthy'
        if (probe?.login) return 'login'
      } catch {
        // ChatGPT can still be hydrating. Retry inside the bounded probe window.
      }
      if (attempt < SKIN_PROBE_ATTEMPTS - 1) await delay(SKIN_PROBE_INTERVAL_MS)
    }
    return 'timeout'
  }

  async applySkin() {
    if (this.disposed || this.view.webContents.isDestroyed()) return
    if (this.skinApplying) return this.skinApplying

    const operation = (async () => {
      const generation = ++this.skinGeneration
      const key = this.skinCssKey
      this.skinCssKey = null
      if (key) await this.view.webContents.removeInsertedCSS(key).catch(() => {})

      const currentUrl = this.view.webContents.getURL()
      if (!currentUrl.startsWith('https://chatgpt.com')) {
        await this.displayReadySurface()
        return
      }

      try {
        const probe = await this.waitForSkinProbe(generation)
        if (generation !== this.skinGeneration || this.view.webContents.isDestroyed()) return
        if (probe !== 'healthy') {
          await this.displayReadySurface()
          return
        }

        const cssKey = await this.view.webContents.insertCSS(HOLO_SKIN_CSS, { cssOrigin: 'user' })
        if (generation !== this.skinGeneration || this.view.webContents.isDestroyed()) {
          if (!this.view.webContents.isDestroyed()) await this.view.webContents.removeInsertedCSS(cssKey).catch(() => {})
          return
        }
        this.skinCssKey = cssKey
        await this.view.webContents.executeJavaScript(`document.documentElement?.setAttribute('data-nirai-holo-skin','product')`, true)
        await this.page({ operation: 'decorate' }).catch(() => {})
        await this.displayReadySurface()
      } catch {
        if (generation !== this.skinGeneration || this.view.webContents.isDestroyed()) return
        await this.displayReadySurface()
        // Skin is cosmetic. Native ChatGPT remains usable without it.
      }
    })()

    this.skinApplying = operation
    try { await operation }
    finally { if (this.skinApplying === operation) this.skinApplying = null }
  }

  close() {
    this.disposed = true
    this.clearPresentation()
    clearInterval(this.poll)
    this.resetSkin()
    try {
      void this.page({ operation: 'native-events', capture: false }).catch(() => {})
      if (this.attached && !this.hostWindow?.isDestroyed?.()) this.hostWindow.contentView.removeChildView(this.view)
    } catch {}
    if (!this.view.webContents.isDestroyed()) this.view.webContents.close()
  }
}
