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
  buildHoloAutoResumePrompt,
  buildHoloAutoResumeSubmissionScript,
  buildHoloBootstrapTemplate,
  buildHoloDisclaimerSuppressionScript,
  buildHoloGenerationBusyProbeScript,
  buildHoloScrollStabilityScript,
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
  holoAutoResumeTriggerKey,
  isHoloAutoResumeTrigger,
  type HoloAddonPhase,
  type HoloAutoResumeSubmitStatus,
  type HoloAutoResumeTrigger,
  type HoloSkinMode,
  type HoloSurfaceBounds,
  type HoloWebState
} from './holoWeb'

interface PersistedHoloState {
  readonly current_dive_url: string | null
  readonly current_dive_session_id: string | null
  readonly known_dive_urls?: Readonly<Record<string, string>>
  readonly pending_auto_resume?: readonly HoloAutoResumeTrigger[]
  readonly processed_auto_resume_keys?: readonly string[]
  readonly updated_at: string
}

interface PersistedHoloWorkflowLease {
  readonly version: 1
  readonly workflow_id: string
  readonly dive_session_id: string
  readonly conversation_url?: string
  readonly label: string
  readonly state: 'active' | 'completed'
  readonly started_at: string
  readonly updated_at: string
  readonly completed_at?: string
}

interface PersistedHoloTaskOwner {
  readonly version: 1
  readonly task_id: string
  readonly dive_session_id: string
  readonly conversation_url: string
  readonly created_at: string
}

export interface HoloAutoResumeEnqueueResult {
  readonly accepted: boolean
  readonly duplicate: boolean
  readonly pending_count: number
}

const HOLO_AUTO_RESUME_QUEUE_LIMIT = 32
const HOLO_AUTO_RESUME_PROCESSED_LIMIT = 128
const HOLO_AUTO_RESUME_RETRY_MIN_MS = 1000
const HOLO_AUTO_RESUME_RETRY_MAX_MS = 15_000

interface HoloWorkflowWatchdogTiming {
  readonly intervalMs: number
  readonly staleMs: number
  readonly idleGraceMs: number
}

const DEFAULT_HOLO_WORKFLOW_WATCHDOG_TIMING: HoloWorkflowWatchdogTiming = {
  intervalMs: 2_000,
  staleMs: 30_000,
  idleGraceMs: 5_000
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
  private stateLoadPromise: Promise<void> | null = null
  private disposed = false
  private initialLoadPromise: Promise<void> | null = null
  private persistTail: Promise<void> = Promise.resolve()
  private clipboardWriteGrant: { readonly webContentsId: number; readonly expiresAt: number } | null = null
  private autoResumeQueue: HoloAutoResumeTrigger[] = []
  private processedAutoResumeKeys: string[] = []
  private autoResumeEnqueueTail: Promise<void> = Promise.resolve()
  private autoResumeDrainRunning = false
  private autoResumeRetryTimer: ReturnType<typeof setTimeout> | null = null
  private autoResumeRetryMs = HOLO_AUTO_RESUME_RETRY_MIN_MS
  private workflowWatchdogTimer: ReturnType<typeof setTimeout> | null = null
  private workflowWatchdogEnabled = false
  private workflowWatchdogRunning = false
  private workflowIdleSince: number | null = null
  private workflowObservedRevision: string | null = null
  private workflowTriggeredRevision: string | null = null
  private knownDiveUrls = new Map<string, string>()
  private transientAutoResumeUrl: string | null = null
  // Previous Dive's conversation URL while a new Dive is preparing. Navigating
  // back to it must not be remembered as the new Dive's conversation.
  private staleDiveUrl: string | null = null

  constructor(
    private readonly window: BrowserWindow,
    private readonly stateIo: HoloStateIo = DEFAULT_HOLO_STATE_IO,
    private readonly workflowWatchdogTiming: HoloWorkflowWatchdogTiming = DEFAULT_HOLO_WORKFLOW_WATCHDOG_TIMING
  ) {}

  async resumePendingAutoResume(): Promise<number> {
    await this.loadState()
    if (this.pruneObsoleteTerminalAutoResume() > 0) {
      await this.persistState().catch(() => undefined)
    }
    this.workflowWatchdogEnabled = true
    this.scheduleWorkflowWatchdog(0)
    if (this.autoResumeQueue.length > 0) this.scheduleAutoResumeDrain(0)
    return this.autoResumeQueue.length
  }

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
      await this.ensureSkinApplied(view.webContents)
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
      const bootstrap = buildHoloBootstrapTemplate(localIsoDate(), this.currentDiveSessionId ?? undefined)
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

  async enqueueAutoResume(trigger: HoloAutoResumeTrigger): Promise<HoloAutoResumeEnqueueResult> {
    const operation = this.autoResumeEnqueueTail.then(() => this.enqueueAutoResumeSerialized(trigger))
    this.autoResumeEnqueueTail = operation.then(() => undefined, () => undefined)
    return operation
  }

  private async enqueueAutoResumeSerialized(trigger: HoloAutoResumeTrigger): Promise<HoloAutoResumeEnqueueResult> {
    await this.loadState()
    const ownedTrigger = await this.resolveAutoResumeOwner(trigger)
    if (!ownedTrigger) {
      return {
        accepted: false,
        duplicate: false,
        pending_count: this.autoResumeQueue.length
      }
    }
    const key = holoAutoResumeTriggerKey(ownedTrigger)
    if (this.isObsoleteTerminalAutoResume(ownedTrigger)) {
      const previousKeys = this.processedAutoResumeKeys
      this.processedAutoResumeKeys = [
        ...this.processedAutoResumeKeys.filter((candidate) => candidate !== key),
        key
      ].slice(-HOLO_AUTO_RESUME_PROCESSED_LIMIT)
      try {
        await this.persistState()
      } catch {
        // A dropped obsolete trigger is acknowledged only after its dedupe key
        // is durable. Preserve the renderer/Core retry on a failed write.
        this.processedAutoResumeKeys = [...new Set([
          ...previousKeys,
          ...this.processedAutoResumeKeys.filter((candidate) => candidate !== key)
        ])].slice(-HOLO_AUTO_RESUME_PROCESSED_LIMIT)
        return { accepted: false, duplicate: false, pending_count: this.autoResumeQueue.length }
      }
      return {
        accepted: true,
        duplicate: false,
        pending_count: this.autoResumeQueue.length
      }
    }
    const duplicate = this.processedAutoResumeKeys.includes(key)
      || this.autoResumeQueue.some((queued) => holoAutoResumeTriggerKey(queued) === key)
    if (duplicate) {
      return {
        accepted: false,
        duplicate: true,
        pending_count: this.autoResumeQueue.length
      }
    }
    if (this.autoResumeQueue.length >= HOLO_AUTO_RESUME_QUEUE_LIMIT) {
      // Do not silently evict an older unfinished continuation. A full queue is
      // an observable backpressure condition and the renderer may retry later.
      return {
        accepted: false,
        duplicate: false,
        pending_count: this.autoResumeQueue.length
      }
    }
    this.autoResumeQueue.push({ ...ownedTrigger })
    try {
      // `accepted` is a durable acknowledgement. The renderer keeps its own
      // persisted outbox entry until this write succeeds, so queue pressure,
      // transient disk faults and World restarts cannot silently lose a trigger.
      await this.persistState()
    } catch {
      const index = this.autoResumeQueue.findIndex(
        (candidate) => holoAutoResumeTriggerKey(candidate) === key
      )
      if (index >= 0) this.autoResumeQueue.splice(index, 1)
      return {
        accepted: false,
        duplicate: false,
        pending_count: this.autoResumeQueue.length
      }
    }
    this.scheduleAutoResumeDrain(0)
    return {
      accepted: true,
      duplicate: false,
      pending_count: this.autoResumeQueue.length
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
    this.disposed = true
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
    this.transientAutoResumeUrl = null
    if (this.autoResumeRetryTimer !== null) {
      clearTimeout(this.autoResumeRetryTimer)
      this.autoResumeRetryTimer = null
    }
    this.workflowWatchdogEnabled = false
    if (this.workflowWatchdogTimer !== null) {
      clearTimeout(this.workflowWatchdogTimer)
      this.workflowWatchdogTimer = null
    }
    this.workflowIdleSince = null
    this.workflowObservedRevision = null
    this.workflowTriggeredRevision = null
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
      if (this.transientAutoResumeUrl !== null && url === this.transientAutoResumeUrl) return
      if (this.staleDiveUrl !== null && url === this.staleDiveUrl) return
      this.staleDiveUrl = null

      // A Dive Session owns exactly one ChatGPT Conversation. Navigating the
      // embedded ChatGPT UI to another Conversation must never rebind that Dive,
      // otherwise an existing workflow lease can later auto-resume into the
      // newly viewed Conversation instead of its original owner.
      if (this.currentDiveSessionId) {
        const boundUrl = this.knownDiveUrls.get(this.currentDiveSessionId) ?? this.currentDiveUrl
        if (boundUrl && boundUrl !== url) {
          if (this.webState === 'ready') void this.ensureSkinApplied(view.webContents)
          return
        }
      }

      this.currentDiveUrl = url
      if (this.currentDiveSessionId) this.knownDiveUrls.set(this.currentDiveSessionId, url)
      this.pruneObsoleteTerminalAutoResume()
      void this.persistState().catch(() => undefined)
      if (!this.autoResumeDrainRunning && !this.workflowWatchdogRunning) this.scheduleAutoResumeDrain(0)
      if (this.webState === 'ready') void this.ensureSkinApplied(view.webContents)
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
      void view.webContents.executeJavaScript(buildHoloScrollStabilityScript(), true)
        .catch(() => undefined)
      void this.applySkin(view.webContents)
      if (!this.autoResumeDrainRunning && !this.workflowWatchdogRunning) this.scheduleAutoResumeDrain(0)
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

  private async ensureSkinApplied(contents: WebContents): Promise<void> {
    if (contents.isDestroyed() || this.webState !== 'ready') return
    try {
      const applied = await contents.executeJavaScript(buildHoloSkinAppliedProbeScript(), true)
      if (applied === true) {
        this.skinMode = 'applied'
        return
      }
    } catch {
      // Remote SPA state can change between probes. Re-apply below.
    }
    await this.applySkin(contents)
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

  private scheduleWorkflowWatchdog(delayMs?: number): void {
    if (this.disposed || !this.workflowWatchdogEnabled || this.workflowWatchdogTimer !== null) return
    const effectiveDelayMs = delayMs ?? this.workflowWatchdogTiming.intervalMs
    this.workflowWatchdogTimer = setTimeout(() => {
      this.workflowWatchdogTimer = null
      void this.runWorkflowWatchdog()
    }, Math.max(0, effectiveDelayMs))
  }

  private resetWorkflowWatchdogObservation(): void {
    this.workflowIdleSince = null
    this.workflowObservedRevision = null
    this.workflowTriggeredRevision = null
  }

  private async readWorkflowLease(): Promise<PersistedHoloWorkflowLease | null> {
    try {
      const parsed = JSON.parse(await this.stateIo.readText(this.getWorkflowStatePath())) as Partial<PersistedHoloWorkflowLease>
      if (
        parsed.version !== 1
        || typeof parsed.workflow_id !== 'string'
        || !parsed.workflow_id.trim()
        || typeof parsed.dive_session_id !== 'string'
        || !parsed.dive_session_id.trim()
        || (parsed.conversation_url !== undefined && (
          typeof parsed.conversation_url !== 'string'
          || !isHoloConversationUrl(parsed.conversation_url)
        ))
        || !['active', 'completed'].includes(String(parsed.state))
        || typeof parsed.started_at !== 'string'
        || typeof parsed.updated_at !== 'string'
      ) return null
      return parsed as PersistedHoloWorkflowLease
    } catch {
      return null
    }
  }

  private async runWorkflowWatchdog(): Promise<void> {
    if (this.disposed || this.workflowWatchdogRunning) return
    if (this.autoResumeDrainRunning) {
      this.scheduleWorkflowWatchdog()
      return
    }
    this.workflowWatchdogRunning = true
    try {
      await this.loadState()
      const lease = await this.readWorkflowLease()
      if (
        !lease
        || lease.state !== 'active'
        || !lease.conversation_url
      ) {
        this.resetWorkflowWatchdogObservation()
        return
      }
      const knownOwnerUrl = this.knownDiveUrls.get(lease.dive_session_id)
        ?? (this.currentDiveSessionId === lease.dive_session_id ? this.currentDiveUrl : null)
      if (knownOwnerUrl && knownOwnerUrl !== lease.conversation_url) {
        // Ownership is inconsistent. Fail closed instead of guessing which
        // Conversation should receive an automatic continuation.
        this.resetWorkflowWatchdogObservation()
        return
      }

      const revision = `${lease.workflow_id}:${lease.updated_at}`
      if (revision !== this.workflowObservedRevision) {
        this.workflowObservedRevision = revision
        this.workflowIdleSince = null
        if (this.workflowTriggeredRevision !== revision) this.workflowTriggeredRevision = null
      }
      if (this.workflowTriggeredRevision === revision) return

      const updatedAtMs = Date.parse(lease.updated_at)
      if (!Number.isFinite(updatedAtMs) || Date.now() - updatedAtMs < this.workflowWatchdogTiming.staleMs) {
        this.workflowIdleSince = null
        return
      }

      if (this.disposed) return
      const view = this.ensureView()
      await this.ensureInitialLoad(view)
      if (this.webState !== 'ready') {
        this.workflowIdleSince = null
        return
      }
      const targetUrl = lease.conversation_url
      const restoreUrl = this.currentDiveUrl
      let navigatedForProbe = false
      if (view.webContents.getURL() !== targetUrl) {
        if (this.attached) {
          this.workflowIdleSince = null
          return
        }
        try {
          this.transientAutoResumeUrl = targetUrl
          await view.webContents.loadURL(targetUrl)
          navigatedForProbe = true
        } catch {
          this.transientAutoResumeUrl = null
          this.workflowIdleSince = null
          return
        }
      }

      let busy = true
      try {
        busy = Boolean(await view.webContents.executeJavaScript(
          buildHoloGenerationBusyProbeScript(),
          true
        ))
      } catch {
        return
      } finally {
        if (navigatedForProbe && restoreUrl && restoreUrl !== targetUrl) {
          try {
            this.transientAutoResumeUrl = restoreUrl
            await view.webContents.loadURL(restoreUrl)
          } catch {
            // The durable current Dive remains unchanged; a later surface open restores it.
          }
        }
        this.transientAutoResumeUrl = null
      }
      if (busy) {
        this.workflowIdleSince = null
        return
      }

      const now = Date.now()
      if (this.workflowIdleSince === null) {
        this.workflowIdleSince = now
        return
      }
      if (now - this.workflowIdleSince < this.workflowWatchdogTiming.idleGraceMs) return

      const result = await this.enqueueAutoResume({
        task_id: `WF-${lease.workflow_id}`,
        reason: 'workflow_stalled',
        request_id: lease.updated_at,
        dive_session_id: lease.dive_session_id,
        conversation_url: lease.conversation_url
      })
      if (result.accepted || result.duplicate) {
        this.workflowTriggeredRevision = revision
      }
    } catch {
      // A navigation or shutdown can invalidate an in-flight probe.
      this.workflowIdleSince = null
    } finally {
      this.workflowWatchdogRunning = false
      this.scheduleWorkflowWatchdog()
    }
  }

  private scheduleAutoResumeDrain(delayMs: number): void {
    if (this.disposed || this.autoResumeRetryTimer !== null) return
    this.autoResumeRetryTimer = setTimeout(() => {
      this.autoResumeRetryTimer = null
      void this.drainAutoResumeQueue()
    }, Math.max(0, delayMs))
  }

  private scheduleAutoResumeRetry(status: HoloAutoResumeSubmitStatus): void {
    // A Master draft must never be overwritten. Retry gently while the draft
    // remains, just as we do while ChatGPT is generating a previous turn.
    const delayMs = status === 'draft_present'
      ? Math.max(5000, this.autoResumeRetryMs)
      : this.autoResumeRetryMs
    this.autoResumeRetryMs = Math.min(
      Math.max(HOLO_AUTO_RESUME_RETRY_MIN_MS, this.autoResumeRetryMs * 2),
      HOLO_AUTO_RESUME_RETRY_MAX_MS
    )
    this.scheduleAutoResumeDrain(delayMs)
  }

  private async drainAutoResumeQueue(): Promise<void> {
    if (this.disposed || this.autoResumeDrainRunning || this.autoResumeQueue.length === 0) return
    if (this.workflowWatchdogRunning) {
      this.scheduleAutoResumeDrain(HOLO_AUTO_RESUME_RETRY_MIN_MS)
      return
    }
    this.autoResumeDrainRunning = true
    try {
      await this.loadState()
      let queueIndex = 0
      let queuedTrigger = this.autoResumeQueue[0]
      let trigger = queuedTrigger ? await this.resolveAutoResumeOwner(queuedTrigger) : null

      if (this.attached) {
        const visibleUrl = this.view?.webContents.getURL() || this.currentDiveUrl
        if (!visibleUrl) {
          this.scheduleAutoResumeRetry('not_ready')
          return
        }
        queueIndex = -1
        for (let index = 0; index < this.autoResumeQueue.length; index += 1) {
          const candidate = this.autoResumeQueue[index]
          const resolved = await this.resolveAutoResumeOwner(candidate)
          if (resolved?.conversation_url === visibleUrl) {
            queueIndex = index
            queuedTrigger = candidate
            trigger = resolved
            break
          }
        }
        if (queueIndex < 0 || !queuedTrigger || !trigger) {
          // Keep older triggers for hidden/background delivery, but never let
          // them block a continuation for the Conversation Master is viewing.
          this.scheduleAutoResumeRetry('not_ready')
          return
        }
      }

      if (!queuedTrigger) return
      if (!trigger) {
        // Unknown ownership must fail closed. Delivering to Current Dive would
        // turn an unrelated Task or a legacy stale trigger into cross-Conversation Resume.
        this.autoResumeQueue.splice(queueIndex, 1)
        await this.persistState().catch(() => undefined)
        if (this.autoResumeQueue.length > 0) {
          this.scheduleAutoResumeDrain(HOLO_AUTO_RESUME_RETRY_MIN_MS)
        }
        return
      }
      if (
        trigger.dive_session_id !== queuedTrigger.dive_session_id
        || trigger.conversation_url !== queuedTrigger.conversation_url
      ) {
        this.autoResumeQueue[queueIndex] = trigger
        await this.persistState().catch(() => undefined)
      }
      const targetUrl = trigger.conversation_url && isHoloConversationUrl(trigger.conversation_url)
        ? trigger.conversation_url
        : null
      if (!targetUrl) return

      if (this.disposed) return
      const view = this.ensureView()
      await this.ensureInitialLoad(view)
      if (this.webState !== 'ready') {
        this.scheduleAutoResumeRetry('not_ready')
        return
      }

      const currentUrl = view.webContents.getURL()
      const restoreUrl = this.currentDiveUrl
      const isBackgroundTarget = targetUrl !== restoreUrl
      if (currentUrl !== targetUrl) {
        // Never yank a visible Holo surface into another Conversation. Hidden
        // background delivery may temporarily navigate to the trigger owner.
        if (this.attached) {
          this.scheduleAutoResumeRetry('not_ready')
          return
        }
        try {
          if (isBackgroundTarget) this.transientAutoResumeUrl = targetUrl
          await view.webContents.loadURL(targetUrl)
        } catch {
          this.transientAutoResumeUrl = null
          this.scheduleAutoResumeRetry('not_ready')
          return
        }
      }

      let rawResult: unknown
      try {
        if (this.disposed || view.webContents.getURL() !== targetUrl) return
        rawResult = await view.webContents.executeJavaScript(
          buildHoloAutoResumeSubmissionScript(
            buildHoloAutoResumePrompt(trigger),
            holoAutoResumeTriggerKey(trigger),
            targetUrl
          ),
          true
        )
      } catch {
        this.scheduleAutoResumeRetry('not_ready')
        return
      } finally {
        if (!this.disposed && isBackgroundTarget && restoreUrl
          && this.currentDiveUrl === restoreUrl && view.webContents.getURL() === targetUrl) {
          try {
            this.transientAutoResumeUrl = restoreUrl
            await view.webContents.loadURL(restoreUrl)
          } catch {
            // Keep the durable Current Dive intact if restoration fails.
          }
        }
        this.transientAutoResumeUrl = null
      }
      const status = (
        rawResult
        && typeof rawResult === 'object'
        && 'status' in rawResult
        && ['submitted', 'busy', 'draft_present', 'not_ready'].includes(String(rawResult.status))
      ) ? String(rawResult.status) as HoloAutoResumeSubmitStatus : 'not_ready'

      if (status !== 'submitted') {
        this.transientAutoResumeUrl = null
        this.scheduleAutoResumeRetry(status)
        return
      }

      const key = holoAutoResumeTriggerKey(trigger)
      this.autoResumeQueue.splice(queueIndex, 1)
      this.processedAutoResumeKeys = [
        ...this.processedAutoResumeKeys.filter((candidate) => candidate !== key),
        key
      ].slice(-HOLO_AUTO_RESUME_PROCESSED_LIMIT)
      this.autoResumeRetryMs = HOLO_AUTO_RESUME_RETRY_MIN_MS
      await this.persistState().catch(() => undefined)

      // ChatGPT is now generating this continuation. Keep subsequent events
      // queued and retry after a small delay rather than submitting overlapping
      // turns into the same Conversation.
      if (this.autoResumeQueue.length > 0) {
        this.scheduleAutoResumeDrain(HOLO_AUTO_RESUME_RETRY_MIN_MS)
      }
    } catch {
      this.scheduleAutoResumeRetry('not_ready')
    } finally {
      this.autoResumeDrainRunning = false
    }
  }

  private isObsoleteTerminalAutoResume(trigger: HoloAutoResumeTrigger): boolean {
    if (!this.currentDiveSessionId || !this.currentDiveUrl) return false
    if (trigger.reason !== 'done' && trigger.reason !== 'cancelled') return false
    const ownerDive = trigger.dive_session_id?.trim()
    return Boolean(ownerDive && ownerDive !== this.currentDiveSessionId)
  }

  private pruneObsoleteTerminalAutoResume(): number {
    if (!this.currentDiveSessionId || !this.currentDiveUrl || this.autoResumeQueue.length === 0) return 0
    const before = this.autoResumeQueue.length
    const obsoleteKeys: string[] = []
    this.autoResumeQueue = this.autoResumeQueue.filter((trigger) => {
      if (!this.isObsoleteTerminalAutoResume(trigger)) return true
      obsoleteKeys.push(holoAutoResumeTriggerKey(trigger))
      return false
    })
    if (obsoleteKeys.length > 0) {
      const obsolete = new Set(obsoleteKeys)
      this.processedAutoResumeKeys = [
        ...this.processedAutoResumeKeys.filter((key) => !obsolete.has(key)),
        ...obsoleteKeys
      ].slice(-HOLO_AUTO_RESUME_PROCESSED_LIMIT)
    }
    return before - this.autoResumeQueue.length
  }

  private getStatePath(): string {
    return join(getNiraiRoot(), 'runtime', 'holo', 'state.json')
  }

  private getWorkflowStatePath(): string {
    return join(getNiraiRoot(), 'runtime', 'holo', 'workflow.json')
  }

  private getTaskOwnerPath(taskId: string): string | null {
    if (!/^(?:T|HR|IA)-[A-Za-z0-9-]+$/.test(taskId)) return null
    return join(getNiraiRoot(), 'runtime', 'holo', 'task_owners', `${taskId}.json`)
  }

  private async readTaskOwner(taskId: string): Promise<PersistedHoloTaskOwner | null> {
    const path = this.getTaskOwnerPath(taskId)
    if (!path) return null
    try {
      const parsed = JSON.parse(await this.stateIo.readText(path)) as Partial<PersistedHoloTaskOwner>
      if (
        parsed.version !== 1
        || parsed.task_id !== taskId
        || typeof parsed.dive_session_id !== 'string'
        || !parsed.dive_session_id.trim()
        || typeof parsed.conversation_url !== 'string'
        || !isHoloConversationUrl(parsed.conversation_url)
        || typeof parsed.created_at !== 'string'
      ) return null
      return parsed as PersistedHoloTaskOwner
    } catch {
      return null
    }
  }

  private async resolveAutoResumeOwner(trigger: HoloAutoResumeTrigger): Promise<HoloAutoResumeTrigger | null> {
    if (trigger.reason === 'workflow_stalled') {
      const lease = await this.readWorkflowLease()
      if (!lease || lease.state !== 'active'
        || trigger.task_id !== `WF-${lease.workflow_id}`
        || trigger.request_id !== lease.updated_at
        || trigger.dive_session_id !== lease.dive_session_id
        || trigger.conversation_url !== lease.conversation_url) return null
    }
    if (
      typeof trigger.dive_session_id === 'string'
      && trigger.dive_session_id.trim()
      && typeof trigger.conversation_url === 'string'
      && isHoloConversationUrl(trigger.conversation_url)
    ) {
      return {
        ...trigger,
        dive_session_id: trigger.dive_session_id.trim(),
        conversation_url: trigger.conversation_url
      }
    }
    const owner = await this.readTaskOwner(trigger.task_id)
    if (!owner) return null
    return {
      ...trigger,
      dive_session_id: owner.dive_session_id,
      conversation_url: owner.conversation_url
    }
  }

  private loadState(): Promise<void> {
    this.stateLoadPromise ??= this.readState()
    return this.stateLoadPromise
  }

  private async readState(): Promise<void> {
    try {
      const parsed = JSON.parse(await this.stateIo.readText(this.getStatePath())) as Partial<PersistedHoloState>
      if (typeof parsed.current_dive_url === 'string' && isHoloConversationUrl(parsed.current_dive_url)) {
        this.currentDiveUrl = parsed.current_dive_url
      }
      if (typeof parsed.current_dive_session_id === 'string' && parsed.current_dive_session_id.trim()) {
        this.currentDiveSessionId = parsed.current_dive_session_id
      }
      if (parsed.known_dive_urls && typeof parsed.known_dive_urls === 'object') {
        this.knownDiveUrls = new Map(Object.entries(parsed.known_dive_urls)
          .filter(([sessionId, url]) => sessionId.trim().length > 0 && typeof url === 'string' && isHoloConversationUrl(url)))
      }
      if (this.currentDiveSessionId && this.currentDiveUrl) {
        this.knownDiveUrls.set(this.currentDiveSessionId, this.currentDiveUrl)
      }
      if (Array.isArray(parsed.pending_auto_resume)) {
        this.autoResumeQueue = parsed.pending_auto_resume
          .filter(isHoloAutoResumeTrigger)
          .filter((trigger) => trigger.reason !== 'workflow_stalled' || (
            typeof trigger.dive_session_id === 'string'
            && trigger.dive_session_id.trim().length > 0
            && typeof trigger.conversation_url === 'string'
            && isHoloConversationUrl(trigger.conversation_url)
          ))
          .slice(-HOLO_AUTO_RESUME_QUEUE_LIMIT)
      }
      if (Array.isArray(parsed.processed_auto_resume_keys)) {
        this.processedAutoResumeKeys = parsed.processed_auto_resume_keys
          .filter((key): key is string => typeof key === 'string' && key.length > 0)
          .slice(-HOLO_AUTO_RESUME_PROCESSED_LIMIT)
      }
    } catch {
      this.currentDiveUrl = null
      this.currentDiveSessionId = null
      this.knownDiveUrls = new Map()
      this.autoResumeQueue = []
      this.processedAutoResumeKeys = []
    }
  }

  private persistState(): Promise<void> {
    // Capture state at request time. A queued persistence operation must never
    // read mutable Dive fields later after prepareDive() has changed them.
    const state: PersistedHoloState = {
      current_dive_url: this.currentDiveUrl,
      current_dive_session_id: this.currentDiveSessionId,
      known_dive_urls: Object.fromEntries(this.knownDiveUrls),
      pending_auto_resume: this.autoResumeQueue.map((trigger) => ({ ...trigger })),
      processed_auto_resume_keys: [...this.processedAutoResumeKeys],
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
