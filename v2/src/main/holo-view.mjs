import { BrowserWindow, WebContentsView } from 'electron/main'
import { holoPageOperation } from './holo-dom.mjs'

const home = 'https://chatgpt.com/'
const hosts = new Set(['chatgpt.com', 'auth.openai.com', 'accounts.google.com', 'login.microsoftonline.com', 'login.live.com', 'appleid.apple.com', 'account.apple.com'])
const allowed = url => { try { const parsed = new URL(url); return parsed.protocol === 'https:' && hosts.has(parsed.hostname) && !parsed.username && !parsed.password } catch { return false } }
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

export class HoloView {
  constructor(request, { partition = 'persist:nirai-v2-holo', visible = true, icon } = {}) {
    this.request = request
    this.active = null
    this.lastObservation = null
    this.loadFailure = null
    this.refreshing = null
    this.disposed = false
    this.window = new BrowserWindow({ width: 1020, height: 900, minWidth: 400, minHeight: 500, show: false, title: 'Nirai v2 · Holo', backgroundColor: '#07131d', ...(icon ? { icon } : {}) })
    this.window.removeMenu()
    this.view = new WebContentsView({ webPreferences: { partition, nodeIntegration: false, contextIsolation: true, sandbox: true } })
    this.window.contentView.addChildView(this.view)
    const resize = () => { const [width, height] = this.window.getContentSize(); this.view.setBounds({ x: 0, y: 0, width, height }) }
    this.window.on('resize', resize); resize()
    this.window.on('close', event => { if (!this.disposed) { event.preventDefault(); this.window.hide() } })
    const wc = this.view.webContents
    const guard = contents => {
      contents.on('will-navigate', (event, url) => { if (!allowed(url)) event.preventDefault() })
      contents.on('will-redirect', (event, url) => { if (!allowed(url)) event.preventDefault() })
      contents.setWindowOpenHandler(({ url }) => allowed(url) ? { action: 'allow', overrideBrowserWindowOptions: {
        autoHideMenuBar: true, webPreferences: { partition, nodeIntegration: false, contextIsolation: true, sandbox: true },
      } } : { action: 'deny' })
      contents.on('did-create-window', popup => guard(popup.webContents))
    }
    guard(wc)
    wc.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))
    wc.session.setPermissionCheckHandler(() => false)
    wc.session.on('will-download', event => event.preventDefault())
    wc.on('did-fail-load', (_event, code, description, validatedURL, isMainFrame) => {
      if (!isMainFrame || code === -3) return
      this.loadFailure = description || `error ${code}`
      this.lastObservation = null
      void this.publish({
        state: 'unavailable',
        reason: `ChatGPTを読み込めません: ${this.loadFailure}`,
        url: allowed(validatedURL) ? validatedURL : null,
        conversation_id: null,
        busy: false,
        draft: false,
      }).catch(() => {})
    })
    wc.on('did-finish-load', () => {
      this.loadFailure = null
      this.lastObservation = null
      void this.refresh()
    })
    wc.on('render-process-gone', () => void this.publish({ state: 'unavailable', reason: 'HoloのWeb表示が終了しました', url: null, conversation_id: null, busy: false, draft: false }))
    this.poll = setInterval(() => void this.refresh(), 1000)
    this.visible = visible
  }
  async open(show = this.visible) {
    const current = this.view.webContents.getURL()
    if (!current || this.loadFailure || !allowed(current)) {
      try {
        await this.view.webContents.loadURL(home)
        this.loadFailure = null
      } catch (error) {
        this.loadFailure = error instanceof Error ? error.message : String(error)
      }
    }
    if (show) { this.window.show(); this.window.focus() }
    await this.refresh()
  }
  page(request) { return this.view.webContents.executeJavaScript(`(${holoPageOperation.toString()})(${JSON.stringify(request)})`, true) }
  async publish(observation) {
    const { state, reason, url, conversation_id, busy, draft } = observation
    const compact = { state, reason, url, conversation_id, busy, draft }
    const serialized = JSON.stringify(compact)
    if (serialized === this.lastObservation) return
    await this.request('holo-observe', { observation: compact })
    this.lastObservation = serialized
  }
  async refresh() {
    if (this.disposed) return
    if (this.refreshing) return this.refreshing
    const refresh = (async () => {
      try {
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
        await this.publish(await this.page({ operation: 'observe' }))
      } catch {
        this.lastObservation = null
      }
    })()
    this.refreshing = refresh
    try { await refresh }
    finally { if (this.refreshing === refresh) this.refreshing = null }
  }
  async dispatch(dispatch) {
    if (this.active) {
      await this.request('holo-delivered', { turn_id: dispatch.turn_id, result: { status: 'not_sent', retryable: false, reason: 'Holo表示は使用中です' } })
      return
    }
    const active = this.active = { dispatch, aborted: false, clicked: false, sending: false, conversation_id: dispatch.target_conversation_id, baseline_assistants: 0 }
    const finish = result => this.request('holo-delivered', { turn_id: dispatch.turn_id, result })
    const check = () => { if (active.aborted || this.active !== active) throw new Error('Holo送信許可が失効しました') }
    const maxAttempts = Math.max(1, Number(dispatch.settings.communication_attempts) || 1)
    const retryDelays = Array.isArray(dispatch.settings.communication_retry_ms) ? dispatch.settings.communication_retry_ms : []
    const failBeforeClick = async (attempt, reason, retryable) => {
      const willRetry = Boolean(retryable) && attempt + 1 < maxAttempts
      await finish({ status: 'not_sent', retryable: willRetry, reason })
      if (!willRetry) return false
      await this.page({ operation: 'clear', conversation_id: active.conversation_id, prompt: dispatch.prompt }).catch(() => {})
      await delay(Number(retryDelays[attempt] ?? retryDelays.at(-1) ?? 1000))
      check()
      return true
    }
    try {
      for (let attempt = 0; attempt < maxAttempts; attempt++) {
        check()
        let before
        try { before = await this.page({ operation: 'observe', prompt: dispatch.prompt }) }
        catch { if (await failBeforeClick(attempt, 'ChatGPT画面を確認できません', true)) continue; return }
        if (before.state !== 'ready') {
          if (await failBeforeClick(attempt, before.reason, before.retryable === true)) continue
          return
        }

        const target = dispatch.target_conversation_id ? `https://chatgpt.com/c/${dispatch.target_conversation_id}` : home
        if (before.conversation_id !== dispatch.target_conversation_id || (!dispatch.target_conversation_id && before.url !== home)) {
          try { await this.view.webContents.loadURL(target); check() }
          catch { if (await failBeforeClick(attempt, '対象Conversationを開けません', true)) continue; return }

          const deadline = Date.now() + 5000
          let prepared = null
          while (Date.now() < deadline) {
            check()
            try {
              const state = await this.page({ operation: 'observe', prompt: dispatch.prompt })
              const onTarget = dispatch.target_conversation_id
                ? state.conversation_id === dispatch.target_conversation_id
                : state.conversation_id === null && state.url === home
              if (onTarget && state.state === 'ready') { prepared = state; break }
              if (onTarget && (state.draft || state.state === 'unavailable' || /ログイン/.test(state.reason ?? ''))) { prepared = { ...state, retryable: false }; break }
            } catch {}
            await delay(100)
          }
          if (!prepared || prepared.state !== 'ready') {
            const reason = prepared?.reason ?? '対象Conversationの準備を待っています'
            if (await failBeforeClick(attempt, reason, prepared?.retryable !== false)) continue
            return
          }
          before = prepared
        }

        active.baseline_assistants = Number(before.assistant_count ?? 0)
        let filled
        try { filled = await this.page({ operation: 'fill', conversation_id: active.conversation_id, prompt: dispatch.prompt }); check() }
        catch { if (await failBeforeClick(attempt, 'Composerへ入力できません', true)) continue; return }
        if (!filled.ok) {
          if (await failBeforeClick(attempt, filled.reason, filled.retryable === true)) continue
          return
        }

        let readyToSend = false
        for (let i = 0; i < 20; i++) {
          const state = await this.page({ operation: 'observe', prompt: dispatch.prompt }); check()
          if (state.ready_to_send) { readyToSend = true; break }
          if (state.draft && state.retryable === false) break
          await delay(50)
        }
        if (!readyToSend) {
          if (await failBeforeClick(attempt, '送信ボタンを確認できません', true)) continue
          return
        }

        active.sending = true
        const sent = await this.page({ operation: 'send', conversation_id: active.conversation_id, prompt: dispatch.prompt })
        active.clicked = sent.ok
        active.sending = false
        if (!sent.ok) {
          if (await failBeforeClick(attempt, sent.reason, sent.retryable === true)) continue
          return
        }

        const confirmDeadline = Date.now() + dispatch.settings.delivery_confirmation_ms
        let confirmed = false
        while (Date.now() < confirmDeadline && !active.aborted) {
          const evidence = await this.page({ operation: 'evidence', conversation_id: dispatch.target_conversation_id, turn_id: dispatch.turn_id })
          if (evidence.received) {
            active.conversation_id = evidence.conversation_id
            await finish({ status: 'confirmed', url: evidence.url })
            await this.publish(evidence)
            confirmed = true
            break
          }
          await delay(100)
        }
        if (!confirmed) { await finish({ status: 'unknown', reason: '受領の確認ができません' }); return }

        while (!active.aborted && this.active === active) {
          const state = await this.page({ operation: 'observe' })
          const newAssistant = Number(state.assistant_count ?? 0) > active.baseline_assistants
          if (state.assistant_complete && newAssistant && String(state.last_assistant_text ?? '').trim()) {
            await this.request('holo-assistant', { turn_id: dispatch.turn_id, content: state.last_assistant_text, url: state.url })
            await this.publish(state)
            return
          }
          await delay(250)
        }
        return
      }
    } catch {
      if (active.aborted) return
      if (active.clicked || active.sending) await this.request('holo-failed', { turn_id: dispatch.turn_id, reason: 'ChatGPTとの接続が失われました' }).catch(() => {})
      else await finish({ status: 'not_sent', retryable: false, reason: '送信前に接続が失われました' }).catch(() => {})
    } finally {
      if (!active.clicked) await this.page({ operation: 'clear', conversation_id: active.conversation_id, prompt: dispatch.prompt }).catch(() => {})
      await this.refresh()
    }
  }

  async cancel(turnId) {
    const active = this.active
    if (!active || active.dispatch.turn_id !== turnId) {
      await this.request('holo-stopped', { turn_id: turnId }).catch(() => {})
      return
    }
    active.aborted = true
    try {
      if (active.clicked || active.sending) {
        for (let i = 0; i < 10; i++) {
          const result = await this.page({ operation: 'stop', conversation_id: active.conversation_id })
          if (result.stopped || !result.requested) break
          await delay(100)
        }
      }
    } finally {
      await this.page({ operation: 'clear', conversation_id: active.conversation_id, prompt: active.dispatch.prompt }).catch(() => {})
      await this.request('holo-stopped', { turn_id: turnId }).catch(() => {})
    }
  }

  release(turnId) {
    if (this.active?.dispatch.turn_id !== turnId) return
    const active = this.active
    active.aborted = true
    void this.page({ operation: 'clear', conversation_id: active.conversation_id, prompt: active.dispatch.prompt }).catch(() => {})
    this.active = null
  }

  disconnected() {
    this.lastObservation = null
    const id = this.active?.dispatch.turn_id
    if (id) void this.cancel(id).finally(() => this.release(id))
  }
  close() { this.disposed = true; clearInterval(this.poll); if (!this.view.webContents.isDestroyed()) this.view.webContents.close(); this.window.destroy() }
}
