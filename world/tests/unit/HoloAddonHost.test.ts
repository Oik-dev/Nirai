import { mkdir, mkdtemp, readFile, readdir, rename, rm, unlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
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
    views,
    FakeWebContentsView,
    niraiRoot: ''
  }
})

vi.mock('electron', () => ({
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

async function writeTaskOwner(
  taskId: string,
  diveSessionId = OLD_DIVE_SESSION,
  conversationUrl = OLD_DIVE_URL
): Promise<void> {
  const root = join(harness.niraiRoot, 'runtime', 'holo', 'task_owners')
  await mkdir(root, { recursive: true })
  await writeFile(join(root, `${taskId}.json`), JSON.stringify({
    version: 1,
    task_id: taskId,
    dive_session_id: diveSessionId,
    conversation_url: conversationUrl,
    created_at: '2026-09-11T12:00:00.000Z'
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
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      await assertion()
      return
    } catch (error) {
      lastError = error
      await new Promise((resolve) => setTimeout(resolve, 10))
    }
  }
  throw lastError
}

describe('HoloAddonHost lifecycle', () => {
  beforeEach(async () => {
    harness.views.length = 0
    harness.niraiRoot = await mkdtemp(join(tmpdir(), 'nirai-holo-host-'))
  })

  afterEach(async () => {
    vi.useRealTimers()
    const niraiRoot = harness.niraiRoot
    harness.niraiRoot = ''
    if (!niraiRoot) return
    await rm(niraiRoot, { recursive: true, force: true })
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
    })

    const duplicate = await host.enqueueAutoResume(trigger)
    expect(duplicate.accepted).toBe(false)
    expect(duplicate.duplicate).toBe(true)
    expect(harness.views[0].webContents.autoResumeScripts).toHaveLength(1)
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

  it.each(['busy', 'draft_present', 'not_ready', 'throws'])('restores the Current Dive after background delivery returns %s', async (status) => {
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
      await host.enqueueAutoResume({ task_id: 'T-RETRY', reason: 'done' })
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

  it('auto-resumes a stale active Holo workflow after generation has stopped', async () => {
    await writeSavedState()
    await writeWorkflowLease({ updated_at: '2000-01-01T00:00:00.000Z' })
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
      expect(saved.pending_auto_resume).toEqual([])
      expect((saved.processed_auto_resume_keys as string[]).some((key) => (
        key.includes('WF-STALL-1') && key.includes(':workflow_stalled:')
      ))).toBe(true)
    })
    host.dispose()
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
      reason: 'done'
    })

    expect(result.accepted).toBe(false)
    expect(result.duplicate).toBe(false)
    expect(harness.views).toHaveLength(0)
    const saved = await readSavedState()
    expect(saved.pending_auto_resume ?? []).toEqual([])
    host.dispose()
  })

  it('routes a Task resume to its owner without replacing a different Current Dive', async () => {
    await writeSavedState()
    const backgroundUrl = 'https://chatgpt.com/c/background-task-owner'
    await writeTaskOwner('T-BACKGROUND', 'DIVE-BACKGROUND-TASK', backgroundUrl)
    const host = new HoloAddonHost(fakeWindow().window)

    const result = await host.enqueueAutoResume({
      task_id: 'T-BACKGROUND',
      agent_session_id: 'AS-BACKGROUND',
      reason: 'done'
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
    host.dispose()
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
    contents.emit('did-navigate', {}, 'https://chatgpt.com/c/write-fails')
    await waitFor(() => {
      expect(host.getStatus().persistence_issue).toBe('state_persistence_failed')
    })
    contents.emit('did-finish-load')
    expect(host.getStatus().web_state).toBe('ready')
    expect(host.getStatus().persistence_issue).toBe('state_persistence_failed')

    failWrites = false
    contents.emit('did-navigate', {}, 'https://chatgpt.com/c/write-recovers')
    await waitFor(async () => {
      expect(host.getStatus().persistence_issue).toBeNull()
      expect((await readSavedState()).current_dive_url).toBe('https://chatgpt.com/c/write-recovers')
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
