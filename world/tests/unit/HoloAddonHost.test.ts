import { mkdir, mkdtemp, readFile, readdir, rename, rm, unlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { setTimeout as realDelay } from 'node:timers/promises'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const harness = vi.hoisted(() => {
  class FakeWebContents {
    readonly id = 101
    url = ''
    readonly loadedUrls: string[] = []
    bootstrapResult: unknown = true
    autoResumeResult: unknown = { status: 'submitted' }
    generationBusy = false
    readonly autoResumeScripts: string[] = []
    private readonly listeners = new Map<string, Array<(...args: unknown[]) => void>>()

    readonly session = {
      permissionRequestHandler: null as null | ((
        webContents: { readonly id: number; getURL: () => string },
        permission: string,
        callback: (allowed: boolean) => void,
        details: { readonly requestingUrl?: string }
      ) => void),
      permissionCheckHandler: null as null | ((
        webContents: { readonly id: number; getURL: () => string } | null,
        permission: string,
        requestingOrigin: string,
        details: { readonly requestingUrl?: string }
      ) => boolean),
      setPermissionRequestHandler: (handler: null | ((
        webContents: { readonly id: number; getURL: () => string },
        permission: string,
        callback: (allowed: boolean) => void,
        details: { readonly requestingUrl?: string }
      ) => void)): void => {
        this.session.permissionRequestHandler = handler
      },
      setPermissionCheckHandler: (handler: null | ((
        webContents: { readonly id: number; getURL: () => string } | null,
        permission: string,
        requestingOrigin: string,
        details: { readonly requestingUrl?: string }
      ) => boolean)): void => {
        this.session.permissionCheckHandler = handler
      },
      setDisplayMediaRequestHandler: (): void => undefined,
      setDevicePermissionHandler: (): void => undefined
    }

    on(event: string, callback: (...args: unknown[]) => void): FakeWebContents {
      const list = this.listeners.get(event) ?? []
      list.push(callback)
      this.listeners.set(event, list)
      return this
    }

    emit(event: string, ...args: unknown[]): void {
      for (const callback of this.listeners.get(event) ?? []) callback(...args)
    }

    async loadURL(url: string): Promise<void> {
      this.emit('did-start-navigation', { isMainFrame: true, isSameDocument: false })
      this.url = url
      this.loadedUrls.push(url)
      this.emit('did-navigate', {}, url)
      this.emit('did-finish-load')
    }

    getURL(): string {
      return this.url
    }

    getTitle(): string {
      return 'ChatGPT'
    }

    isDestroyed(): boolean {
      return false
    }

    reload(): void {
      void this.loadURL(this.url)
    }

    close(): void {}

    setWindowOpenHandler(): void {}

    async executeJavaScript(script: string): Promise<unknown> {
      if (script.includes('__niraiHoloAutoResume')) {
        this.autoResumeScripts.push(script)
        return this.autoResumeResult
      }
      if (script.includes('__niraiHoloGenerationProbe')) return this.generationBusy
      if (script.includes('Nirai Dive')) return this.bootstrapResult
      if (script.includes('host_ok')) {
        return { host_ok: true, body_ok: true, chrome_ok: true, composer_ok: true }
      }
      return true
    }

    async insertCSS(): Promise<string> {
      return 'holo-skin-css-key'
    }

    async removeInsertedCSS(): Promise<void> {}
  }

  const views: Array<{ webContents: FakeWebContents }> = []

  class FakeWebContentsView {
    readonly webContents = new FakeWebContents()

    constructor() {
      views.push(this)
    }

    setBackgroundColor(): void {}

    setBounds(): void {}
  }

  return {
    ipcListeners: new Map<string, (...args: any[]) => void>(),
    views,
    FakeWebContentsView,
    niraiRoot: ''
  }
})

vi.mock('electron', () => ({
  ipcMain: {
    on: (channel: string, listener: (...args: any[]) => void) => harness.ipcListeners.set(channel, listener),
    removeListener: (channel: string) => harness.ipcListeners.delete(channel)
  },
  BrowserWindow: class {},
  WebContentsView: harness.FakeWebContentsView,
  shell: { openExternal: async (): Promise<void> => undefined }
}))

vi.mock('../../src/main/paths', () => ({
  getNiraiRoot: (): string => harness.niraiRoot
}))

import { HoloAddonHost } from '../../src/main/holo/HoloWebHost'

const OLD_DIVE_URL = 'https://chatgpt.com/c/old-conversation'
const OLD_DIVE_SESSION = 'DIVE-OLD'
const BOUNDS = { x: 0, y: 0, width: 800, height: 600 }

interface FakeWindow {
  window: ConstructorParameters<typeof HoloAddonHost>[0]
  sentFocusEvents: boolean[]
}

function fakeWindow(): FakeWindow {
  const sentFocusEvents: boolean[] = []
  const window = {
    isDestroyed: () => false,
    getContentSize: () => [1280, 720],
    contentView: {
      addChildView: () => undefined,
      removeChildView: () => undefined
    },
    webContents: {
      isDestroyed: () => false,
      send: (channel: string, payload: unknown) => {
        if (channel === 'holo:web-focus-changed') sentFocusEvents.push(payload === true)
      }
    }
  } as unknown as ConstructorParameters<typeof HoloAddonHost>[0]
  return { window, sentFocusEvents }
}

function statePath(): string {
  return join(harness.niraiRoot, 'runtime', 'holo', 'state.json')
}

async function writeSavedState(): Promise<void> {
  await mkdir(join(harness.niraiRoot, 'runtime', 'holo'), { recursive: true })
  await writeFile(statePath(), JSON.stringify({
    current_dive_url: OLD_DIVE_URL,
    current_dive_session_id: OLD_DIVE_SESSION,
    updated_at: '2026-08-31T09:00:00+09:00'
  }), 'utf8')
}

async function readSavedState(): Promise<Record<string, unknown>> {
  return JSON.parse(await readFile(statePath(), 'utf8')) as Record<string, unknown>
}

function taskOwnerPath(taskId: string): string {
  return join(harness.niraiRoot, 'runtime', 'holo', 'task_owners', `${taskId}.json`)
}

async function writeTaskOwner(
  taskId: string,
  diveSessionId = OLD_DIVE_SESSION,
  conversationUrl = OLD_DIVE_URL,
  createdAt = '2026-09-11T12:00:00.000Z'
): Promise<void> {
  const root = join(harness.niraiRoot, 'runtime', 'holo', 'task_owners')
  await mkdir(root, { recursive: true })
  await writeFile(taskOwnerPath(taskId), JSON.stringify({
    version: 1,
    task_id: taskId,
    dive_session_id: diveSessionId,
    conversation_url: conversationUrl,
    created_at: createdAt
  }), 'utf8')
}

async function writeWorkflowLease(overrides: Partial<{
  workflow_id: string
  dive_session_id: string
  conversation_url: string
  label: string
  state: 'active' | 'completed'
  started_at: string
  updated_at: string
  completed_at: string
}> = {}): Promise<void> {
  await mkdir(join(harness.niraiRoot, 'runtime', 'holo'), { recursive: true })
  await writeFile(join(harness.niraiRoot, 'runtime', 'holo', 'workflow.json'), JSON.stringify({
    version: 1,
    workflow_id: 'STALL-1',
    dive_session_id: OLD_DIVE_SESSION,
    conversation_url: OLD_DIVE_URL,
    label: 'watchdog test',
    state: 'active',
    started_at: '2026-09-11T11:58:00.000Z',
    updated_at: '2026-09-11T11:59:00.000Z',
    ...overrides
  }), 'utf8')
}

function createStateIo(overrides: Partial<{
  readText: (path: string) => Promise<string>
  ensureDirectory: (path: string) => Promise<void>
  writeText: (path: string, content: string) => Promise<void>
  renameFile: (source: string, target: string) => Promise<void>
  removeFile: (path: string) => Promise<void>
}> = {}) {
  return {
    readText: (path: string) => readFile(path, 'utf8'),
    ensureDirectory: async (path: string): Promise<void> => { await mkdir(path, { recursive: true }) },
    writeText: async (path: string, content: string): Promise<void> => { await writeFile(path, content, 'utf8') },
    renameFile: (source: string, target: string) => rename(source, target),
    removeFile: async (path: string): Promise<void> => { await unlink(path) },
    ...overrides
  }
}

async function waitFor(assertion: () => Promise<void> | void): Promise<void> {
  let lastError: unknown = null
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try {
      await assertion()
      return
    } catch (error) {
      lastError = error
      await new Promise((resolve) => setTimeout(resolve, 20))
    }
  }
  throw lastError
}

describe('HoloAddonHost lifecycle', () => {
  it('never drains an entry before the enqueue save completes', async () => {
    await writeSavedState()
    await writeTaskOwner('T-SLOW-SAVE')
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    let saving = false
    const host = new HoloAddonHost(fakeWindow().window, createStateIo({
      writeText: async (path, content) => {
        if (!saving && content.includes('T-SLOW-SAVE')) { saving = true; await gate }
        await writeFile(path, content, 'utf8')
      }
    }))
    try {
      const enqueue = host.enqueueAutoResume({ task_id: 'T-SLOW-SAVE', reason: 'failed' })
      await waitFor(() => expect(saving).toBe(true))
      await host.resumePendingAutoResume()
      await realDelay(30)
      expect(harness.views.flatMap((view) => view.webContents.autoResumeScripts)).toHaveLength(0)
      release()
      expect((await enqueue).accepted).toBe(true)
      await waitFor(async () => expect((await readSavedState()).pending_auto_resume).toEqual([]))
    } finally { release(); host.dispose() }
  })

  it('reuses a persisted workflow Delivery Key after restart', async () => {
    await writeSavedState()
    await writeWorkflowLease()
    const trigger = { task_id: 'WF-STALL-1', reason: 'workflow_stalled' as const,
      request_id: '2026-09-11T11:59:00.000Z', dive_session_id: OLD_DIVE_SESSION, conversation_url: OLD_DIVE_URL }
    const first = new HoloAddonHost(fakeWindow().window)
    await first.setSurface(true, BOUNDS)
    harness.views[0].webContents.autoResumeResult = { status: 'busy' }
    await first.enqueueAutoResume(trigger)
    const saved = (await readSavedState()).pending_auto_resume as Array<{ delivery_id: string }>
    expect(saved[0].delivery_id).toMatch(/^[0-9a-f-]{36}$/)
    first.dispose()
    const second = new HoloAddonHost(fakeWindow().window)
    try {
      await second.resumePendingAutoResume()
      await waitFor(async () => expect((await readSavedState()).pending_auto_resume).toEqual([]))
      expect(harness.views.at(-1)?.webContents.autoResumeScripts[0]).toContain(`const deliveryId = "${saved[0].delivery_id}"`)
    } finally { second.dispose() }
  })

  it('retains the same delivery attempt when saving its submitted ACK fails', async () => {
    await writeSavedState()
    await writeWorkflowLease()
    let rejectAck = false
    const host = new HoloAddonHost(fakeWindow().window, createStateIo({
      writeText: async (path, content) => {
        if (rejectAck && JSON.parse(content).pending_auto_resume.length === 0) throw new Error('disk unavailable')
        await writeFile(path, content, 'utf8')
      }
    }))
    try {
      await host.setSurface(true, BOUNDS)
      const contents = harness.views[0].webContents
      const original = contents.executeJavaScript.bind(contents)
      vi.spyOn(contents, 'executeJavaScript').mockImplementation(async (script) => {
        if (script.includes('__niraiHoloAutoResume')) rejectAck = true
        return original(script)
      })
      await host.enqueueAutoResume({ task_id: 'WF-STALL-1', reason: 'workflow_stalled',
        request_id: '2026-09-11T11:59:00.000Z', dive_session_id: OLD_DIVE_SESSION, conversation_url: OLD_DIVE_URL })
      await waitFor(() => expect(host.getStatus().persistence_issue).toBe('state_persistence_failed'))
      expect((await host.taskManagementState()).tasks.some((task) => task.pending_count === 1)).toBe(true)
      const id = ((await readSavedState()).pending_auto_resume as Array<{ delivery_id: string }>)[0].delivery_id
      expect(contents.autoResumeScripts[0]).toContain(`const deliveryId = "${id}"`)
      vi.mocked(contents.executeJavaScript).mockImplementation(original)
      rejectAck = false
      await (host as unknown as { drainAutoResumeQueue(): Promise<void> }).drainAutoResumeQueue()
      await waitFor(async () => expect((await readSavedState()).pending_auto_resume).toEqual([]))
      expect(contents.autoResumeScripts.at(-1)).toContain(`const deliveryId = "${id}"`)
    } finally { host.dispose() }
  })

  it('persists a Conversation stop while Core is offline and retries it after restart', async () => {
    await writeSavedState()
    await writeWorkflowLease()
    await writeTaskOwner('T-BEFORE-STOP')
    const cancelWorkflow = vi.fn(async () => { await writeWorkflowLease({ state: 'completed' }) })
    const offline = vi.fn(async () => { throw new Error('offline') })
    const first = new HoloAddonHost(fakeWindow().window, undefined, undefined, cancelWorkflow, offline)
    await first.stopConversation(OLD_DIVE_URL)
    expect(cancelWorkflow).toHaveBeenCalledWith(OLD_DIVE_SESSION, 'STALL-1')
    expect((await readSavedState()).stopped_conversations).toMatchObject([{ pending: true }])
    expect((await first.enqueueAutoResume({ task_id: 'T-BEFORE-STOP', reason: 'failed' })).discarded).toBe(true)
    first.dispose()
    const online = vi.fn(async () => undefined)
    const second = new HoloAddonHost(fakeWindow().window, undefined, { intervalMs: 5, staleMs: 10, idleGraceMs: 10 }, cancelWorkflow, online)
    try {
      await second.resumePendingAutoResume()
      await waitFor(async () => expect((await readSavedState()).stopped_conversations).toMatchObject([{ pending: false }]))
      expect(online).toHaveBeenCalledTimes(1)
      await writeTaskOwner('T-AFTER-STOP', OLD_DIVE_SESSION, OLD_DIVE_URL, new Date(Date.now() + 1000).toISOString())
      expect((await second.enqueueAutoResume({ task_id: 'T-AFTER-STOP', reason: 'failed' })).accepted).toBe(true)
      await writeTaskOwner('T-OTHER-CONVERSATION', 'DIVE-OTHER', 'https://chatgpt.com/c/other')
      expect((await second.enqueueAutoResume({ task_id: 'T-OTHER-CONVERSATION', reason: 'failed' })).accepted).toBe(true)
    } finally { second.dispose() }
  })

  beforeEach(async () => {
    harness.views.length = 0
    harness.niraiRoot = await mkdtemp(join(tmpdir(), 'nirai-holo-host-'))
  })

  afterEach(async () => {
    vi.useRealTimers()
    const niraiRoot = harness.niraiRoot
    harness.niraiRoot = ''
    if (!niraiRoot) return
    // Host shutdown may still be completing an already-started atomic state
    // rename on Windows. Retry deletion briefly so the test validates Host
    // behavior rather than filesystem timing; each Host is pinned to this root.
    await rm(niraiRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 20 })
  })


  it('keeps the Current Dive when a background owner URL redirects to an equivalent URL', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      await host.setSurface(false)
      const contents = harness.views[0].webContents
      const background = 'https://chatgpt.com/c/background-redirect'
      const load = contents.loadURL.bind(contents)
      vi.spyOn(contents, 'loadURL').mockImplementation((url) => load(
        url === background ? background + '?model=default' : url
      ))
      await host.enqueueAutoResume({
        task_id: 'T-REDIRECT', reason: 'failed', dive_session_id: 'DIVE-BACKGROUND',
        conversation_url: background
      })
      await waitFor(async () => {
        expect((await readSavedState()).pending_auto_resume).toEqual([])
      })
      expect(host.getStatus().current_dive_url).toBe(OLD_DIVE_URL)
      expect(contents.getURL()).toBe(OLD_DIVE_URL)
    } finally {
      host.dispose()
    }
  })

  it('retries a failed page load when a pending continuation is drained', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      const contents = harness.views[0].webContents
      contents.emit('did-fail-load', {}, -105, 'ERR_NAME_NOT_RESOLVED', OLD_DIVE_URL, true)
      await host.enqueueAutoResume({
        task_id: 'T-LOAD-RETRY', reason: 'failed', dive_session_id: OLD_DIVE_SESSION,
        conversation_url: OLD_DIVE_URL
      })
      await waitFor(async () => {
        expect((await readSavedState()).pending_auto_resume).toEqual([])
      })
      expect(contents.loadedUrls).toEqual([OLD_DIVE_URL, OLD_DIVE_URL])
    } finally {
      host.dispose()
    }
  })

  it.each([false, true])('restarts workflow monitoring after discarding a stale queued lease (visible=%s)', async (visible) => {
    await writeSavedState()
    const oldRevision = '2000-01-01T00:00:00.000Z'
    await writeFile(statePath(), JSON.stringify({
      current_dive_url: OLD_DIVE_URL, current_dive_session_id: OLD_DIVE_SESSION,
      pending_auto_resume: [{
        task_id: 'WF-STALL-1', reason: 'workflow_stalled', request_id: oldRevision,
        dive_session_id: OLD_DIVE_SESSION, conversation_url: OLD_DIVE_URL
      }]
    }), 'utf8')
    await writeWorkflowLease({ updated_at: '2001-01-01T00:00:00.000Z' })
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 10, staleMs: 1, idleGraceMs: 1
    })
    try {
      if (visible) await host.setSurface(true, BOUNDS)
      await host.resumePendingAutoResume()
      await waitFor(() => {
        expect(harness.views[0]?.webContents.autoResumeScripts.some(
          (script) => script.includes('2001-01-01')
        )).toBe(true)
      })
      await waitFor(async () => {
        expect((await readSavedState()).pending_auto_resume).toEqual([])
      })
    } finally {
      host.dispose()
    }
  })

  it('releases a hung generation probe so Task delivery can continue', async () => {
    await writeSavedState()
    await writeWorkflowLease()
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      const contents = harness.views[0].webContents
      const execute = contents.executeJavaScript.bind(contents)
      let probed = false
      vi.spyOn(contents, 'executeJavaScript').mockImplementation((script) => {
        if (script.includes('__niraiHoloGenerationProbe')) {
          probed = true
          return new Promise(() => {})
        }
        return execute(script)
      })
      const internals = host as unknown as {
        runWorkflowWatchdog(): Promise<void>
        drainAutoResumeQueue(): Promise<void>
        workflowWatchdogRunning: boolean
      }
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
      void internals.runWorkflowWatchdog()
      // IO still uses the real filesystem; yield without advancing retry timers.
      for (let i = 0; i < 100 && !probed; i += 1) await realDelay(10)
      expect(probed).toBe(true)
      await host.enqueueAutoResume({
        task_id: 'T-PROBE-HANG', reason: 'failed', dive_session_id: OLD_DIVE_SESSION,
        conversation_url: OLD_DIVE_URL
      })
      await vi.advanceTimersByTimeAsync(12_100)
      expect(internals.workflowWatchdogRunning).toBe(false)
      vi.useRealTimers()
      await internals.drainAutoResumeQueue()
      await waitFor(async () => expect((await readSavedState()).pending_auto_resume).toEqual([]))
    } finally {
      host.dispose()
    }
  })

  it('retains a completed Review owner until Core receives the delivery acknowledgement', async () => {
    await writeSavedState()
    await writeTaskOwner('HR-ACK-OWNER')
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      expect(await host.enqueueAutoResume({
        kind: 'review', task_id: 'HR-ACK-OWNER', agent_session_id: 'AS-ACK-OWNER', reason: 'done'
      })).toMatchObject({ accepted: true })
      expect(JSON.parse(await readFile(taskOwnerPath('HR-ACK-OWNER'), 'utf8')).task_id).toBe('HR-ACK-OWNER')
      await waitFor(async () => expect((await readSavedState()).pending_auto_resume).toEqual([]))
    } finally {
      host.dispose()
    }
  })

  it('does not remove the next event when a new Dive prunes an in-flight terminal event', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      vi.useFakeTimers()
      const contents = harness.views[0].webContents
      const execute = contents.executeJavaScript.bind(contents)
      vi.spyOn(contents, 'executeJavaScript').mockImplementation(async (script) => {
        if (!script.includes('__niraiHoloAutoResume')) return execute(script)
        await host.prepareDive()
        await contents.loadURL('https://chatgpt.com/c/new-dive')
        return { status: 'submitted' }
      })
      await host.enqueueAutoResume({
        task_id: 'T-INFLIGHT-DONE', reason: 'done', dive_session_id: OLD_DIVE_SESSION,
        conversation_url: OLD_DIVE_URL
      })
      await host.enqueueAutoResume({
        task_id: 'T-NEXT-FAILURE', reason: 'failed', dive_session_id: OLD_DIVE_SESSION,
        conversation_url: OLD_DIVE_URL
      })
      await (host as unknown as { drainAutoResumeQueue(): Promise<void> }).drainAutoResumeQueue()
      expect((await readSavedState()).pending_auto_resume).toEqual([
        expect.objectContaining({ task_id: 'T-NEXT-FAILURE' })
      ])
    } finally {
      host.dispose()
    }
  })

  it('lists pending Auto Resume work for the Tasks surface', async () => {
    await writeSavedState()
    await writeTaskOwner('T-MANAGE-LIST')
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      expect(await host.enqueueAutoResume({
        task_id: 'T-MANAGE-LIST', agent_session_id: 'AS-MANAGE-LIST', reason: 'failed'
      })).toMatchObject({ accepted: true })
      const state = await host.taskManagementState()
      expect(state.tasks).toEqual(expect.arrayContaining([
        expect.objectContaining({
          task_id: 'T-MANAGE-LIST', state: 'failed', kind: 'task', pending_count: 1
        })
      ]))
    } finally {
      host.dispose()
    }
  })

  it('cancels pending Auto Resume durably and rejects later events for the same Task', async () => {
    await writeSavedState()
    await writeTaskOwner('T-MANAGE-CANCEL')
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      expect(await host.enqueueAutoResume({
        task_id: 'T-MANAGE-CANCEL', agent_session_id: 'AS-MANAGE-CANCEL', reason: 'failed'
      })).toMatchObject({ accepted: true })
      await host.cancelAutoResume('T-MANAGE-CANCEL')

      const state = await host.taskManagementState()
      expect(state.tasks.some((task) => task.task_id === 'T-MANAGE-CANCEL')).toBe(false)
      expect(state.cancelled_task_ids).toContain('T-MANAGE-CANCEL')
      expect(await host.enqueueAutoResume({
        task_id: 'T-MANAGE-CANCEL', agent_session_id: 'AS-MANAGE-LATE', reason: 'done'
      })).toMatchObject({ accepted: false, discarded: true })
      expect((await readSavedState()).cancelled_auto_resume_task_ids).toContain('T-MANAGE-CANCEL')
    } finally {
      host.dispose()
    }
  })

  it('routes workflow cancellation through the exact-ID Local Client writer', async () => {
    await writeSavedState()
    await writeWorkflowLease({ workflow_id: 'CANCEL-LEASE' })
    const cancellation = vi.fn(async (diveSessionId: string, workflowId: string) => {
      expect(diveSessionId).toBe(OLD_DIVE_SESSION)
      expect(workflowId).toBe('CANCEL-LEASE')
      const path = join(harness.niraiRoot, 'runtime', 'holo', 'workflow.json')
      const lease = JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>
      await writeFile(path, JSON.stringify({
        ...lease,
        state: 'completed',
        completed_at: '2026-09-14T00:00:00.000Z',
        updated_at: '2026-09-14T00:00:00.000Z',
        completion_reason: 'cancelled_by_master'
      }), 'utf8')
    })
    const host = new HoloAddonHost(fakeWindow().window, undefined, undefined, cancellation)
    try {
      await host.cancelAutoResume('WF-CANCEL-LEASE')
      expect(cancellation).toHaveBeenCalledTimes(1)
      const lease = JSON.parse(await readFile(
        join(harness.niraiRoot, 'runtime', 'holo', 'workflow.json'), 'utf8'
      )) as Record<string, unknown>
      expect(lease.state).toBe('completed')
      expect((await readSavedState()).cancelled_auto_resume_task_ids).toContain('WF-CANCEL-LEASE')
    } finally {
      host.dispose()
    }
  })

  it('does not persist a workflow cancellation tombstone when the exact-ID writer rejects it', async () => {
    await writeSavedState()
    await writeWorkflowLease({ workflow_id: 'STALE-CANCEL' })
    const cancellation = vi.fn(async () => { throw new Error('workflow_id does not match') })
    const host = new HoloAddonHost(fakeWindow().window, undefined, undefined, cancellation)
    try {
      await expect(host.cancelAutoResume('WF-STALE-CANCEL')).rejects.toThrow('workflow_id does not match')
      expect((await readSavedState()).cancelled_auto_resume_task_ids ?? []).not.toContain('WF-STALE-CANCEL')
      const lease = JSON.parse(await readFile(
        join(harness.niraiRoot, 'runtime', 'holo', 'workflow.json'), 'utf8'
      )) as Record<string, unknown>
      expect(lease.state).toBe('active')
    } finally {
      host.dispose()
    }
  })

  it('accepts safe custom Core Task IDs for durable Tasks dismissal across restart', async () => {
    await writeSavedState()
    const taskIds = [
      'M4-CURSOR-SMOKE-LIVE',
      'M4-ANTIGRAVITY-SMOKE-LIVE',
      'M4-CURSOR-ESCAPE-SMOKE'
    ]
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      for (const taskId of taskIds) await host.cancelAutoResume(taskId)
      expect((await readSavedState()).cancelled_auto_resume_task_ids).toEqual(
        expect.arrayContaining(taskIds)
      )
    } finally {
      host.dispose()
    }

    const restored = new HoloAddonHost(fakeWindow().window)
    try {
      expect((await restored.taskManagementState()).cancelled_task_ids).toEqual(
        expect.arrayContaining(taskIds)
      )
      await expect(restored.cancelAutoResume('../escape')).rejects.toThrow('Invalid Task ID')
    } finally {
      restored.dispose()
    }
  })

  it('restores the saved conversation when the surface first opens', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)

    const status = await host.setSurface(true, BOUNDS)

    expect(harness.views[0].webContents.loadedUrls).toEqual([OLD_DIVE_URL])
    expect(status.dive_state).toBe('current')
    expect(status.current_dive_url).toBe(OLD_DIVE_URL)
    expect(status.current_dive_session_id).toBe(OLD_DIVE_SESSION)
    expect(status.web_state).toBe('ready')
  })

  it('tracks the Conversation Master is currently viewing within the same Dive Session', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      const currentConversation = 'https://chatgpt.com/c/another-conversation'
      await harness.views[0].webContents.loadURL(currentConversation)

      await waitFor(async () => {
        const saved = await readSavedState()
        expect(saved.current_dive_url).toBe(currentConversation)
        expect(saved.current_dive_session_id).toBe(OLD_DIVE_SESSION)
        expect((saved.known_dive_urls as Record<string, string>)[OLD_DIVE_SESSION]).toBe(currentConversation)
      })
    } finally {
      host.dispose()
    }
  })

  it('auto-resumes the owning Dive once and deduplicates the same Task event', async () => {
    await writeSavedState()
    await writeTaskOwner('T-AUTO-1')
    const host = new HoloAddonHost(fakeWindow().window)
    const trigger = {
      task_id: 'T-AUTO-1',
      agent_session_id: 'AS-AUTO-1',
      reason: 'done' as const
    }

    const first = await host.enqueueAutoResume(trigger)
    expect(first.accepted).toBe(true)
    expect(first.duplicate).toBe(false)

    await waitFor(() => {
      expect(harness.views).toHaveLength(1)
      expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
    })
    await waitFor(async () => {
      const saved = await readSavedState()
      expect(saved.pending_auto_resume).toEqual([])
      expect(saved.processed_auto_resume_keys).toContain('T-AUTO-1:AS-AUTO-1:done:-')
      await expect(readFile(taskOwnerPath('T-AUTO-1'), 'utf8')).rejects.toMatchObject({ code: 'ENOENT' })
    })

    const duplicate = await host.enqueueAutoResume(trigger)
    expect(duplicate.accepted).toBe(false)
    expect(duplicate.duplicate).toBe(true)
    expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
    host.dispose()
  })

  it('auto-resumes while the visible owner Conversation has only query or hash URL differences', async () => {
    await writeSavedState()
    await writeTaskOwner('T-AUTO-URL-VARIANT')
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      const contents = harness.views[0].webContents
      await contents.loadURL(`${OLD_DIVE_URL}?model=gpt-5#latest`)

      const result = await host.enqueueAutoResume({
        task_id: 'T-AUTO-URL-VARIANT',
        agent_session_id: 'AS-AUTO-URL-VARIANT',
        reason: 'done'
      })
      expect(result.accepted).toBe(true)

      await waitFor(() => {
        expect(contents.autoResumeScripts).toHaveLength(1)
      })
      await waitFor(async () => {
        const saved = await readSavedState()
        expect(saved.pending_auto_resume).toEqual([])
        expect(saved.processed_auto_resume_keys).toContain(
          'T-AUTO-URL-VARIANT:AS-AUTO-URL-VARIANT:done:-'
        )
      })
    } finally {
      host.dispose()
    }
  })

  it('immediate Auto Resume wake replaces an already-armed retry backoff', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    const internals = host as unknown as {
      scheduleAutoResumeDrain(delayMs: number): void
      scheduleImmediateAutoResumeDrain(): void
      drainAutoResumeQueue(): Promise<void>
    }
    try {
      vi.useFakeTimers()
      let drainCount = 0
      vi.spyOn(internals, 'drainAutoResumeQueue').mockImplementation(async () => {
        drainCount += 1
      })

      internals.scheduleAutoResumeDrain(1000)
      internals.scheduleImmediateAutoResumeDrain()
      await vi.advanceTimersByTimeAsync(0)
      expect(drainCount).toBe(1)

      await vi.advanceTimersByTimeAsync(1000)
      expect(drainCount).toBe(1)
    } finally {
      host.dispose()
    }
  })

  it('ordinary zero-delay scheduling does not replace an existing retry backoff', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    const internals = host as unknown as {
      scheduleAutoResumeDrain(delayMs: number): void
      drainAutoResumeQueue(): Promise<void>
    }
    try {
      vi.useFakeTimers()
      let drainCount = 0
      vi.spyOn(internals, 'drainAutoResumeQueue').mockImplementation(async () => {
        drainCount += 1
      })

      internals.scheduleAutoResumeDrain(1000)
      internals.scheduleAutoResumeDrain(0)
      await vi.advanceTimersByTimeAsync(0)
      expect(drainCount).toBe(0)

      await vi.advanceTimersByTimeAsync(999)
      expect(drainCount).toBe(0)
      await vi.advanceTimersByTimeAsync(1)
      expect(drainCount).toBe(1)
    } finally {
      host.dispose()
    }
  })

  it('binds work to the Conversation where it starts and resumes when Master returns to it', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      const contents = harness.views[0].webContents
      const ownerConversation = 'https://chatgpt.com/c/task-owner-conversation'
      const otherConversation = 'https://chatgpt.com/c/other-visible-conversation'

      // Master manually moves to B. This becomes the current routing context
      // used by Local Client when work starts.
      await contents.loadURL(ownerConversation)
      await waitFor(async () => {
        expect((await readSavedState()).current_dive_url).toBe(ownerConversation)
      })
      await writeTaskOwner('T-AUTO-RETURN', OLD_DIVE_SESSION, ownerConversation)

      // Master then moves to C while B-owned work is still running.
      await contents.loadURL(otherConversation)
      await waitFor(async () => {
        expect((await readSavedState()).current_dive_url).toBe(otherConversation)
      })

      const result = await host.enqueueAutoResume({
        task_id: 'T-AUTO-RETURN',
        agent_session_id: 'AS-AUTO-RETURN',
        reason: 'done'
      })
      expect(result.accepted).toBe(true)

      await new Promise((resolve) => setTimeout(resolve, 25))
      expect(contents.getURL()).toBe(otherConversation)
      expect(contents.autoResumeScripts).toHaveLength(0)
      expect((await readSavedState()).pending_auto_resume).toHaveLength(1)

      // Returning to B must wake the pending Resume immediately without
      // rewriting the already-persisted Task owner.
      await contents.loadURL(ownerConversation)

      await waitFor(() => {
        expect(contents.autoResumeScripts).toHaveLength(1)
      })
      await waitFor(async () => {
        const saved = await readSavedState()
        expect(saved.current_dive_url).toBe(ownerConversation)
        expect(saved.pending_auto_resume).toEqual([])
        expect(saved.processed_auto_resume_keys).toContain(
          'T-AUTO-RETURN:AS-AUTO-RETURN:done:-'
        )
      })
    } finally {
      host.dispose()
    }
  })

  it('restores the Current Dive from ChatGPT Home before delivering a visible pending resume', async () => {
    await writeSavedState()
    await writeTaskOwner('T-AUTO-HOME')
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      const contents = harness.views[0].webContents
      await contents.loadURL('https://chatgpt.com/')

      const result = await host.enqueueAutoResume({
        task_id: 'T-AUTO-HOME',
        agent_session_id: 'AS-AUTO-HOME',
        reason: 'done'
      })
      expect(result.accepted).toBe(true)

      await waitFor(() => {
        expect(contents.loadedUrls.at(-1)).toBe(OLD_DIVE_URL)
        expect(contents.autoResumeScripts).toHaveLength(1)
      })
      await waitFor(async () => {
        const saved = await readSavedState()
        expect(saved.pending_auto_resume).toEqual([])
        expect(saved.processed_auto_resume_keys).toContain('T-AUTO-HOME:AS-AUTO-HOME:done:-')
      })
    } finally {
      host.dispose()
    }
  })

  it('keeps draft backoff when Auto Resume internally restores the owner Conversation from Home', async () => {
    await writeSavedState()
    await writeTaskOwner('T-AUTO-HOME-DRAFT')
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      const contents = harness.views[0].webContents
      await contents.loadURL('https://chatgpt.com/')
      contents.autoResumeResult = { status: 'draft_present' }

      const result = await host.enqueueAutoResume({
        task_id: 'T-AUTO-HOME-DRAFT',
        agent_session_id: 'AS-AUTO-HOME-DRAFT',
        reason: 'failed'
      })
      expect(result.accepted).toBe(true)

      await waitFor(() => {
        expect(contents.loadedUrls.at(-1)).toBe(OLD_DIVE_URL)
        expect(contents.autoResumeScripts).toHaveLength(1)
      })
      // An internal Home -> owner restore used to look like a Master return and
      // collapse the 5s draft backoff into an immediate retry loop.
      await realDelay(100)
      expect(contents.autoResumeScripts).toHaveLength(1)
      expect((await readSavedState()).pending_auto_resume).toEqual([
        expect.objectContaining({ task_id: 'T-AUTO-HOME-DRAFT', reason: 'failed' })
      ])
    } finally {
      host.dispose()
    }
  })

  it('routes an owned Review resume through the same durable Conversation queue', async () => {
    await writeSavedState()
    const backgroundUrl = 'https://chatgpt.com/c/review-owner'
    await writeTaskOwner('HR-AUTO-1', 'DIVE-REVIEW', backgroundUrl)
    const host = new HoloAddonHost(fakeWindow().window)
    const trigger = {
      kind: 'review' as const,
      task_id: 'HR-AUTO-1',
      agent_session_id: 'AS-HR-AUTO-1',
      reason: 'failed' as const
    }

    const first = await host.enqueueAutoResume(trigger)
    expect(first.accepted).toBe(true)
    expect(first.duplicate).toBe(false)

    await waitFor(() => {
      expect(harness.views).toHaveLength(1)
      expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
    })
    await waitFor(async () => {
      const saved = await readSavedState()
      expect(saved.pending_auto_resume).toEqual([])
      expect(saved.processed_auto_resume_keys).toContain('review:HR-AUTO-1:AS-HR-AUTO-1:failed:-')
    })
    expect(harness.views[0].webContents.loadedUrls).toContain(backgroundUrl)
    expect(harness.views[0].webContents.autoResumeScripts[0]).toContain('review-wait AS-HR-AUTO-1 0')

    const duplicate = await host.enqueueAutoResume(trigger)
    expect(duplicate.accepted).toBe(false)
    expect(duplicate.duplicate).toBe(true)
    host.dispose()
  })

  it('does not acknowledge an auto-resume until its queue entry is durably persisted', async () => {
    await writeSavedState()
    await writeTaskOwner('T-PERSIST-FAIL')
    const host = new HoloAddonHost(fakeWindow().window, createStateIo({
      writeText: async () => { throw new Error('simulated state persistence failure') }
    }))
    try {
      const result = await host.enqueueAutoResume({ task_id: 'T-PERSIST-FAIL', reason: 'done' })
      expect(result.accepted).toBe(false)
      expect(result.duplicate).toBe(false)
      expect(result.pending_count).toBe(0)
      expect((await readSavedState()).pending_auto_resume ?? []).toEqual([])
    } finally {
      host.dispose()
    }
  })

  it.each(['busy', 'draft_present', 'not_ready', 'throws'])('restores the Current Dive after background failure delivery returns %s', async (status) => {
    await writeSavedState()
    await writeTaskOwner('T-RETRY', 'DIVE-BACKGROUND', 'https://chatgpt.com/c/background-retry')
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      await host.setSurface(false)
      const contents = harness.views[0].webContents
      const original = contents.executeJavaScript.bind(contents)
      vi.spyOn(contents, 'executeJavaScript').mockImplementation(async (script) => {
        if (script.includes('__niraiHoloAutoResume') && status === 'throws') {
          contents.autoResumeScripts.push(script)
          throw new Error('navigation interrupted execution')
        }
        return original(script)
      })
      contents.autoResumeResult = { status }
      await host.enqueueAutoResume({ task_id: 'T-RETRY', reason: 'failed' })
      await waitFor(() => {
        expect(contents.autoResumeScripts).toHaveLength(1)
        expect(contents.getURL()).toBe(OLD_DIVE_URL)
      })
      const saved = await readSavedState()
      expect(saved.current_dive_url).toBe(OLD_DIVE_URL)
      expect(saved.pending_auto_resume).toHaveLength(1)
    } finally {
      host.dispose()
    }
  })

  it('shares the initial persisted-state read with concurrent surface startup', async () => {
    await writeSavedState()
    let releaseRead!: () => void
    const readBlocked = new Promise<void>((resolve) => { releaseRead = resolve })
    const host = new HoloAddonHost(fakeWindow().window, createStateIo({
      readText: async (path) => {
        if (path === statePath()) await readBlocked
        return readFile(path, 'utf8')
      }
    }))
    try {
      const restore = host.resumePendingAutoResume()
      const show = host.setSurface(true, BOUNDS)
      releaseRead()
      await Promise.all([restore, show])
      expect(harness.views[0].webContents.loadedUrls).toEqual([OLD_DIVE_URL])
      expect(host.getStatus().current_dive_session_id).toBe(OLD_DIVE_SESSION)
    } finally {
      host.dispose()
    }
  })

  it('does not recover active work, but recovers it once observed activity stops', async () => {
    await writeSavedState()
    await writeWorkflowLease({ updated_at: new Date().toISOString() })
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 5, staleMs: 60_000, idleGraceMs: 5, retryMs: 60_000
    })
    try {
      await host.setSurface(true, BOUNDS)
      await host.resumePendingAutoResume()
      await realDelay(35)
      expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(0)
      await writeWorkflowLease({ updated_at: '2000-01-01T00:00:00.000Z' })
      await waitFor(() => expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1))
      await realDelay(40)
      expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
      await writeWorkflowLease({ state: 'completed', updated_at: new Date().toISOString() })
      await realDelay(35)
      expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
      expect((await readSavedState()).pending_auto_resume).toEqual([])
    } finally { host.dispose() }
  })

  it.each(['active', 'completed'] as const)('drops stale Resume when work becomes %s during owner navigation', async (state) => {
    await writeSavedState()
    await writeWorkflowLease({ conversation_url: 'https://chatgpt.com/c/background', updated_at: '2000-01-01T00:00:00.000Z' })
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      await host.setSurface(false, BOUNDS)
      const contents = harness.views[0].webContents
      const load = contents.loadURL.bind(contents)
      vi.spyOn(contents, 'loadURL').mockImplementation(async (url) => {
        await load(url)
        if (url.endsWith('/background')) {
          await writeWorkflowLease({ state, conversation_url: url, updated_at: new Date().toISOString() })
        }
      })
      await host.enqueueAutoResume({
        task_id: 'WF-STALL-1', reason: 'workflow_stalled', request_id: '2000-01-01T00:00:00.000Z',
        dive_session_id: OLD_DIVE_SESSION, conversation_url: 'https://chatgpt.com/c/background'
      })
      await waitFor(async () => expect((await readSavedState()).pending_auto_resume).toEqual([]))
      expect(contents.autoResumeScripts).toHaveLength(0)
    } finally { host.dispose() }
  })

  it.each(['completed', 'superseded'])('does not revive a %s workflow for a late failed owned Task', async (caseName) => {
    await writeSavedState()
    await writeWorkflowLease(caseName === 'completed' ? { state: 'completed' } : {
      workflow_id: 'REPLACEMENT', updated_at: new Date().toISOString()
    })
    await writeTaskOwner('T-LATE')
    const owner = JSON.parse(await readFile(taskOwnerPath('T-LATE'), 'utf8'))
    await writeFile(taskOwnerPath('T-LATE'), JSON.stringify({ ...owner, workflow_id: 'STALL-1' }))
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      const result = await host.enqueueAutoResume({ task_id: 'T-LATE', reason: 'failed' })
      expect(result.accepted).toBe(true)
      expect(result.pending_count).toBe(0)
      expect((await readSavedState()).pending_auto_resume).toEqual([])
      await expect(readFile(taskOwnerPath('T-LATE'), 'utf8')).rejects.toMatchObject({ code: 'ENOENT' })
    } finally { host.dispose() }
  })

  it('retains pending Task ownership until delivery and discards it if its workflow completes first', async () => {
    await writeSavedState()
    await writeWorkflowLease({ updated_at: new Date().toISOString() })
    await writeTaskOwner('T-PENDING-COMPLETE')
    const owner = JSON.parse(await readFile(taskOwnerPath('T-PENDING-COMPLETE'), 'utf8'))
    await writeFile(taskOwnerPath('T-PENDING-COMPLETE'), JSON.stringify({ ...owner, workflow_id: 'STALL-1' }))
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.setSurface(true, BOUNDS)
      const contents = harness.views[0].webContents
      contents.autoResumeResult = { status: 'busy' }
      await host.enqueueAutoResume({ task_id: 'T-PENDING-COMPLETE', reason: 'done' })
      await waitFor(() => expect(contents.autoResumeScripts).toHaveLength(1))
      expect(await readFile(taskOwnerPath('T-PENDING-COMPLETE'), 'utf8')).toContain('STALL-1')
      await writeWorkflowLease({ state: 'completed', updated_at: new Date().toISOString() })
      await host.resumePendingAutoResume()
      await waitFor(async () => expect((await readSavedState()).pending_auto_resume).toEqual([]))
      await expect(readFile(taskOwnerPath('T-PENDING-COMPLETE'), 'utf8')).rejects.toMatchObject({ code: 'ENOENT' })
      expect(contents.autoResumeScripts).toHaveLength(1)
    } finally { host.dispose() }
  })

  it('re-arms a stale active Holo workflow when its submitted continuation dies before activity', async () => {
    await writeSavedState()
    await writeWorkflowLease({ updated_at: '2000-01-01T00:00:00.000Z' })
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 5,
      staleMs: 10,
      idleGraceMs: 5,
      retryMs: 25
    })

    expect(await host.resumePendingAutoResume()).toBe(0)
    await waitFor(() => {
      expect(harness.views).toHaveLength(1)
      expect(harness.views[0].webContents.autoResumeScripts.length).toBeGreaterThanOrEqual(2)
    })
    const firstTwoScripts = harness.views[0].webContents.autoResumeScripts.slice(0, 2)
    const deliveryIds = firstTwoScripts.map((script) => (
      script.match(/const deliveryId = "([^"]+)";/)?.[1] ?? null
    ))
    expect(deliveryIds[0]).toMatch(/^[0-9a-f-]{36}$/)
    expect(deliveryIds[1]).toMatch(/^[0-9a-f-]{36}$/)
    expect(deliveryIds[1]).not.toBe(deliveryIds[0])
    for (const script of firstTwoScripts) {
      expect(script).toContain('WF-STALL-1:-:workflow_stalled:2000-01-01T00:00:00.000Z')
    }

    await waitFor(async () => {
      const saved = await readSavedState()
      expect(saved.pending_auto_resume).toEqual([])
      expect((saved.processed_auto_resume_keys as string[]).some((key) => (
        key.includes('WF-STALL-1') && key.includes(':workflow_stalled:')
      ))).toBe(false)
    })
    host.dispose()
  })

  it('prunes obsolete done/cancelled triggers from older Dives and still resumes the visible workflow', async () => {
    await writeSavedState()
    await writeWorkflowLease({ updated_at: '2000-01-01T00:00:00.000Z' })
    await writeTaskOwner('T-OTHER-DONE', 'DIVE-OTHER', 'https://chatgpt.com/c/other-conversation')
    await writeTaskOwner('T-OTHER-CANCELLED', 'DIVE-OTHER', 'https://chatgpt.com/c/other-conversation')
    await writeFile(statePath(), JSON.stringify({
      current_dive_url: OLD_DIVE_URL,
      current_dive_session_id: OLD_DIVE_SESSION,
      known_dive_urls: {
        [OLD_DIVE_SESSION]: OLD_DIVE_URL,
        'DIVE-OTHER': 'https://chatgpt.com/c/other-conversation'
      },
      pending_auto_resume: [
        {
          task_id: 'T-OTHER-DONE',
          agent_session_id: 'AS-OTHER-DONE',
          reason: 'done',
          dive_session_id: 'DIVE-OTHER',
          conversation_url: 'https://chatgpt.com/c/other-conversation'
        },
        {
          task_id: 'T-OTHER-CANCELLED',
          agent_session_id: 'AS-OTHER-CANCELLED',
          reason: 'cancelled',
          dive_session_id: 'DIVE-OTHER',
          conversation_url: 'https://chatgpt.com/c/other-conversation'
        }
      ]
    }), 'utf8')
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 5,
      staleMs: 10,
      idleGraceMs: 10
    })
    try {
      await host.setSurface(true, BOUNDS)
      expect(await host.resumePendingAutoResume()).toBe(0)

      await waitFor(() => {
        expect(harness.views[0].webContents.autoResumeScripts.some((script) => (
          script.includes('WF-STALL-1') && script.includes('workflow_stalled')
        ))).toBe(true)
      })

      await waitFor(async () => {
        const saved = await readSavedState()
        expect(saved.pending_auto_resume).toEqual([])
        const processed = saved.processed_auto_resume_keys as string[]
        expect(processed.some((key) => key.includes('T-OTHER-DONE') && key.includes(':done:'))).toBe(true)
        expect(processed.some((key) => key.includes('T-OTHER-CANCELLED') && key.includes(':cancelled:'))).toBe(true)
        expect(processed.some((key) => key.includes('WF-STALL-1') && key.includes(':workflow_stalled:'))).toBe(false)
        await expect(readFile(taskOwnerPath('T-OTHER-DONE'), 'utf8')).rejects.toMatchObject({ code: 'ENOENT' })
        await expect(readFile(taskOwnerPath('T-OTHER-CANCELLED'), 'utf8')).rejects.toMatchObject({ code: 'ENOENT' })
      })
    } finally {
      host.dispose()
    }
  })

  it('drops Review terminal events that predate completion of their owning workflow', async () => {
    await writeSavedState()
    await writeTaskOwner(
      'HR-WORKFLOW-OLD',
      OLD_DIVE_SESSION,
      OLD_DIVE_URL,
      '2026-09-12T09:00:00.000Z'
    )
    await writeWorkflowLease({
      state: 'completed',
      updated_at: '2026-09-12T10:00:00.000Z',
      completed_at: '2026-09-12T10:00:00.000Z'
    })
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      const result = await host.enqueueAutoResume({
        kind: 'review', task_id: 'HR-WORKFLOW-OLD', agent_session_id: 'AS-WORKFLOW-OLD', reason: 'failed'
      })
      expect(result).toEqual({ accepted: true, duplicate: false, pending_count: 0 })
      expect(harness.views).toHaveLength(0)
      const saved = await readSavedState()
      expect(saved.pending_auto_resume).toEqual([])
      expect(saved.processed_auto_resume_keys).toContain(
        'review:HR-WORKFLOW-OLD:AS-WORKFLOW-OLD:failed:-'
      )
    } finally {
      host.dispose()
    }
  })

  it('keeps a Review started after workflow completion eligible for delivery', async () => {
    await writeSavedState()
    await writeWorkflowLease({
      state: 'completed',
      updated_at: '2026-09-12T10:00:00.000Z',
      completed_at: '2026-09-12T10:00:00.000Z'
    })
    await writeTaskOwner(
      'HR-WORKFLOW-NEW',
      OLD_DIVE_SESSION,
      OLD_DIVE_URL,
      '2026-09-12T11:00:00.000Z'
    )
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      expect(await host.enqueueAutoResume({
        kind: 'review', task_id: 'HR-WORKFLOW-NEW', agent_session_id: 'AS-WORKFLOW-NEW', reason: 'failed'
      })).toMatchObject({ accepted: true })
      await waitFor(() => {
        expect(harness.views).toHaveLength(1)
        expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
      })
      await waitFor(async () => {
        expect((await readSavedState()).pending_auto_resume).toEqual([])
      })
    } finally {
      host.dispose()
    }
  })

  it('keeps older Dive failures that still require Commander or Master judgment', async () => {
    await writeSavedState()
    await writeFile(statePath(), JSON.stringify({
      current_dive_url: OLD_DIVE_URL,
      current_dive_session_id: OLD_DIVE_SESSION,
      pending_auto_resume: [{
        task_id: 'T-OTHER-FAILED',
        agent_session_id: 'AS-OTHER-FAILED',
        reason: 'failed',
        dive_session_id: 'DIVE-OTHER',
        conversation_url: 'https://chatgpt.com/c/other-conversation'
      }]
    }), 'utf8')
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      expect(await host.resumePendingAutoResume()).toBe(1)
      const saved = await readSavedState()
      expect(saved.pending_auto_resume).toEqual([expect.objectContaining({
        task_id: 'T-OTHER-FAILED',
        reason: 'failed'
      })])
    } finally {
      host.dispose()
    }
  })

  it('acknowledges a late obsolete done trigger without queuing it', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      expect(await host.resumePendingAutoResume()).toBe(0)
      const result = await host.enqueueAutoResume({
        task_id: 'T-LATE-DONE',
        agent_session_id: 'AS-LATE-DONE',
        reason: 'done',
        dive_session_id: 'DIVE-OTHER',
        conversation_url: 'https://chatgpt.com/c/other-conversation'
      })
      expect(result).toEqual({ accepted: true, duplicate: false, pending_count: 0 })
      const saved = await readSavedState()
      expect(saved.pending_auto_resume).toEqual([])
      expect((saved.processed_auto_resume_keys as string[]).some((key) => (
        key.includes('T-LATE-DONE') && key.includes(':done:')
      ))).toBe(true)
    } finally {
      host.dispose()
    }
  })

  it('does not acknowledge an obsolete trigger until its processed key is saved', async () => {
    await writeSavedState()
    let failWrites = true
    const host = new HoloAddonHost(fakeWindow().window, createStateIo({
      writeText: async (path, content) => {
        if (failWrites) throw new Error('simulated disk failure')
        await writeFile(path, content, 'utf8')
      }
    }))
    const trigger = {
      kind: 'review' as const,
      task_id: 'HR-OBSOLETE',
      agent_session_id: 'AS-OBSOLETE',
      reason: 'done' as const,
      dive_session_id: 'DIVE-OTHER',
      conversation_url: 'https://chatgpt.com/c/other-conversation'
    }
    try {
      expect(await host.enqueueAutoResume(trigger)).toMatchObject({ accepted: false, duplicate: false })
      expect(await host.enqueueAutoResume(trigger)).toMatchObject({ accepted: false, duplicate: false })
      failWrites = false
      expect(await host.enqueueAutoResume(trigger)).toMatchObject({ accepted: true })
      expect((await readSavedState()).processed_auto_resume_keys).toContain('review:HR-OBSOLETE:AS-OBSOLETE:done:-')
    } finally {
      host.dispose()
    }
  })

  it('discards a queued workflow continuation if the workflow has since completed', async () => {
    await writeSavedState()
    const revision = '2000-01-01T00:00:00.000Z'
    await writeWorkflowLease({ state: 'completed', updated_at: revision })
    await writeFile(statePath(), JSON.stringify({
      current_dive_url: OLD_DIVE_URL,
      current_dive_session_id: OLD_DIVE_SESSION,
      pending_auto_resume: [{
        task_id: 'WF-STALL-1', reason: 'workflow_stalled', request_id: revision,
        dive_session_id: OLD_DIVE_SESSION, conversation_url: OLD_DIVE_URL
      }]
    }), 'utf8')
    const host = new HoloAddonHost(fakeWindow().window)
    try {
      await host.resumePendingAutoResume()
      await waitFor(async () => {
        expect((await readSavedState()).pending_auto_resume).toEqual([])
      })
      expect(harness.views).toHaveLength(0)
    } finally {
      host.dispose()
    }
  })

  it('drops legacy workflow continuations that have no explicit Conversation ownership', async () => {
    await writeSavedState()
    await writeWorkflowLease({ updated_at: '2000-01-01T00:00:00.000Z' })
    await writeFile(statePath(), JSON.stringify({
      current_dive_url: OLD_DIVE_URL,
      current_dive_session_id: OLD_DIVE_SESSION,
      pending_auto_resume: [{
        task_id: 'WF-STALL-1',
        reason: 'workflow_stalled',
        request_id: '2000-01-01T00:00:00.000Z'
      }]
    }), 'utf8')
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 1000,
      staleMs: 60_000,
      idleGraceMs: 10
    })
    try {
      expect(await host.resumePendingAutoResume()).toBe(0)
      expect(harness.views).toHaveLength(0)
    } finally {
      host.dispose()
    }
  })

  it('does not navigate for a watchdog probe while a Task submission is pending', async () => {
    await writeSavedState()
    await writeTaskOwner('T-INFLIGHT')
    await writeWorkflowLease({
      dive_session_id: 'DIVE-OTHER', conversation_url: 'https://chatgpt.com/c/other-workflow',
      updated_at: '2000-01-01T00:00:00.000Z'
    })
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 5, staleMs: 10, idleGraceMs: 10
    })
    let release!: (value: unknown) => void
    const submitted = new Promise<unknown>((resolve) => { release = resolve })
    try {
      await host.setSurface(true, BOUNDS)
      await host.setSurface(false)
      const contents = harness.views[0].webContents
      const original = contents.executeJavaScript.bind(contents)
      vi.spyOn(contents, 'executeJavaScript').mockImplementation((script) => (
        script.includes('__niraiHoloAutoResume') ? submitted : original(script)
      ))
      await host.enqueueAutoResume({ task_id: 'T-INFLIGHT', reason: 'done' })
      await waitFor(() => {
        expect(contents.executeJavaScript).toHaveBeenCalledWith(expect.stringContaining('__niraiHoloAutoResume'), true)
      })
      await host.resumePendingAutoResume()
      await new Promise((resolve) => setTimeout(resolve, 40))
      expect(contents.loadedUrls).toEqual([OLD_DIVE_URL])
    } finally {
      host.dispose()
      release({ status: 'not_ready' })
      await submitted
    }
  })

  it('keeps a workflow bound to its lease owner after Master moves within the same Dive', async () => {
    const reboundUrl = 'https://chatgpt.com/c/rebound-conversation'
    await writeSavedState()
    await writeWorkflowLease({
      dive_session_id: OLD_DIVE_SESSION,
      conversation_url: OLD_DIVE_URL,
      updated_at: '2000-01-01T00:00:00.000Z'
    })
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 5,
      staleMs: 10,
      idleGraceMs: 10
    })
    try {
      await host.setSurface(true, BOUNDS)
      const contents = harness.views[0].webContents
      await contents.loadURL(reboundUrl)
      await waitFor(async () => {
        expect((await readSavedState()).current_dive_url).toBe(reboundUrl)
      })
      await host.setSurface(false)

      expect(await host.resumePendingAutoResume()).toBe(0)
      await waitFor(() => {
        expect(contents.autoResumeScripts.some((script) => (
          script.includes('WF-STALL-1') && script.includes('workflow_stalled')
        ))).toBe(true)
      })
      await waitFor(async () => {
        const saved = await readSavedState()
        expect(saved.current_dive_url).toBe(reboundUrl)
        expect(saved.pending_auto_resume).toEqual([])
      })
      expect(contents.loadedUrls).toContain(OLD_DIVE_URL)
      expect(contents.loadedUrls.at(-1)).toBe(reboundUrl)
    } finally {
      host.dispose()
    }
  })

  it('routes a stale workflow resume to its owning Conversation without replacing the Current Dive', async () => {
    await writeSavedState()
    const backgroundUrl = 'https://chatgpt.com/c/background-workflow'
    await writeWorkflowLease({
      dive_session_id: 'DIVE-BACKGROUND',
      conversation_url: backgroundUrl,
      updated_at: '2000-01-01T00:00:00.000Z'
    })
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 5,
      staleMs: 10,
      idleGraceMs: 10
    })

    expect(await host.resumePendingAutoResume()).toBe(0)
    await waitFor(() => {
      expect(harness.views).toHaveLength(1)
      expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
    })
    await waitFor(async () => {
      const saved = await readSavedState()
      expect(saved.current_dive_url).toBe(OLD_DIVE_URL)
      expect(saved.current_dive_session_id).toBe(OLD_DIVE_SESSION)
      expect(saved.pending_auto_resume).toEqual([])
    })

    const loadedUrls = harness.views[0].webContents.loadedUrls
    expect(loadedUrls).toContain(backgroundUrl)
    expect(loadedUrls.at(-1)).toBe(OLD_DIVE_URL)
    expect(harness.views[0].webContents.autoResumeScripts[0]).toContain('DIVE-BACKGROUND')
    host.dispose()
  })

  it('does not auto-resume a stale workflow into a different Conversation while Holo is visible', async () => {
    await writeSavedState()
    await writeWorkflowLease({
      dive_session_id: 'DIVE-BACKGROUND',
      conversation_url: 'https://chatgpt.com/c/background-visible-guard',
      updated_at: '2000-01-01T00:00:00.000Z'
    })
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 5,
      staleMs: 10,
      idleGraceMs: 10
    })

    await host.setSurface(true, BOUNDS)
    expect(await host.resumePendingAutoResume()).toBe(0)
    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(0)
    expect(harness.views[0].webContents.getURL()).toBe(OLD_DIVE_URL)
    host.dispose()
  })

  it('does not auto-resume a stale workflow while ChatGPT is still generating', async () => {
    await writeSavedState()
    await writeWorkflowLease({ updated_at: '2000-01-01T00:00:00.000Z' })
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 5,
      staleMs: 10,
      idleGraceMs: 10
    })

    await host.setSurface(true, BOUNDS)
    harness.views[0].webContents.generationBusy = true
    expect(await host.resumePendingAutoResume()).toBe(0)
    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(0)
    host.dispose()
  })

  it('keeps auto-resume queued instead of overwriting a Master draft', async () => {
    await writeSavedState()
    await writeTaskOwner('T-DRAFT')
    const host = new HoloAddonHost(fakeWindow().window)
    await host.setSurface(true, BOUNDS)
    const contents = harness.views[0].webContents
    contents.autoResumeResult = { status: 'draft_present' }

    await host.enqueueAutoResume({
      task_id: 'T-DRAFT',
      agent_session_id: 'AS-DRAFT',
      reason: 'failed'
    })

    await waitFor(() => {
      expect(contents.autoResumeScripts.length).toBeGreaterThan(0)
    })
    await waitFor(async () => {
      const saved = await readSavedState()
      expect((saved.pending_auto_resume as unknown[]).length).toBe(1)
      expect(saved.processed_auto_resume_keys).toEqual([])
    })
    host.dispose()
  })

  it('drops an unowned legacy Task resume instead of guessing the Current Dive', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)

    const result = await host.enqueueAutoResume({
      task_id: 'T-UNOWNED',
      agent_session_id: 'AS-UNOWNED',
      reason: 'failed'
    })

    expect(result.accepted).toBe(false)
    expect(result.duplicate).toBe(false)
    expect(result.discarded).toBe(true)
    expect(harness.views).toHaveLength(0)
    const saved = await readSavedState()
    expect(saved.pending_auto_resume ?? []).toEqual([])
    host.dispose()
  })

  it('retains owner delivery across a temporary read failure', async () => {
    await writeSavedState()
    await writeTaskOwner('T-READ-RETRY')
    let readable = false
    const host = new HoloAddonHost(fakeWindow().window, createStateIo({
      readText: async (path) => {
        if (!readable && path === taskOwnerPath('T-READ-RETRY')) {
          throw Object.assign(new Error('busy'), { code: 'EBUSY' })
        }
        return readFile(path, 'utf8')
      }
    }))
    const trigger = { task_id: 'T-READ-RETRY', reason: 'failed' as const }
    try {
      await expect(host.enqueueAutoResume(trigger)).rejects.toThrow('busy')
      readable = true
      expect(await host.enqueueAutoResume(trigger)).toMatchObject({ accepted: true })
      await waitFor(async () => expect((await readSavedState()).pending_auto_resume).toEqual([]))
    } finally {
      host.dispose()
    }
  })

  it('routes a failed Task resume to its owner without replacing a different Current Dive', async () => {
    await writeSavedState()
    const backgroundUrl = 'https://chatgpt.com/c/background-task-owner'
    await writeTaskOwner('T-BACKGROUND', 'DIVE-BACKGROUND-TASK', backgroundUrl)
    const host = new HoloAddonHost(fakeWindow().window)

    const result = await host.enqueueAutoResume({
      task_id: 'T-BACKGROUND',
      agent_session_id: 'AS-BACKGROUND',
      reason: 'failed'
    })
    expect(result.accepted).toBe(true)

    await waitFor(() => {
      expect(harness.views).toHaveLength(1)
      expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
    })
    await waitFor(async () => {
      const saved = await readSavedState()
      expect(saved.current_dive_url).toBe(OLD_DIVE_URL)
      expect(saved.current_dive_session_id).toBe(OLD_DIVE_SESSION)
      expect(saved.pending_auto_resume).toEqual([])
    })
    expect(harness.views[0].webContents.loadedUrls).toContain(backgroundUrl)
    expect(harness.views[0].webContents.loadedUrls.at(-1)).toBe(OLD_DIVE_URL)
    expect(harness.views[0].webContents.autoResumeScripts[0]).toContain('DIVE-BACKGROUND-TASK')
    // A failed Task may still be recovered under the same Task ID, so its
    // routing owner must survive until a final done/cancelled event arrives.
    await expect(readFile(taskOwnerPath('T-BACKGROUND'), 'utf8')).resolves.toContain('DIVE-BACKGROUND-TASK')
    host.dispose()
  })

  it('prioritizes persisted pending auto-resume over a completed workflow watchdog on startup', async () => {
    await mkdir(join(harness.niraiRoot, 'runtime', 'holo'), { recursive: true })
    await writeWorkflowLease({
      state: 'completed',
      completed_at: '2026-09-12T10:26:04.209Z',
      updated_at: '2026-09-12T10:26:04.209Z'
    })
    await writeFile(statePath(), JSON.stringify({
      current_dive_url: OLD_DIVE_URL,
      current_dive_session_id: OLD_DIVE_SESSION,
      pending_auto_resume: [{
        task_id: 'T-STARTUP-PRIORITY',
        agent_session_id: 'AS-STARTUP-PRIORITY',
        reason: 'done',
        dive_session_id: OLD_DIVE_SESSION,
        conversation_url: OLD_DIVE_URL
      }],
      processed_auto_resume_keys: [],
      updated_at: '2026-09-12T10:36:33.374Z'
    }), 'utf8')
    const host = new HoloAddonHost(fakeWindow().window, undefined, {
      intervalMs: 1,
      staleMs: 1,
      idleGraceMs: 1
    })
    try {
      expect(await host.resumePendingAutoResume()).toBe(1)
      await waitFor(() => {
        expect(harness.views).toHaveLength(1)
        expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
      })
      await waitFor(async () => {
        const saved = await readSavedState()
        expect(saved.pending_auto_resume).toEqual([])
        expect(saved.processed_auto_resume_keys).toContain(
          'T-STARTUP-PRIORITY:AS-STARTUP-PRIORITY:done:-'
        )
      })
    } finally {
      host.dispose()
    }
  })

  it('restores a persisted pending auto-resume without reopening Holo Whisper manually', async () => {
    await mkdir(join(harness.niraiRoot, 'runtime', 'holo'), { recursive: true })
    await writeFile(statePath(), JSON.stringify({
      current_dive_url: OLD_DIVE_URL,
      current_dive_session_id: OLD_DIVE_SESSION,
      pending_auto_resume: [{
        task_id: 'T-MALFORMED', reason: 'done', request_id: 123,
        dive_session_id: OLD_DIVE_SESSION, conversation_url: OLD_DIVE_URL
      }, {
        task_id: 'T-RESTORE',
        agent_session_id: 'AS-RESTORE',
        reason: 'interrupted',
        dive_session_id: OLD_DIVE_SESSION,
        conversation_url: OLD_DIVE_URL
      }],
      processed_auto_resume_keys: [],
      updated_at: '2026-09-11T18:00:00+09:00'
    }), 'utf8')
    const host = new HoloAddonHost(fakeWindow().window)

    expect(await host.resumePendingAutoResume()).toBe(1)
    await waitFor(() => {
      expect(harness.views).toHaveLength(1)
      expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
    })
    await waitFor(async () => {
      const saved = await readSavedState()
      expect(saved.pending_auto_resume).toEqual([])
      expect(saved.processed_auto_resume_keys).toContain('T-RESTORE:AS-RESTORE:interrupted:-')
    })
    host.dispose()
  })

  it('keeps the same view and conversation across surface close and reopen', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    await host.setSurface(true, BOUNDS)

    const closed = await host.setSurface(false)
    expect(closed.visible).toBe(false)
    expect(closed.current_dive_url).toBe(OLD_DIVE_URL)

    const reopened = await host.setSurface(true, BOUNDS)
    expect(reopened.visible).toBe(true)
    expect(reopened.dive_state).toBe('current')
    expect(harness.views).toHaveLength(1)
    expect(harness.views[0].webContents.loadedUrls).toEqual([OLD_DIVE_URL])
  })

  it('keeps the previous Dive and persisted state when Dive preparation fails', async () => {
    await writeSavedState()
    let writeCount = 0
    const host = new HoloAddonHost(fakeWindow().window, createStateIo({
      writeText: async (path, content) => {
        writeCount += 1
        await writeFile(path, content, 'utf8')
      }
    }))
    await host.setSurface(true, BOUNDS)
    await waitFor(() => {
      expect(writeCount).toBeGreaterThan(0)
    })
    const writesBeforeDive = writeCount
    harness.views[0].webContents.bootstrapResult = false

    const result = await host.prepareDive()

    expect(result.bootstrap_prepared).toBe(false)
    expect(result.current_dive_session_id).toBe(OLD_DIVE_SESSION)
    expect(result.current_dive_url).toBe(OLD_DIVE_URL)
    expect(result.dive_state).toBe('current')
    expect(harness.views[0].webContents.loadedUrls.at(-1)).toBe(OLD_DIVE_URL)
    // Rollback performs exactly one durable save. Reloading the old
    // Conversation is part of rollback and must not enqueue another
    // fire-and-forget persistence request.
    expect(writeCount).toBe(writesBeforeDive + 1)
    // prepareDive() must not report the rollback complete before the durable
    // state and previous conversation have both been restored.
    const saved = await readSavedState()
    expect(saved.current_dive_session_id).toBe(OLD_DIVE_SESSION)
    expect(saved.current_dive_url).toBe(OLD_DIVE_URL)
    const runtimeFiles = await readdir(join(harness.niraiRoot, 'runtime', 'holo'))
    expect(runtimeFiles.filter((name) => name.endsWith('.tmp'))).toEqual([])
  }, 15000)

  it('persists the new Dive only after the bootstrap succeeded and ignores stale conversation navigations', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    await host.setSurface(true, BOUNDS)
    const contents = harness.views[0].webContents
    contents.bootstrapResult = true

    const result = await host.prepareDive()

    expect(result.bootstrap_prepared).toBe(true)
    expect(result.dive_state).toBe('preparing')
    expect(result.current_dive_session_id).not.toBe(OLD_DIVE_SESSION)
    const saved = await readSavedState()
    expect(saved.current_dive_session_id).toBe(result.current_dive_session_id)
    expect(saved.current_dive_url).toBeNull()

    // Navigating back to the previous conversation must not become the new Dive.
    contents.emit('did-navigate', {}, OLD_DIVE_URL)
    expect(host.getStatus().current_dive_url).toBeNull()

    const newConversationUrl = 'https://chatgpt.com/c/new-conversation'
    contents.emit('did-navigate', {}, newConversationUrl)
    expect(host.getStatus().current_dive_url).toBe(newConversationUrl)
    expect(host.getStatus().dive_state).toBe('current')
    await waitFor(async () => {
      const updated = await readSavedState()
      expect(updated.current_dive_url).toBe(newConversationUrl)
    })
  })

  it('keeps a Conversation persistence failure sticky until a later save succeeds', async () => {
    await writeSavedState()
    let failWrites = false
    const host = new HoloAddonHost(fakeWindow().window, createStateIo({
      writeText: async (path, content) => {
        if (failWrites) throw new Error('simulated write failure')
        await writeFile(path, content, 'utf8')
      }
    }))
    await host.setSurface(true, BOUNDS)
    const contents = harness.views[0].webContents

    failWrites = true
    contents.emit('did-navigate', {}, OLD_DIVE_URL)
    await waitFor(() => {
      expect(host.getStatus().persistence_issue).toBe('state_persistence_failed')
    })
    contents.emit('did-finish-load')
    expect(host.getStatus().web_state).toBe('ready')
    expect(host.getStatus().persistence_issue).toBe('state_persistence_failed')

    failWrites = false
    contents.emit('did-navigate', {}, OLD_DIVE_URL)
    await waitFor(async () => {
      expect(host.getStatus().persistence_issue).toBeNull()
      expect((await readSavedState()).current_dive_url).toBe(OLD_DIVE_URL)
    })
  })

  it('reports a rename failure after a successful new Dive without Web lifecycle erasing it', async () => {
    const renameError = Object.assign(new Error('simulated rename failure'), { code: 'EIO' })
    const host = new HoloAddonHost(fakeWindow().window, createStateIo({
      renameFile: async () => { throw renameError }
    }))
    await host.setSurface(true, BOUNDS)
    harness.views[0].webContents.bootstrapResult = true

    const result = await host.prepareDive()

    expect(result.bootstrap_prepared).toBe(true)
    expect(result.web_state).toBe('ready')
    expect(result.persistence_issue).toBe('state_persistence_failed')
    harness.views[0].webContents.emit('did-finish-load')
    expect(host.getStatus().persistence_issue).toBe('state_persistence_failed')
  })

  it('keeps rollback persistence failure visible after the previous Conversation reloads', async () => {
    await writeSavedState()
    let failWrites = false
    const host = new HoloAddonHost(fakeWindow().window, createStateIo({
      writeText: async (path, content) => {
        if (failWrites) throw new Error('simulated rollback write failure')
        await writeFile(path, content, 'utf8')
      }
    }))
    await host.setSurface(true, BOUNDS)
    harness.views[0].webContents.bootstrapResult = false
    failWrites = true

    const result = await host.prepareDive()

    expect(result.bootstrap_prepared).toBe(false)
    expect(result.current_dive_url).toBe(OLD_DIVE_URL)
    expect(result.web_state).toBe('ready')
    expect(result.persistence_issue).toBe('state_persistence_failed')
    expect(host.getStatus().persistence_issue).toBe('state_persistence_failed')
  }, 15000)

  it('allows one sanitized Clipboard write only after a recent Master left-click in the Holo WebContents', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    await host.setSurface(true, BOUNDS)
    const contents = harness.views[0].webContents
    const checkPermission = contents.session.permissionCheckHandler
    const requestPermission = contents.session.permissionRequestHandler
    expect(checkPermission).not.toBeNull()
    expect(requestPermission).not.toBeNull()

    const chatgptDetails = { requestingUrl: OLD_DIVE_URL }
    expect(checkPermission!(contents, 'clipboard-sanitized-write', 'https://chatgpt.com', chatgptDetails))
      .toBe(false)

    // A right-click must never authorize the remote page.
    contents.emit('before-mouse-event', {}, { type: 'mouseDown', button: 'right' })
    contents.emit('before-mouse-event', {}, { type: 'mouseUp', button: 'right' })
    expect(checkPermission!(contents, 'clipboard-sanitized-write', 'https://chatgpt.com', chatgptDetails))
      .toBe(false)

    // A completed primary click arms one short-lived grant. An unrelated
    // origin cannot use it, and the first valid ChatGPT permission check
    // consumes it.
    contents.emit('before-mouse-event', {}, { type: 'mouseUp', button: 'left' })
    expect(checkPermission!(contents, 'clipboard-sanitized-write', 'https://auth.openai.com', {
      requestingUrl: 'https://auth.openai.com/'
    })).toBe(false)
    expect(checkPermission!(contents, 'clipboard-sanitized-write', 'https://chatgpt.com', chatgptDetails))
      .toBe(true)
    expect(checkPermission!(contents, 'clipboard-sanitized-write', 'https://chatgpt.com', chatgptDetails))
      .toBe(false)

    // The request-handler path uses the same one-shot boundary.
    contents.emit('before-mouse-event', {}, { type: 'mouseUp', button: 'left' })
    let requestAllowed: boolean | null = null
    requestPermission!(contents, 'clipboard-sanitized-write', (allowed) => {
      requestAllowed = allowed
    }, chatgptDetails)
    expect(requestAllowed).toBe(true)
    requestAllowed = null
    requestPermission!(contents, 'clipboard-sanitized-write', (allowed) => {
      requestAllowed = allowed
    }, chatgptDetails)
    expect(requestAllowed).toBe(false)

    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    try {
      contents.emit('before-mouse-event', {}, { type: 'mouseUp', button: 'left' })
      now.mockReturnValue(1_751)
      expect(checkPermission!(contents, 'clipboard-sanitized-write', 'https://chatgpt.com', chatgptDetails))
        .toBe(false)
    } finally {
      now.mockRestore()
    }
  })

  it('pushes ChatGPT web focus changes to the renderer while the surface is attached', async () => {
    await writeSavedState()
    const { window, sentFocusEvents } = fakeWindow()
    const host = new HoloAddonHost(window)
    await host.setSurface(true, BOUNDS)
    const contents = harness.views[0].webContents

    contents.emit('focus')
    contents.emit('blur')
    expect(sentFocusEvents).toEqual([true, false])

    // A detached surface must never report an engaged conversation.
    await host.setSurface(false)
    contents.emit('focus')
    expect(sentFocusEvents.at(-1)).toBe(false)
  })

  it('reports a main-frame load failure as an observable unavailable state', async () => {
    await writeSavedState()
    const host = new HoloAddonHost(fakeWindow().window)
    await host.setSurface(true, BOUNDS)
    const contents = harness.views[0].webContents

    contents.emit('did-fail-load', {}, -3, 'aborted', OLD_DIVE_URL, true)
    expect(host.getStatus().web_state).toBe('ready')

    contents.emit('did-fail-load', {}, -105, 'ERR_NAME_NOT_RESOLVED', OLD_DIVE_URL, true)
    const status = host.getStatus()
    expect(status.web_state).toBe('unavailable')
    expect(status.phase).toBe('unavailable')
    expect(status.issue).toBe('web_load_failed')
    expect(status.current_dive_url).toBe(OLD_DIVE_URL)
  })
})
