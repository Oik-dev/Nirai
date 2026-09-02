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
