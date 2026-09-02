import { BrowserWindow, WebContentsView, shell, type WebContents } from 'electron'
import { randomUUID } from 'node:crypto'
import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { getNiraiRoot } from '../paths'
import {
  HOLO_CHATGPT_HOME_URL,
  HOLO_CLIPBOARD_GESTURE_TTL_MS,
  HOLO_SKIN_CSS,
  HOLO_SESSION_PARTITION,
  buildHoloBootstrapTemplate,
  buildHoloDisclaimerSuppressionScript,
  buildHoloSkinAppliedProbeScript,
  buildHoloSkinMarkerScript,
  buildHoloSkinProbeScript,
  clampHoloSurfaceBounds,
  isHealthyHoloSkinProbe,
  isHoloAllowedNavigationUrl,
  isHoloConversationUrl,
  isSafeHoloExternalUrl,
  shouldResetHoloSkinForNavigation,
  shouldAllowHoloWebPermission,
  deriveHoloAddonPhase,
  type HoloAddonPhase,
  type HoloSkinMode,
  type HoloSurfaceBounds,
  type HoloWebState
} from './holoWeb'

interface PersistedHoloState {
  readonly current_dive_url: string | null
  readonly current_dive_session_id: string | null
  readonly updated_at: string
}

export type HoloDiveState = 'none' | 'preparing' | 'current'

export interface HoloAddonStatus {
  readonly phase: HoloAddonPhase
  readonly visible: boolean
  readonly loaded: boolean
  readonly web_state: HoloWebState
  readonly dive_state: HoloDiveState
  readonly current_url: string | null
  readonly current_dive_url: string | null
  readonly current_dive_session_id: string | null
  readonly title: string | null
  readonly skin_mode: HoloSkinMode
  readonly issue: 'web_load_failed' | 'unexpected_error' | null
  readonly persistence_issue: 'state_persistence_failed' | null
}

export interface HoloDiveResult extends HoloAddonStatus {
  readonly bootstrap_prepared: boolean
}

function localIsoDate(now = new Date()): string {
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function bootstrapInsertionScript(text: string): string {
  return `(() => {
    const target = document.querySelector('#prompt-textarea')
      ?? document.querySelector('textarea[placeholder]')
      ?? document.querySelector('[contenteditable="true"][data-virtualkeyboard="true"]')
      ?? document.querySelector('[contenteditable="true"]');
    if (!(target instanceof HTMLElement)) return false;
    target.focus();
    if (target instanceof HTMLTextAreaElement) {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      if (setter) setter.call(target, ${JSON.stringify(text)});
      else target.value = ${JSON.stringify(text)};
      target.dispatchEvent(new Event('input', { bubbles: true }));
      return target.value.includes('Nirai Dive');
    }
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(target);
    selection?.removeAllRanges();
    selection?.addRange(range);
    document.execCommand('insertText', false, ${JSON.stringify(text)});
    target.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      inputType: 'insertText',
      data: ${JSON.stringify(text)}
    }));
    const value = target.innerText || target.textContent || '';
    return value.includes('Nirai Dive');
  })()`
}

async function delay(ms: number): Promise<void> {
  await new Promise<void>((resolve) => setTimeout(resolve, ms))
}

interface HoloStateIo {
  readonly readText: (path: string) => Promise<string>
  readonly ensureDirectory: (path: string) => Promise<void>
  readonly writeText: (path: string, content: string) => Promise<void>
  readonly renameFile: (source: string, target: string) => Promise<void>
  readonly removeFile: (path: string) => Promise<void>
}

const DEFAULT_HOLO_STATE_IO: HoloStateIo = {
  readText: (path) => readFile(path, 'utf8'),
  ensureDirectory: async (path) => { await mkdir(path, { recursive: true }) },
  writeText: async (path, content) => { await writeFile(path, content, 'utf8') },
  renameFile: (source, target) => rename(source, target),
  removeFile: async (path) => { await unlink(path) }
}

async function replaceFileAtomically(
  temporaryPath: string,
  targetPath: string,
  renameFile: HoloStateIo['renameFile']
): Promise<void> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      await renameFile(temporaryPath, targetPath)
      return
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code
      const retryable = code === 'EPERM' || code === 'EACCES' || code === 'EBUSY'
      if (!retryable || attempt >= 7) throw error
      // Windows can briefly deny replacement while another process is reading
      // state.json. Keep the atomic temp-file contract and retry only the
      // replacement step within a small bounded window.
      await delay((attempt + 1) * 10)
    }
  }
}

export class HoloAddonHost {
  private view: WebContentsView | null = null
  private attached = false
  private currentDiveUrl: string | null = null
  private currentDiveSessionId: string | null = null
  private skinMode: HoloSkinMode = 'checking'
  private webState: HoloWebState = 'idle'
  private issue: HoloAddonStatus['issue'] = null
  private persistenceIssue: HoloAddonStatus['persistence_issue'] = null
  private skinCssKey: string | null = null
  private skinGeneration = 0
  private stateLoaded = false
  private initialLoadPromise: Promise<void> | null = null
  private persistTail: Promise<void> = Promise.resolve()
  private clipboardWriteGrant: { readonly webContentsId: number; readonly expiresAt: number } | null = null
  // Previous Dive's conversation URL while a new Dive is preparing. Navigating
  // back to it must not be remembered as the new Dive's conversation.
  private staleDiveUrl: string | null = null

  constructor(
    private readonly window: BrowserWindow,
    private readonly stateIo: HoloStateIo = DEFAULT_HOLO_STATE_IO
  ) {}

  async setSurface(visible: boolean, bounds?: HoloSurfaceBounds): Promise<HoloAddonStatus> {
    if (!visible) {
      this.detachView()
      return this.getStatus()
    }

    const view = this.ensureView()
    this.attachView(view)
    if (bounds) {
      const [contentWidth, contentHeight] = this.window.getContentSize()
      view.setBounds(clampHoloSurfaceBounds(bounds, contentWidth, contentHeight))
    }
    try {
      await this.ensureInitialLoad(view)
    } catch {
      if (this.webState !== 'unavailable') this.setWebFailure('unexpected_error')
    }
    return this.getStatus()
  }

  async prepareDive(): Promise<HoloDiveResult> {
    const view = this.ensureView()
    this.attachView(view)
    try {
      // Join any in-flight initial restore so the Dive load cannot race it.
      await this.ensureInitialLoad(view)
    } catch {
      // A failed restore must not block starting a new Dive.
    }

    const previousDiveUrl = this.currentDiveUrl
    const previousDiveSessionId = this.currentDiveSessionId
    this.staleDiveUrl = previousDiveUrl
    this.currentDiveSessionId = randomUUID()
    this.currentDiveUrl = null
    this.issue = null
    this.webState = 'loading'

    let prepared = false
    try {
      await view.webContents.loadURL(HOLO_CHATGPT_HOME_URL)
      const bootstrap = buildHoloBootstrapTemplate(localIsoDate())
      for (let attempt = 0; attempt < 16 && !prepared; attempt += 1) {
        try {
          prepared = Boolean(await view.webContents.executeJavaScript(
            bootstrapInsertionScript(bootstrap),
            true
          ))
        } catch {
          prepared = false
        }
        if (!prepared) await delay(125)
      }
    } catch {
      this.setWebFailure('web_load_failed', 'unavailable')
    }

    if (!prepared) {
      // Keep the saved Dive as current: a failed Dive must not lose the
      // conversation reference or overwrite the persisted state (D-003).
      this.currentDiveUrl = previousDiveUrl
      this.currentDiveSessionId = previousDiveSessionId
      try {
        // Persist the rollback before returning. Each persistence request owns
        // an immutable snapshot, so a previously queued write cannot observe
        // the temporary new-Dive state after it starts running.
        await this.persistState()
      } catch {
        // persistState owns the sticky persistence_issue. Web lifecycle events
        // must not erase this warning.
      }
      try {
        // Keep staleDiveUrl until the previous Conversation has finished
        // reloading. Its did-navigate event is part of rollback, not a new
        // Conversation transition, and must not enqueue another persistence
        // request after prepareDive() has reported rollback complete.
        if (previousDiveUrl && !view.webContents.isDestroyed()) {
          await view.webContents.loadURL(previousDiveUrl)
        }
      } catch {
        this.setWebFailure('web_load_failed', 'unavailable')
      } finally {
        this.staleDiveUrl = null
      }
      return {
        ...this.getStatus(),
        bootstrap_prepared: false
      }
    }

    try {
      // Persist the new Dive only after the bootstrap actually succeeded.
      await this.persistState()
    } catch {
      // Restart restore degrades, but the prepared Dive itself continues.
      // persistState keeps the failure sticky until a later save succeeds.
    }

    return {
      ...this.getStatus(),
      bootstrap_prepared: true
    }
  }

  async reload(): Promise<HoloAddonStatus> {
    const view = this.ensureView()
    this.attachView(view)
    await this.ensureInitialLoad(view)
    this.skinGeneration += 1
    this.issue = null
    this.webState = 'loading'
    await this.clearSkin(view.webContents, 'checking')
    view.webContents.reload()
    return this.getStatus()
  }

  async simulateSkinFallbackForQa(): Promise<HoloAddonStatus> {
    const view = this.ensureView()
    await this.ensureInitialLoad(view)
    this.skinGeneration += 1
    await this.clearSkin(view.webContents, 'fallback')
    return this.getStatus()
  }

  getStatus(): HoloAddonStatus {
    const currentUrl = this.view?.webContents.getURL() || null
    const diveState: HoloDiveState = this.currentDiveUrl
      ? 'current'
      : this.currentDiveSessionId ? 'preparing' : 'none'
    return {
      phase: deriveHoloAddonPhase(this.webState),
      visible: this.attached,
      loaded: this.webState === 'ready' && Boolean(currentUrl && isHoloAllowedNavigationUrl(currentUrl)),
      web_state: this.webState,
      dive_state: diveState,
      current_url: currentUrl,
      current_dive_url: this.currentDiveUrl,
      current_dive_session_id: this.currentDiveSessionId,
      title: this.view?.webContents.getTitle() || null,
      skin_mode: this.skinMode,
      issue: this.issue,
      persistence_issue: this.persistenceIssue
    }
  }

  dispose(): void {
    const view = this.view
    this.view = null
    this.attached = false
    this.skinGeneration += 1
    this.skinCssKey = null
    this.skinMode = 'checking'
    this.webState = 'idle'
    this.issue = null
    this.persistenceIssue = null
    this.clipboardWriteGrant = null
    this.staleDiveUrl = null
    if (!view) return

    // BrowserWindow teardown can destroy native Electron objects before JS cleanup runs.
    // Dispose defensively so shutdown never surfaces an uncaught "Object has been destroyed".
    try {
      if (!this.window.isDestroyed()) {
        this.window.contentView.removeChildView(view)
      }
    } catch {
      // The native view/window is already being torn down; there is nothing left to detach.
    }

    try {
      if (!view.webContents.isDestroyed()) {
        view.webContents.close()
      }
    } catch {
      // The child webContents may already have been destroyed by BrowserWindow teardown.
    }
  }

  private ensureView(): WebContentsView {
    if (this.view) return this.view

    const view = new WebContentsView({
      webPreferences: {
        partition: HOLO_SESSION_PARTITION,
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true
      }
    })
    view.setBackgroundColor('#00000000')

    const holoSession = view.webContents.session
    view.webContents.on('before-mouse-event', (_event, mouse) => {
      if (mouse.type === 'mouseDown') {
        // Any new press invalidates an older grant. A fresh one is armed only
        // by a completed primary-button gesture in this exact WebContents.
        this.clipboardWriteGrant = null
      }
      if (mouse.type === 'mouseUp' && mouse.button === 'left') {
        this.clipboardWriteGrant = {
          webContentsId: view.webContents.id,
          expiresAt: Date.now() + HOLO_CLIPBOARD_GESTURE_TTL_MS
        }
      }
    })
    holoSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
      const requestingUrl = details.requestingUrl || webContents.getURL()
      callback(this.consumeHoloWebPermission(webContents, permission, requestingUrl))
    })
    holoSession.setPermissionCheckHandler((webContents, permission, requestingOrigin, details) => {
      const requestingUrl = details.requestingUrl || requestingOrigin || webContents?.getURL()
      return this.consumeHoloWebPermission(webContents, permission, requestingUrl)
    })
    holoSession.setDisplayMediaRequestHandler((_request, callback) => {
      callback({})
    })
    holoSession.setDevicePermissionHandler(() => false)

    this.configureWebContentsSecurity(view.webContents)

    const rememberConversation = (_event: unknown, url: string): void => {
      if (!isHoloConversationUrl(url)) return
      if (this.staleDiveUrl !== null && url === this.staleDiveUrl) return
      this.staleDiveUrl = null
      this.currentDiveUrl = url
      void this.persistState().catch(() => undefined)
    }
    view.webContents.on('did-navigate', rememberConversation)
    view.webContents.on('did-navigate-in-page', rememberConversation)
    view.webContents.on('did-start-navigation', (event) => {
      if (event.isMainFrame) this.clipboardWriteGrant = null
      if (shouldResetHoloSkinForNavigation(event.isMainFrame, event.isSameDocument)) {
        this.issue = null
        this.webState = 'loading'
        this.resetSkinForNavigation(view.webContents)
      }
    })
    view.webContents.on('did-finish-load', () => {
      this.issue = null
      this.webState = 'ready'
      // Product-only cosmetic: hide ChatGPT's small accuracy disclaimer below
      // the composer without touching the composer or conversation contents.
      void view.webContents.executeJavaScript(buildHoloDisclaimerSuppressionScript(), true)
        .catch(() => undefined)
      void this.applySkin(view.webContents)
    })
    view.webContents.on('did-fail-load', (_event, errorCode, _description, _url, isMainFrame) => {
      if (!isMainFrame || errorCode === -3) return
      this.setWebFailure('web_load_failed', 'unavailable')
    })
    view.webContents.on('render-process-gone', () => {
      this.setWebFailure('unexpected_error')
    })
    // Focus is an observable fact: the renderer darkens the glass while
    // Master is pressed into the ChatGPT conversation (chat log behavior).
    view.webContents.on('focus', () => this.notifyWebFocus(true))
    view.webContents.on('blur', () => {
      this.clipboardWriteGrant = null
      this.notifyWebFocus(false)
    })

    this.view = view
    return view
  }

  private notifyWebFocus(focused: boolean): void {
    if (this.window.isDestroyed() || this.window.webContents.isDestroyed()) return
    this.window.webContents.send('holo:web-focus-changed', focused && this.attached)
  }

  private consumeHoloWebPermission(
    contents: WebContents | null,
    permission: string,
    requestingUrl?: string
  ): boolean {
    const grant = this.clipboardWriteGrant
    if (!grant || !contents || grant.webContentsId !== contents.id || grant.expiresAt < Date.now()) {
      if (grant?.expiresAt !== undefined && grant.expiresAt < Date.now()) {
        this.clipboardWriteGrant = null
      }
      return false
    }
    if (!shouldAllowHoloWebPermission(permission, requestingUrl, true)) return false
    this.clipboardWriteGrant = null
    return true
  }

  private configureWebContentsSecurity(contents: WebContents): void {
    contents.on('will-navigate', (event, navigationUrl) => {
      if (isHoloAllowedNavigationUrl(navigationUrl)) return
      event.preventDefault()
    })

    contents.setWindowOpenHandler(({ url }) => {
      if (url === 'about:blank' || isHoloAllowedNavigationUrl(url)) {
        return {
          action: 'allow',
          overrideBrowserWindowOptions: {
            webPreferences: {
              partition: HOLO_SESSION_PARTITION,
              nodeIntegration: false,
              contextIsolation: true,
              sandbox: true,
              webSecurity: true
            }
          }
        }
      }

      if (isSafeHoloExternalUrl(url)) {
        setImmediate(() => {
          void shell.openExternal(url).catch(() => undefined)
        })
      }
      return { action: 'deny' }
    })

    contents.on('did-create-window', (childWindow) => {
      this.configureWebContentsSecurity(childWindow.webContents)
    })
  }

  private attachView(view: WebContentsView): void {
    if (this.attached) return
    this.window.contentView.addChildView(view)
    this.attached = true
  }

  private detachView(): void {
    if (!this.view || !this.attached) return
    const view = this.view
    this.attached = false
    this.notifyWebFocus(false)

    if (this.window.isDestroyed()) return
    try {
      this.window.contentView.removeChildView(view)
    } catch (error) {
      if (!this.window.isDestroyed()) throw error
    }
  }

  private async ensureInitialLoad(view: WebContentsView): Promise<void> {
    if (isHoloAllowedNavigationUrl(view.webContents.getURL())) return
    if (!this.initialLoadPromise) {
      this.initialLoadPromise = (async () => {
        await this.loadState()
        if (isHoloAllowedNavigationUrl(view.webContents.getURL())) return
        this.issue = null
        this.webState = 'loading'
        await view.webContents.loadURL(this.currentDiveUrl ?? HOLO_CHATGPT_HOME_URL)
      })().finally(() => {
        this.initialLoadPromise = null
      })
    }
    await this.initialLoadPromise
  }

  private async waitForHealthySkinProbe(
    contents: WebContents,
    generation: number
  ): Promise<boolean> {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (generation !== this.skinGeneration || contents.isDestroyed()) return false
      try {
        const probe = await contents.executeJavaScript(buildHoloSkinProbeScript(), true)
        if (isHealthyHoloSkinProbe(probe)) return true
      } catch {
        // ChatGPT is still constructing the SPA. Retry only within the bounded Gate 0 window.
      }
      if (attempt < 19) await delay(250)
    }
    return false
  }

  private async applySkin(contents: WebContents): Promise<void> {
    const generation = ++this.skinGeneration
    await this.clearSkin(contents, 'checking')
    if (generation !== this.skinGeneration || contents.isDestroyed()) return

    try {
      const healthy = await this.waitForHealthySkinProbe(contents, generation)
      if (generation !== this.skinGeneration || contents.isDestroyed()) return
      if (!healthy) {
        this.skinMode = 'fallback'
        return
      }

      const cssKey = await contents.insertCSS(HOLO_SKIN_CSS, { cssOrigin: 'user' })
      if (generation !== this.skinGeneration || contents.isDestroyed()) {
        if (!contents.isDestroyed()) await contents.removeInsertedCSS(cssKey).catch(() => undefined)
        return
      }
      this.skinCssKey = cssKey
      await contents.executeJavaScript(buildHoloSkinMarkerScript(true), true)
      const postflight = await contents.executeJavaScript(buildHoloSkinProbeScript(), true)
      const cssApplied = await contents.executeJavaScript(buildHoloSkinAppliedProbeScript(), true)
      if (generation !== this.skinGeneration || contents.isDestroyed()) return
      if (!isHealthyHoloSkinProbe(postflight) || cssApplied !== true) {
        await this.clearSkin(contents, 'fallback')
        return
      }
      this.skinMode = 'applied'
    } catch {
      if (generation === this.skinGeneration && !contents.isDestroyed()) {
        await this.clearSkin(contents, 'fallback')
      }
    }
  }

  private resetSkinForNavigation(contents: WebContents): void {
    this.skinGeneration += 1
    const cssKey = this.skinCssKey
    this.skinCssKey = null
    this.skinMode = 'checking'
    if (cssKey && !contents.isDestroyed()) {
      void contents.removeInsertedCSS(cssKey).catch(() => undefined)
    }
  }

  private async clearSkin(contents: WebContents, nextMode: HoloSkinMode): Promise<void> {
    const cssKey = this.skinCssKey
    this.skinCssKey = null
    this.skinMode = nextMode
    if (contents.isDestroyed()) return

    try {
      await contents.executeJavaScript(buildHoloSkinMarkerScript(false), true)
    } catch {
      // Skin cleanup is fail-open. A broken remote DOM must not break ChatGPT itself.
    }
    if (cssKey) {
      await contents.removeInsertedCSS(cssKey).catch(() => undefined)
    }
  }

  private getStatePath(): string {
    return join(getNiraiRoot(), 'runtime', 'holo', 'state.json')
  }

  private async loadState(): Promise<void> {
    if (this.stateLoaded) return
    this.stateLoaded = true
    try {
      const parsed = JSON.parse(await this.stateIo.readText(this.getStatePath())) as Partial<PersistedHoloState>
      if (typeof parsed.current_dive_url === 'string' && isHoloConversationUrl(parsed.current_dive_url)) {
        this.currentDiveUrl = parsed.current_dive_url
      }
      if (typeof parsed.current_dive_session_id === 'string' && parsed.current_dive_session_id.trim()) {
        this.currentDiveSessionId = parsed.current_dive_session_id
      }
    } catch {
      this.currentDiveUrl = null
      this.currentDiveSessionId = null
    }
  }

  private persistState(): Promise<void> {
    // Capture state at request time. A queued persistence operation must never
    // read mutable Dive fields later after prepareDive() has changed them.
    const state: PersistedHoloState = {
      current_dive_url: this.currentDiveUrl,
      current_dive_session_id: this.currentDiveSessionId,
      updated_at: new Date().toISOString()
    }
    const next = this.persistTail.then(async () => {
      const path = this.getStatePath()
      const temporaryPath = `${path}.${randomUUID()}.tmp`
      try {
        await this.stateIo.ensureDirectory(dirname(path))
        await this.stateIo.writeText(temporaryPath, `${JSON.stringify(state, null, 2)}\n`)
        await replaceFileAtomically(temporaryPath, path, this.stateIo.renameFile)
        this.persistenceIssue = null
      } catch (error) {
        this.persistenceIssue = 'state_persistence_failed'
        throw error
      } finally {
        await this.stateIo.removeFile(temporaryPath).catch(() => undefined)
      }
    })
    this.persistTail = next.catch(() => undefined)
    return next
  }

  private setWebFailure(
    issue: NonNullable<HoloAddonStatus['issue']>,
    state: Extract<HoloWebState, 'unavailable' | 'error'> = 'error'
  ): void {
    this.issue = issue
    this.webState = state
  }
}
