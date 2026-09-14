import { BrowserWindow, WebContentsView, ipcMain, shell, type IpcMainEvent, type WebContents } from 'electron'
import { execFile } from 'node:child_process'
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
  buildHoloAutoResumeCancellationScript,
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
  isSameHoloConversationUrl,
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
  readonly stopped_conversations?: readonly HoloConversationStop[]
  readonly current_dive_url: string | null
  readonly current_dive_session_id: string | null
  readonly known_dive_urls?: Readonly<Record<string, string>>
  readonly pending_auto_resume?: readonly HoloAutoResumeTrigger[]
  readonly processed_auto_resume_keys?: readonly string[]
  readonly cancelled_auto_resume_task_ids?: readonly string[]
  readonly updated_at: string
}

interface HoloConversationStop {
  readonly conversation_url: string
  readonly stopped_at: string
  readonly pending: boolean
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
  readonly workflow_id?: string
}

export interface HoloAutoResumeEnqueueResult {
  readonly accepted: boolean
  readonly duplicate: boolean
  readonly discarded?: boolean
  readonly pending_count: number
}

export interface HoloTaskManagementState {
  readonly tasks: readonly {
    readonly task_id: string
    readonly title: string
    readonly state: string
    readonly kind: 'task' | 'review' | 'workflow'
    readonly pending_count: number
  }[]
  readonly cancelled_task_ids: readonly string[]
}

const HOLO_AUTO_RESUME_QUEUE_LIMIT = 32
const HOLO_AUTO_RESUME_PROCESSED_LIMIT = 128
const HOLO_AUTO_RESUME_RETRY_MIN_MS = 1000

function isSafeHoloTaskId(taskId: string): boolean {
  return taskId.length > 0
    && taskId.length <= 256
    && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(taskId)
}

const HOLO_AUTO_RESUME_RETRY_MAX_MS = 15_000
const HOLO_AUTO_RESUME_LOAD_TIMEOUT_MS = 10_000
const HOLO_AUTO_RESUME_SUBMIT_TIMEOUT_MS = 12_000

interface HoloWorkflowWatchdogTiming {
  readonly intervalMs: number
  readonly staleMs: number
  readonly idleGraceMs: number
  readonly retryMs?: number
}

const DEFAULT_HOLO_WORKFLOW_WATCHDOG_TIMING: HoloWorkflowWatchdogTiming = {
  intervalMs: 2_000,
  staleMs: 30_000,
  idleGraceMs: 5_000,
  retryMs: 60_000
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

type HoloWorkflowCancellation = (diveSessionId: string, workflowId: string) => Promise<void>

async function cancelWorkflowThroughLocalClient(
  niraiRoot: string,
  diveSessionId: string,
  workflowId: string
): Promise<void> {
  const clientPath = join(niraiRoot, 'tools', 'holo-local-client.mjs')
  await new Promise<void>((resolve, reject) => {
    execFile(process.execPath, [clientPath, 'workflow-cancel', diveSessionId, workflowId], {
      cwd: niraiRoot,
      env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
      timeout: 30_000,
      windowsHide: true
    }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(String(stderr || error.message || 'Nirai Holo workflow cancellation failed').trim()))
        return
      }
      try {
        const lines = String(stdout).split(/\r?\n/).filter((line) => line.trim())
        const payload = JSON.parse(lines.at(-1) ?? '{}') as {
          ok?: boolean
          result?: { workflow?: { workflow_id?: string; state?: string } }
          error?: string
        }
        if (payload.ok !== true
          || payload.result?.workflow?.workflow_id !== workflowId
          || payload.result?.workflow?.state !== 'completed') {
          reject(new Error(payload.error || 'Nirai Holo workflow cancellation returned an invalid result'))
          return
        }
        resolve()
      } catch (parseError) {
        reject(parseError)
      }
    })
  })
}

async function stopConversationThroughLocalClient(niraiRoot: string, stop: HoloConversationStop): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    execFile(process.execPath, [join(niraiRoot, 'tools', 'holo-local-client.mjs'),
      'conversation-stop', stop.conversation_url, stop.stopped_at], {
      cwd: niraiRoot, env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
      timeout: 30_000, windowsHide: true
    }, (error, stdout) => {
      try {
        const payload = JSON.parse(String(stdout).trim().split(/\r?\n/).at(-1) ?? '{}')
        if (error || payload.ok !== true || payload.result?.ok !== true) {
          reject(new Error('Holo Conversation task cancellation failed'))
        } else resolve()
      } catch { reject(new Error('Holo Conversation task cancellation returned an invalid result')) }
    })
  })
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
      if (!retryable || attempt >= 15) throw error
      // Windows can briefly deny replacement while another process is reading
      // state.json. Keep the atomic temp-file contract and retry only the
      // replacement step within a bounded window long enough to outlive
      // short-lived readers and antivirus/file-indexer handle contention.
      await delay(Math.min((attempt + 1) * 10, 50))
    }
  }
}

export class HoloAddonHost {
  private readonly niraiRoot = getNiraiRoot()
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
  private cancelledAutoResumeTaskIds = new Set<string>()
  private stoppedConversations: HoloConversationStop[] = []
  private capturingMasterStop = 0
  private flushingMasterStops = false
  private readonly masterStopListener = (event: IpcMainEvent, url: unknown): void => {
    const contents = this.view?.webContents
    if (!contents || event.sender !== contents || event.senderFrame !== contents.mainFrame
      || typeof url !== 'string' || !isSameHoloConversationUrl(url, contents.getURL())) return
    void this.stopConversation(url).catch(() => { this.issue = 'unexpected_error' })
  }
  private activeAutoResumeTrigger: HoloAutoResumeTrigger | null = null
  private autoResumeEnqueueTail: Promise<void> = Promise.resolve()
  private autoResumeDrainRunning = false
  private autoResumeWakeRequested = false
  private autoResumeRetryTimer: ReturnType<typeof setTimeout> | null = null
  private autoResumeRetryMs = HOLO_AUTO_RESUME_RETRY_MIN_MS
  private workflowWatchdogTimer: ReturnType<typeof setTimeout> | null = null
  private workflowWatchdogEnabled = false
  private workflowWatchdogRunning = false
  private workflowIdleSince: number | null = null
  private workflowObservedRevision: string | null = null
  private workflowTriggeredAt: number | null = null
  private knownDiveUrls = new Map<string, string>()
  private transientAutoResumeUrl: string | null = null
  private hostInitiatedNavigationCount = 0
  // Previous Dive's conversation URL while a new Dive is preparing. Navigating
  // back to it must not be remembered as the new Dive's conversation.
  private staleDiveUrl: string | null = null

  private readonly workflowCancellation: HoloWorkflowCancellation

  constructor(
    private readonly window: BrowserWindow,
    private readonly stateIo: HoloStateIo = DEFAULT_HOLO_STATE_IO,
    private readonly workflowWatchdogTiming: HoloWorkflowWatchdogTiming = DEFAULT_HOLO_WORKFLOW_WATCHDOG_TIMING,
    workflowCancellation?: HoloWorkflowCancellation,
    private readonly conversationStop = stopConversationThroughLocalClient
  ) {
    this.workflowCancellation = workflowCancellation
      ?? ((diveSessionId, workflowId) => cancelWorkflowThroughLocalClient(this.niraiRoot, diveSessionId, workflowId))
    ipcMain.on('holo:master-stop', this.masterStopListener)
  }

  async stopConversation(conversationUrl: string): Promise<void> {
    if (!isHoloConversationUrl(conversationUrl)) throw new Error('Invalid owner Conversation')
    const stoppedAt = new Date().toISOString()
    this.capturingMasterStop += 1
    try {
      await this.loadState()
      const stop = { conversation_url: conversationUrl, stopped_at: stoppedAt, pending: true }
      this.stoppedConversations = this.stoppedConversations.filter(
        (item) => !isSameHoloConversationUrl(item.conversation_url, conversationUrl)
      ).concat(stop)
      await this.persistState()
      await this.flushPendingConversationStops()
    } finally {
      this.capturingMasterStop -= 1
      this.workflowWatchdogEnabled = true
      this.scheduleWorkflowWatchdog()
      this.scheduleAutoResumeDrain(0)
    }
  }

  private wasConversationStopped(url: string | null | undefined, startedAt: string | undefined): boolean {
    return this.stoppedConversations.some((stop) => isSameHoloConversationUrl(stop.conversation_url, url)
      && (!startedAt || !Number.isFinite(Date.parse(startedAt)) || Date.parse(startedAt) <= Date.parse(stop.stopped_at)))
  }

  private async flushPendingConversationStops(): Promise<void> {
    if (this.flushingMasterStops || this.disposed) return
    this.flushingMasterStops = true
    try {
      for (const stop of [...this.stoppedConversations]) {
        if (!stop.pending) continue
        // Save the stop before touching Core. A failed save or offline Core
        // leaves this same request available after restart.
        await this.persistState()
        const lease = await this.readWorkflowLease()
        if (lease?.state === 'active' && isSameHoloConversationUrl(lease.conversation_url, stop.conversation_url)
          && Date.parse(lease.started_at) <= Date.parse(stop.stopped_at)) {
          await this.cancelAutoResume(`WF-${lease.workflow_id}`)
        }
        await this.conversationStop(this.niraiRoot, stop)
        this.stoppedConversations = this.stoppedConversations.map((item) => item === stop ? { ...stop, pending: false } : item)
        await this.persistState()
      }
    } catch {
      this.issue = 'unexpected_error'
      console.warn('holo_conversation_stop_pending')
    } finally {
      this.flushingMasterStops = false
      if (this.stoppedConversations.some((stop) => stop.pending)) this.scheduleWorkflowWatchdog()
    }
  }

  async resumePendingAutoResume(): Promise<number> {
    await this.loadState()
    this.workflowWatchdogEnabled = true
    await this.flushPendingConversationStops()
    console.warn('holo_auto_resume_restore', {
      pending_count: this.autoResumeQueue.length,
      current_dive_session_id: this.currentDiveSessionId,
      current_dive_url: this.currentDiveUrl
    })
    const pruned = this.pruneObsoleteTerminalAutoResume()
    if (pruned.length > 0) {
      const persisted = await this.persistState().then(
        () => true,
        () => false
      )
      if (persisted) {
        for (const trigger of pruned) await this.clearTaskOwnerAfterTerminalResume(trigger)
      }
    }
    if (this.autoResumeQueue.length > 0) {
      // Startup delivery must not depend on a zero-delay timer racing the
      // workflow watchdog. Start the durable continuation immediately; the
      // drain schedules the watchdog after the queue becomes empty.
      void this.drainAutoResumeQueue()
    } else {
      this.scheduleWorkflowWatchdog(0)
    }
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
      await this.loadHostInitiatedUrl(view.webContents, HOLO_CHATGPT_HOME_URL)
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
          await this.loadHostInitiatedUrl(view.webContents, previousDiveUrl)
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

  async taskManagementState(): Promise<HoloTaskManagementState> {
    await this.loadState()
    const rows = new Map<string, HoloTaskManagementState['tasks'][number]>()
    for (const trigger of this.autoResumeQueue) {
      if (this.cancelledAutoResumeTaskIds.has(trigger.task_id)) continue
      const previous = rows.get(trigger.task_id)
      rows.set(trigger.task_id, {
        task_id: trigger.task_id, title: trigger.task_id, state: trigger.reason,
        kind: trigger.reason === 'workflow_stalled' ? 'workflow' : trigger.kind ?? 'task',
        pending_count: (previous?.pending_count ?? 0) + 1
      })
    }
    const workflow = await this.readWorkflowLease()
    if (workflow?.state === 'active') {
      const taskId = `WF-${workflow.workflow_id}`
      rows.set(taskId, {
        task_id: taskId, title: workflow.label, state: 'workflow_active', kind: 'workflow',
        pending_count: rows.get(taskId)?.pending_count ?? 0
      })
    }
    return { tasks: [...rows.values()], cancelled_task_ids: [...this.cancelledAutoResumeTaskIds] }
  }

  async cancelAutoResume(taskId: string): Promise<void> {
    if (!isSafeHoloTaskId(taskId)) throw new Error('Invalid Task ID')
    const operation = this.autoResumeEnqueueTail.then(async () => {
      await this.loadState()
      // Workflow lease mutation is owned by the Holo Local Client. Routing the
      // Master cancellation through that same writer keeps its cross-process
      // lock and exact workflow identity checks in force instead of racing a
      // second direct workflow.json writer in the Electron process.
      const workflow = taskId.startsWith('WF-') ? await this.readWorkflowLease() : null
      if (workflow?.state === 'active' && taskId === `WF-${workflow.workflow_id}`) {
        await this.workflowCancellation(workflow.dive_session_id, workflow.workflow_id)
      }

      const triggers = this.autoResumeQueue.filter((trigger) => trigger.task_id === taskId)
      if (this.activeAutoResumeTrigger?.task_id === taskId) triggers.push(this.activeAutoResumeTrigger)
      this.cancelledAutoResumeTaskIds.add(taskId)
      this.autoResumeQueue = this.autoResumeQueue.filter((trigger) => trigger.task_id !== taskId)
      // Stop a not-yet-clicked page submission too. The durable guard below
      // covers navigation, renderer replay and subsequent application restarts.
      const contents = this.view?.webContents
      if (contents && !contents.isDestroyed()) {
        void contents.executeJavaScript(buildHoloAutoResumeCancellationScript(
          taskId,
          triggers.map(buildHoloAutoResumePrompt)
        ), true).catch(() => undefined)
      }
      await this.persistState()
      this.scheduleAutoResumeDrain(0)
    })
    this.autoResumeEnqueueTail = operation.then(() => undefined, () => undefined)
    return operation
  }

  private async enqueueAutoResumeSerialized(trigger: HoloAutoResumeTrigger): Promise<HoloAutoResumeEnqueueResult> {
    await this.loadState()
    await this.persistTail
    if (this.persistenceIssue) {
      try { await this.persistState() } catch {
        return { accepted: false, duplicate: false, pending_count: this.autoResumeQueue.length }
      }
    }
    if (this.cancelledAutoResumeTaskIds.has(trigger.task_id)) {
      return { accepted: false, duplicate: false, discarded: true, pending_count: this.autoResumeQueue.length }
    }
    const key = holoAutoResumeTriggerKey(trigger)
    // A successfully processed Trigger no longer needs its owner tombstone.
    // Deduplicate before owner resolution so a legitimate replay remains a
    // duplicate even after that routing metadata has been cleaned up.
    if (this.processedAutoResumeKeys.includes(key) && trigger.reason !== 'workflow_stalled') {
      await this.clearTaskOwnerAfterTerminalResume(trigger)
      return {
        accepted: false,
        duplicate: true,
        pending_count: this.autoResumeQueue.length
      }
    }
    const ownedTrigger = await this.resolveAutoResumeOwner(trigger)
    if (!ownedTrigger) {
      return {
        accepted: false,
        duplicate: false,
        discarded: true,
        pending_count: this.autoResumeQueue.length
      }
    }
    const durableTrigger = ownedTrigger.reason === 'workflow_stalled' && !ownedTrigger.delivery_id?.trim()
      ? { ...ownedTrigger, delivery_id: randomUUID() }
      : ownedTrigger
    if (
      this.isObsoleteTerminalAutoResume(ownedTrigger)
      || await this.isObsoleteAfterWorkflowCompletion(ownedTrigger)
    ) {
      const previousKeys = this.processedAutoResumeKeys
      this.markProcessedAutoResumeKey(key)
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
      await this.clearTaskOwnerAfterTerminalResume(ownedTrigger)
      return {
        accepted: true,
        duplicate: false,
        pending_count: this.autoResumeQueue.length
      }
    }
    const duplicate = this.autoResumeQueue.some(
      (queued) => holoAutoResumeTriggerKey(queued) === key
    )
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
    this.autoResumeQueue.push({ ...durableTrigger })
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
    // Retain ownership until delivery/discard is durable: completion can still
    // make this pending continuation obsolete. Cleanup has one terminal path.
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
    ipcMain.removeListener('holo:master-stop', this.masterStopListener)
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
    this.hostInitiatedNavigationCount = 0
    this.autoResumeWakeRequested = false
    if (this.autoResumeRetryTimer !== null) {
      clearTimeout(this.autoResumeRetryTimer)
      this.autoResumeRetryTimer = null
    }
    this.workflowWatchdogEnabled = false
    if (this.workflowWatchdogTimer !== null) {
      clearTimeout(this.workflowWatchdogTimer)
      this.workflowWatchdogTimer = null
    }
    this.resetWorkflowWatchdogObservation()
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
        preload: join(__dirname, '../preload/holo.js'),
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
      if (isSameHoloConversationUrl(url, this.transientAutoResumeUrl)) return
      if (isSameHoloConversationUrl(url, this.staleDiveUrl)) return
      this.staleDiveUrl = null

      // Track the Conversation Master is actually viewing. Task/Review and
      // workflow ownership is persisted separately when work starts, so later
      // manual navigation must update the current routing context without
      // rewriting ownership of already-running work. Auto-resume background
      // navigation and failed-Dive rollback are excluded above.
      this.currentDiveUrl = url
      if (this.currentDiveSessionId) this.knownDiveUrls.set(this.currentDiveSessionId, url)
      const pruned = this.pruneObsoleteTerminalAutoResume()
      void this.persistState()
        .then(async () => {
          for (const trigger of pruned) await this.clearTaskOwnerAfterTerminalResume(trigger)
        })
        .catch(() => undefined)
      if (this.hostInitiatedNavigationCount === 0) {
        this.wakeAutoResumeForVisibleConversation(url)
      }
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
      const loadedUrl = view.webContents.getURL()
      // Only navigation outside a Host-owned load is evidence that Master
      // returned to a Conversation. Auto Resume, watchdog, startup restore and
      // Dive rollback loads must never preempt their own retry/backoff policy.
      if (this.hostInitiatedNavigationCount === 0) {
        this.wakeAutoResumeForVisibleConversation(loadedUrl)
      }
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
    const needsLoad = (): boolean => !isHoloAllowedNavigationUrl(view.webContents.getURL())
      || this.webState === 'unavailable' || this.webState === 'error'
    if (!needsLoad()) return
    if (!this.initialLoadPromise) {
      this.initialLoadPromise = (async () => {
        await this.loadState()
        if (!needsLoad()) return
        this.issue = null
        this.webState = 'loading'
        const visibleUrl = view.webContents.getURL()
        await this.loadHostInitiatedUrl(
          view.webContents,
          this.attached && isHoloAllowedNavigationUrl(visibleUrl)
            ? visibleUrl : this.currentDiveUrl ?? HOLO_CHATGPT_HOME_URL
        )
      })().finally(() => {
        this.initialLoadPromise = null
      })
    }
    await this.initialLoadPromise
  }

  private async ensureInitialLoadForAutoResume(view: WebContentsView): Promise<void> {
    let timeout: ReturnType<typeof setTimeout> | null = null
    try {
      await Promise.race([
        this.ensureInitialLoad(view),
        new Promise<never>((_resolve, reject) => {
          timeout = setTimeout(() => {
            reject(new Error('Holo auto-resume initial load timed out'))
          }, HOLO_AUTO_RESUME_LOAD_TIMEOUT_MS)
        })
      ])
    } catch (error) {
      if (error instanceof Error && error.message === 'Holo auto-resume initial load timed out') {
        try {
          view.webContents.stop()
        } catch {
          // The WebContents may have been destroyed during shutdown.
        }
        this.initialLoadPromise = null
        this.setWebFailure('web_load_failed', 'unavailable')
      }
      throw error
    } finally {
      if (timeout !== null) clearTimeout(timeout)
    }
  }

  private async loadHostInitiatedUrl(contents: WebContents, url: string): Promise<void> {
    this.hostInitiatedNavigationCount += 1
    try {
      await contents.loadURL(url)
    } finally {
      this.hostInitiatedNavigationCount = Math.max(0, this.hostInitiatedNavigationCount - 1)
    }
  }

  private async loadUrlForAutoResume(view: WebContentsView, url: string): Promise<void> {
    let timeout: ReturnType<typeof setTimeout> | null = null
    try {
      await Promise.race([
        this.loadHostInitiatedUrl(view.webContents, url),
        new Promise<never>((_resolve, reject) => {
          timeout = setTimeout(() => {
            reject(new Error('Holo auto-resume navigation timed out'))
          }, HOLO_AUTO_RESUME_LOAD_TIMEOUT_MS)
        })
      ])
    } catch (error) {
      if (error instanceof Error && error.message === 'Holo auto-resume navigation timed out') {
        this.setWebFailure('web_load_failed', 'unavailable')
        try {
          view.webContents.stop()
        } catch {
          // The WebContents may have been destroyed during shutdown.
        }
      }
      throw error
    } finally {
      if (timeout !== null) clearTimeout(timeout)
    }
  }

  private async executeAutoResumeScript(view: WebContentsView, script: string): Promise<unknown> {
    let timeout: ReturnType<typeof setTimeout> | null = null
    try {
      return await Promise.race([
        view.webContents.executeJavaScript(script, true),
        new Promise<never>((_resolve, reject) => {
          timeout = setTimeout(() => {
            reject(new Error('Holo auto-resume submission timed out'))
          }, HOLO_AUTO_RESUME_SUBMIT_TIMEOUT_MS)
        })
      ])
    } finally {
      if (timeout !== null) clearTimeout(timeout)
    }
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
    this.workflowTriggeredAt = null
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
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null
      throw error
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
      await this.flushPendingConversationStops()
      const lease = await this.readWorkflowLease()
      if (
        !lease
        || lease.state !== 'active'
        || !lease.conversation_url
        || this.cancelledAutoResumeTaskIds.has(`WF-${lease.workflow_id}`)
        || this.capturingMasterStop > 0
        || this.wasConversationStopped(lease.conversation_url, lease.started_at)
      ) {
        this.resetWorkflowWatchdogObservation()
        return
      }
      // The Workflow lease captures its owning Conversation when the workflow
      // starts. `currentDiveUrl` / `knownDiveUrls` track what Master is viewing
      // now and may legitimately move to another Conversation within the same
      // Dive while the workflow keeps running. Never reinterpret that browsing
      // state as ownership or a hidden watchdog would strand the original work.
      const revision = `${lease.workflow_id}:${lease.updated_at}`
      if (revision !== this.workflowObservedRevision) {
        this.workflowObservedRevision = revision
        this.workflowIdleSince = null
        this.workflowTriggeredAt = null
      }
      if (this.workflowTriggeredAt !== null) {
        const retryMs = this.workflowWatchdogTiming.retryMs ?? 60_000
        if (this.workflowTriggeredAt !== null && Date.now() - this.workflowTriggeredAt < retryMs) return
        // A submitted workflow continuation can itself die before it reaches a
        // observed activity. The lease revision then stays unchanged. Re-arm the same
        // stale revision after a bounded cooldown instead of suppressing it
        // forever and stranding an active lease.
        this.workflowTriggeredAt = null
      }

      const updatedAtMs = Date.parse(lease.updated_at)
      if (!Number.isFinite(updatedAtMs) || Date.now() - updatedAtMs < this.workflowWatchdogTiming.staleMs) {
        this.workflowIdleSince = null
        return
      }

      if (this.disposed) return
      const view = this.ensureView()
      await this.ensureInitialLoadForAutoResume(view)
      if (this.webState !== 'ready') {
        this.workflowIdleSince = null
        return
      }
      const targetUrl = lease.conversation_url
      const restoreUrl = this.currentDiveUrl
      let navigatedForProbe = false
      if (!isSameHoloConversationUrl(view.webContents.getURL(), targetUrl)) {
        if (this.attached) {
          this.workflowIdleSince = null
          return
        }
        try {
          this.transientAutoResumeUrl = targetUrl
          await this.loadUrlForAutoResume(view, targetUrl)
          navigatedForProbe = true
        } catch {
          this.transientAutoResumeUrl = null
          this.workflowIdleSince = null
          return
        }
      }

      let busy = true
      try {
        busy = Boolean(await this.executeAutoResumeScript(view, buildHoloGenerationBusyProbeScript()))
      } catch {
        return
      } finally {
        if (!this.disposed && navigatedForProbe && restoreUrl
          && this.currentDiveUrl === restoreUrl
          && isSameHoloConversationUrl(view.webContents.getURL(), targetUrl)
          && !isSameHoloConversationUrl(restoreUrl, targetUrl)) {
          try {
            this.transientAutoResumeUrl = restoreUrl
            await this.loadUrlForAutoResume(view, restoreUrl)
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
        this.workflowTriggeredAt = Date.now()
      }
    } catch {
      // A navigation or shutdown can invalidate an in-flight probe.
      this.workflowIdleSince = null
    } finally {
      this.workflowWatchdogRunning = false
      if (this.autoResumeWakeRequested && this.autoResumeQueue.length > 0) {
        this.autoResumeWakeRequested = false
        this.scheduleImmediateAutoResumeDrain()
      } else {
        this.scheduleWorkflowWatchdog()
      }
    }
  }

  private scheduleAutoResumeDrain(delayMs: number): void {
    if (this.disposed || this.autoResumeRetryTimer !== null) return
    this.autoResumeRetryTimer = setTimeout(() => {
      this.autoResumeRetryTimer = null
      void this.drainAutoResumeQueue()
    }, Math.max(0, delayMs))
  }

  private scheduleImmediateAutoResumeDrain(): void {
    if (this.disposed) return
    // A visible-owner wake is stronger than an ordinary retry backoff. Only
    // this explicit wake path may replace an already-armed retry timer; normal
    // zero-delay scheduling must not defeat draft/busy/not-ready backoff.
    if (this.autoResumeRetryTimer !== null) {
      clearTimeout(this.autoResumeRetryTimer)
      this.autoResumeRetryTimer = null
    }
    this.scheduleAutoResumeDrain(0)
  }

  private wakeAutoResumeForVisibleConversation(visibleUrl: string): void {
    if (
      this.disposed
      || this.autoResumeQueue.length === 0
      || !isHoloConversationUrl(visibleUrl)
    ) return
    const ownsPendingResume = this.autoResumeQueue.some((trigger) => (
      typeof trigger.conversation_url === 'string'
      && isSameHoloConversationUrl(trigger.conversation_url, visibleUrl)
    ))
    if (!ownsPendingResume) return

    // A queued retry may be sleeping because Master was viewing another
    // Conversation. Returning to the owner Conversation is an explicit wake-up
    // signal. If a drain/watchdog is still unwinding, remember the wake instead
    // of losing it and falling back to the ordinary one-second retry.
    if (this.autoResumeRetryTimer !== null) {
      clearTimeout(this.autoResumeRetryTimer)
      this.autoResumeRetryTimer = null
    }
    if (this.autoResumeDrainRunning || this.workflowWatchdogRunning) {
      this.autoResumeWakeRequested = true
      return
    }
    this.autoResumeWakeRequested = false
    void this.drainAutoResumeQueue()
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
    if (this.disposed || this.capturingMasterStop > 0 || this.autoResumeDrainRunning || this.autoResumeQueue.length === 0) return
    if (this.workflowWatchdogRunning) {
      // A watchdog may enqueue the continuation itself and its zero-delay drain
      // can fire before the watchdog's finally block clears the running flag.
      // Latch an immediate handoff instead of nondeterministically falling into
      // the ordinary one-second retry backoff.
      this.autoResumeWakeRequested = true
      return
    }
    this.autoResumeDrainRunning = true
    console.warn('holo_auto_resume_drain_start', {
      pending_count: this.autoResumeQueue.length,
      attached: this.attached,
      current_dive_url: this.currentDiveUrl
    })
    try {
      await this.loadState()
      // Entries become visible in memory before their atomic save finishes.
      // Never deliver one while enqueue/cancellation persistence is in flight.
      let enqueueTail: Promise<void>
      do {
        enqueueTail = this.autoResumeEnqueueTail
        await enqueueTail
      } while (enqueueTail !== this.autoResumeEnqueueTail)
      // Resolve each candidate once. Invalid/stale leases must be discarded even
      // when a visible, unrelated Conversation prevents delivery.
      const candidates: Array<{ queued: HoloAutoResumeTrigger; owned: HoloAutoResumeTrigger }> = []
      const removed: HoloAutoResumeTrigger[] = []
      for (const queued of [...this.autoResumeQueue]) {
        const owned = await this.resolveAutoResumeOwner(queued)
        if (!this.autoResumeQueue.includes(queued)) continue
        if (owned && !await this.isObsoleteAfterWorkflowCompletion(owned)) {
          candidates.push({ queued, owned })
        } else {
          this.autoResumeQueue = this.autoResumeQueue.filter((candidate) => candidate !== queued)
          if (owned) this.markProcessedAutoResumeKey(holoAutoResumeTriggerKey(owned))
          removed.push(owned ?? queued)
        }
      }
      if (removed.length > 0) {
        await this.persistState()
        for (const trigger of removed) await this.clearTaskOwnerAfterTerminalResume(trigger)
      }
      if (candidates.length === 0) return
      let selected: typeof candidates[number] | undefined = candidates[0]

      if (this.attached) {
        const visibleUrl = this.view?.webContents.getURL() || this.currentDiveUrl
        if (!visibleUrl) {
          console.warn('holo_auto_resume_wait', { reason: 'visible_url_missing' })
          this.scheduleAutoResumeRetry('not_ready')
          return
        }
        selected = candidates.find(({ owned }) => isSameHoloConversationUrl(owned.conversation_url, visibleUrl))
        if (!selected && !isHoloConversationUrl(visibleUrl)) {
          // Startup can leave the attached ChatGPT surface briefly at Home even
          // though the persisted Dive still owns a concrete Conversation. In
          // that case it is safe to restore only the Current Dive owner. Never
          // navigate away from a different visible Conversation.
          selected = candidates.find(({ owned }) => (
            owned.dive_session_id === this.currentDiveSessionId
            && isSameHoloConversationUrl(owned.conversation_url, this.currentDiveUrl)
          ))
        }
        if (!selected) {
          console.warn('holo_auto_resume_wait', {
            reason: 'visible_conversation_mismatch',
            visible_url: visibleUrl,
            pending_count: this.autoResumeQueue.length
          })
          // Keep older triggers for hidden/background delivery, but never let
          // them block a continuation for the Conversation Master is viewing.
          this.scheduleAutoResumeRetry('not_ready')
          return
        }
      }

      if (!selected) return
      const { queued: queuedTrigger, owned: trigger } = selected
      const key = holoAutoResumeTriggerKey(trigger)
      const queueIndex = this.autoResumeQueue.indexOf(queuedTrigger)
      if (queueIndex < 0) return
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
      await this.ensureInitialLoadForAutoResume(view)
      if (this.webState !== 'ready') {
        console.warn('holo_auto_resume_wait', {
          reason: 'web_state_not_ready',
          web_state: this.webState,
          current_url: view.webContents.getURL()
        })
        this.scheduleAutoResumeRetry('not_ready')
        return
      }

      const currentUrl = view.webContents.getURL()
      const restoreUrl = this.currentDiveUrl
      const isBackgroundTarget = !isSameHoloConversationUrl(targetUrl, restoreUrl)
      const mayRestoreAttachedCurrentDive = this.attached
        && !isHoloConversationUrl(currentUrl)
        && trigger.dive_session_id === this.currentDiveSessionId
        && Boolean(restoreUrl && isSameHoloConversationUrl(targetUrl, restoreUrl))
      if (!isSameHoloConversationUrl(currentUrl, targetUrl)) {
        // Never yank a visible Holo surface away from another Conversation.
        // Restoring the Current Dive from ChatGPT Home/non-conversation startup
        // state is safe and prevents a durable pending resume from stalling.
        if (this.attached && !mayRestoreAttachedCurrentDive) {
          console.warn('holo_auto_resume_wait', {
            reason: 'attached_navigation_guard',
            current_url: currentUrl,
            target_url: targetUrl
          })
          this.scheduleAutoResumeRetry('not_ready')
          return
        }
        try {
          if (isBackgroundTarget) this.transientAutoResumeUrl = targetUrl
          await this.loadUrlForAutoResume(view, targetUrl)
        } catch {
          this.transientAutoResumeUrl = null
          console.warn('holo_auto_resume_wait', {
            reason: 'target_navigation_failed',
            current_url: currentUrl,
            target_url: targetUrl
          })
          this.scheduleAutoResumeRetry('not_ready')
          return
        }
      }

      let rawResult: unknown
      try {
        if (this.disposed || this.capturingMasterStop > 0 || this.cancelledAutoResumeTaskIds.has(trigger.task_id)) return
        // Loading the owner Conversation can take seconds. Work may have
        // resumed/completed during navigation; recheck the same revision at
        // the final submission boundary instead of waking an active owner.
        if (!await this.resolveAutoResumeOwner(trigger) || await this.isObsoleteAfterWorkflowCompletion(trigger)) {
          this.autoResumeQueue = this.autoResumeQueue.filter((candidate) => holoAutoResumeTriggerKey(candidate) !== key)
          await this.persistState()
          await this.clearTaskOwnerAfterTerminalResume(trigger)
          return
        }
        if (!isSameHoloConversationUrl(view.webContents.getURL(), targetUrl)) {
          console.warn('holo_auto_resume_wait', {
            reason: 'target_conversation_not_reached',
            current_url: view.webContents.getURL(),
            target_url: targetUrl
          })
          this.scheduleAutoResumeRetry('not_ready')
          return
        }
        this.activeAutoResumeTrigger = trigger
        rawResult = await this.executeAutoResumeScript(
          view,
          buildHoloAutoResumeSubmissionScript(
            buildHoloAutoResumePrompt(trigger),
            key,
            targetUrl,
            Date.now() + HOLO_AUTO_RESUME_SUBMIT_TIMEOUT_MS,
            trigger.task_id,
            trigger.delivery_id?.trim() || undefined
          )
        )
      } catch {
        this.scheduleAutoResumeRetry('not_ready')
        return
      } finally {
        this.activeAutoResumeTrigger = null
        if (!this.disposed && isBackgroundTarget && restoreUrl
          && this.currentDiveUrl === restoreUrl && isSameHoloConversationUrl(view.webContents.getURL(), targetUrl)) {
          try {
            this.transientAutoResumeUrl = restoreUrl
            await this.loadUrlForAutoResume(view, restoreUrl)
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
        console.warn('holo_auto_resume_retry', {
          task_id: trigger.task_id,
          status,
          current_url: view.webContents.getURL(),
          target_url: targetUrl
        })
        this.transientAutoResumeUrl = null
        this.scheduleAutoResumeRetry(status)
        return
      }

      // Navigation/new Dive preparation can prune entries while submission is
      // pending. Remove by identity, never by a pre-await array position.
      this.autoResumeQueue = this.autoResumeQueue.filter((candidate) => holoAutoResumeTriggerKey(candidate) !== key)
      if (trigger.reason === 'workflow_stalled') {
        this.workflowObservedRevision = `${trigger.task_id.slice(3)}:${trigger.request_id}`
        this.workflowTriggeredAt = Date.now()
        this.workflowIdleSince = null
        // Workflow-stalled is not terminal. Keeping its key in the durable
        // processed set would make a second failed continuation unrecoverable.
        this.processedAutoResumeKeys = this.processedAutoResumeKeys.filter((candidate) => candidate !== key)
      } else {
        this.markProcessedAutoResumeKey(key)
      }
      this.autoResumeRetryMs = HOLO_AUTO_RESUME_RETRY_MIN_MS
      const processedPersisted = await this.persistState().then(
        () => true,
        () => false
      )
      if (!processedPersisted) {
        // Keep the same delivery attempt until its acknowledgement is durable.
        // In particular, a failed disk write must not permit watchdog re-arm
        // to replace a delivery that ChatGPT has already received.
        this.processedAutoResumeKeys = this.processedAutoResumeKeys.filter((candidate) => candidate !== key)
        if (!this.cancelledAutoResumeTaskIds.has(trigger.task_id)
          && !this.autoResumeQueue.some((candidate) => holoAutoResumeTriggerKey(candidate) === key)) {
          this.autoResumeQueue.push(trigger)
        }
        this.scheduleAutoResumeRetry('not_ready')
        return
      }
      await this.clearTaskOwnerAfterTerminalResume(trigger)

      // ChatGPT is now generating this continuation. Keep subsequent events
      // queued and retry after a small delay rather than submitting overlapping
      // turns into the same Conversation.
      if (this.autoResumeQueue.length > 0) {
        this.scheduleAutoResumeDrain(HOLO_AUTO_RESUME_RETRY_MIN_MS)
      } else {
        this.scheduleWorkflowWatchdog(0)
      }
    } catch (error) {
      console.warn('holo_auto_resume_drain_error', {
        error: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
        pending_count: this.autoResumeQueue.length,
        attached: this.attached,
        web_state: this.webState,
        current_dive_url: this.currentDiveUrl,
        visible_url: this.view?.webContents.getURL() || null
      })
      this.scheduleAutoResumeRetry('not_ready')
    } finally {
      this.autoResumeDrainRunning = false
      if (this.autoResumeQueue.length === 0) {
        this.autoResumeWakeRequested = false
        this.scheduleWorkflowWatchdog(0)
      } else if (this.autoResumeWakeRequested) {
        this.autoResumeWakeRequested = false
        this.scheduleImmediateAutoResumeDrain()
      } else {
        this.scheduleAutoResumeDrain(HOLO_AUTO_RESUME_RETRY_MIN_MS)
      }
    }
  }

  private isObsoleteTerminalAutoResume(trigger: HoloAutoResumeTrigger): boolean {
    if (!this.currentDiveSessionId || !this.currentDiveUrl) return false
    if (trigger.reason !== 'done' && trigger.reason !== 'cancelled') return false
    const ownerDive = trigger.dive_session_id?.trim()
    return Boolean(ownerDive && ownerDive !== this.currentDiveSessionId)
  }

  private async isObsoleteAfterWorkflowCompletion(trigger: HoloAutoResumeTrigger): Promise<boolean> {
    if (trigger.reason === 'workflow_stalled') return false
    const owner = await this.readTaskOwner(trigger.task_id)
    if (!owner) return false
    const workflow = await this.readWorkflowLease()
    // Exact identity replaces the timestamp guess for newly created owners.
    // Legacy owners remain readable without rewriting live task records.
    if (owner.workflow_id) {
      // A single active lease can be replaced only after completion/cancel.
      // Supersession therefore proves old work is finished without keeping a
      // second persisted set of completed workflow IDs.
      return Boolean(workflow && (workflow.state === 'completed' || workflow.workflow_id !== owner.workflow_id))
    }
    if (trigger.kind !== 'review') return false
    if (
      workflow?.state !== 'completed'
      || workflow.dive_session_id !== owner.dive_session_id
      || typeof workflow.conversation_url !== 'string'
      || !isSameHoloConversationUrl(workflow.conversation_url, owner.conversation_url)
      || typeof workflow.completed_at !== 'string'
    ) return false
    const ownerCreatedAt = Date.parse(owner.created_at)
    const workflowCompletedAt = Date.parse(workflow.completed_at)
    return Number.isFinite(ownerCreatedAt)
      && Number.isFinite(workflowCompletedAt)
      && ownerCreatedAt <= workflowCompletedAt
  }

  private markProcessedAutoResumeKey(key: string): void {
    this.processedAutoResumeKeys = [
      ...this.processedAutoResumeKeys.filter((candidate) => candidate !== key),
      key
    ].slice(-HOLO_AUTO_RESUME_PROCESSED_LIMIT)
  }

  private pruneObsoleteTerminalAutoResume(): HoloAutoResumeTrigger[] {
    if (!this.currentDiveSessionId || !this.currentDiveUrl || this.autoResumeQueue.length === 0) return []
    const obsoleteTriggers: HoloAutoResumeTrigger[] = []
    const obsoleteKeys: string[] = []
    this.autoResumeQueue = this.autoResumeQueue.filter((trigger) => {
      if (!this.isObsoleteTerminalAutoResume(trigger)) return true
      obsoleteTriggers.push(trigger)
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
    return obsoleteTriggers
  }

  private getStatePath(): string {
    return join(this.niraiRoot, 'runtime', 'holo', 'state.json')
  }

  private getWorkflowStatePath(): string {
    return join(this.niraiRoot, 'runtime', 'holo', 'workflow.json')
  }

  private getTaskOwnerPath(taskId: string): string | null {
    if (!/^(?:T|HR|IA)-[A-Za-z0-9-]+$/.test(taskId)) return null
    return join(this.niraiRoot, 'runtime', 'holo', 'task_owners', `${taskId}.json`)
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
    } catch (error) {
      // Only a missing record proves that no owner exists. A transient read
      // failure must retain the renderer's durable entry for a later retry.
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null
      throw error
    }
  }

  private async clearTaskOwnerAfterTerminalResume(trigger: HoloAutoResumeTrigger): Promise<void> {
    if (trigger.reason !== 'done' && trigger.reason !== 'cancelled'
      && !await this.isObsoleteAfterWorkflowCompletion(trigger)) return
    // Core validates Review ACKs against this owner and removes it after the
    // receipt is persisted. Deleting it here would race that acknowledgement.
    if (trigger.kind === 'review') return
    const path = this.getTaskOwnerPath(trigger.task_id)
    if (!path) return
    try {
      await this.stateIo.removeFile(path)
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
        console.warn('holo_task_owner_cleanup_failed', trigger.task_id, error)
      }
    }
  }

  private async resolveAutoResumeOwner(trigger: HoloAutoResumeTrigger): Promise<HoloAutoResumeTrigger | null> {
    if (this.cancelledAutoResumeTaskIds.has(trigger.task_id)) return null
    if (trigger.reason === 'workflow_stalled') {
      const lease = await this.readWorkflowLease()
      if (!lease || lease.state !== 'active'
        || trigger.task_id !== `WF-${lease.workflow_id}`
        || trigger.request_id !== lease.updated_at
        || trigger.dive_session_id !== lease.dive_session_id
        || !isSameHoloConversationUrl(trigger.conversation_url, lease.conversation_url)
        || this.wasConversationStopped(lease.conversation_url, lease.started_at)) return null
    } else if (this.stoppedConversations.length > 0) {
      const owner = await this.readTaskOwner(trigger.task_id)
      if (this.wasConversationStopped(owner?.conversation_url ?? trigger.conversation_url, owner?.created_at)) return null
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
      if (Array.isArray(parsed.stopped_conversations)) {
        this.stoppedConversations = parsed.stopped_conversations.filter((item) => item
          && typeof item.conversation_url === 'string' && isHoloConversationUrl(item.conversation_url)
          && typeof item.stopped_at === 'string' && Number.isFinite(Date.parse(item.stopped_at))
          && typeof item.pending === 'boolean')
      }
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
      if (Array.isArray(parsed.cancelled_auto_resume_task_ids)) {
        this.cancelledAutoResumeTaskIds = new Set(parsed.cancelled_auto_resume_task_ids.filter(
          (id): id is string => typeof id === 'string' && isSafeHoloTaskId(id)
        ))
        this.autoResumeQueue = this.autoResumeQueue.filter((trigger) => !this.cancelledAutoResumeTaskIds.has(trigger.task_id))
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
      stopped_conversations: [...this.stoppedConversations],
      current_dive_url: this.currentDiveUrl,
      current_dive_session_id: this.currentDiveSessionId,
      known_dive_urls: Object.fromEntries(this.knownDiveUrls),
      pending_auto_resume: this.autoResumeQueue.map((trigger) => ({ ...trigger })),
      processed_auto_resume_keys: [...this.processedAutoResumeKeys],
      cancelled_auto_resume_task_ids: [...this.cancelledAutoResumeTaskIds],
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
