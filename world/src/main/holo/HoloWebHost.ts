import { BrowserWindow, WebContentsView, shell, type WebContents } from 'electron'
import { randomUUID } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { getNiraiRoot } from '../paths'
import {
  HOLO_CHATGPT_HOME_URL,
  HOLO_SESSION_PARTITION,
  buildHoloBootstrapTemplate,
  clampHoloSurfaceBounds,
  isHoloAllowedNavigationUrl,
  isHoloConversationUrl,
  isSafeHoloExternalUrl,
  shouldAllowHoloWebPermission,
  type HoloSurfaceBounds
} from './holoWeb'

interface PersistedHoloState {
  readonly current_dive_url: string | null
  readonly current_dive_session_id: string | null
  readonly updated_at: string
}

export interface HoloWebStatus {
  readonly visible: boolean
  readonly loaded: boolean
  readonly current_url: string | null
  readonly current_dive_url: string | null
  readonly current_dive_session_id: string | null
  readonly title: string | null
}

export interface HoloDiveResult extends HoloWebStatus {
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

export class HoloWebHost {
  private view: WebContentsView | null = null
  private attached = false
  private currentDiveUrl: string | null = null
  private currentDiveSessionId: string | null = null
  private stateLoaded = false

  constructor(private readonly window: BrowserWindow) {}

  async setSurface(visible: boolean, bounds?: HoloSurfaceBounds): Promise<HoloWebStatus> {
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
    await this.ensureInitialLoad(view)
    return this.getStatus()
  }

  async prepareDive(): Promise<HoloDiveResult> {
    const view = this.ensureView()
    this.attachView(view)
    this.currentDiveSessionId = randomUUID()
    this.currentDiveUrl = null
    await this.persistState()
    await view.webContents.loadURL(HOLO_CHATGPT_HOME_URL)

    const bootstrap = buildHoloBootstrapTemplate(localIsoDate())
    let prepared = false
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

    return {
      ...this.getStatus(),
      bootstrap_prepared: prepared
    }
  }

  async reload(): Promise<HoloWebStatus> {
    const view = this.ensureView()
    this.attachView(view)
    await this.ensureInitialLoad(view)
    view.webContents.reload()
    return this.getStatus()
  }

  getStatus(): HoloWebStatus {
    const currentUrl = this.view?.webContents.getURL() || null
    return {
      visible: this.attached,
      loaded: Boolean(currentUrl),
      current_url: currentUrl,
      current_dive_url: this.currentDiveUrl,
      current_dive_session_id: this.currentDiveSessionId,
      title: this.view?.webContents.getTitle() || null
    }
  }

  dispose(): void {
    const view = this.view
    this.view = null
    this.attached = false
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

    const holoSession = view.webContents.session
    holoSession.setPermissionRequestHandler((_webContents, permission, callback) => {
      callback(shouldAllowHoloWebPermission(permission))
    })
    holoSession.setPermissionCheckHandler((_webContents, permission) => (
      shouldAllowHoloWebPermission(permission)
    ))
    holoSession.setDisplayMediaRequestHandler((_request, callback) => {
      callback({})
    })
    holoSession.setDevicePermissionHandler(() => false)

    this.configureWebContentsSecurity(view.webContents)

    const rememberConversation = (_event: unknown, url: string): void => {
      if (!isHoloConversationUrl(url)) return
      this.currentDiveUrl = url
      void this.persistState()
    }
    view.webContents.on('did-navigate', rememberConversation)
    view.webContents.on('did-navigate-in-page', rememberConversation)

    this.view = view
    return view
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

    if (this.window.isDestroyed()) return
    try {
      this.window.contentView.removeChildView(view)
    } catch (error) {
      if (!this.window.isDestroyed()) throw error
    }
  }

  private async ensureInitialLoad(view: WebContentsView): Promise<void> {
    if (view.webContents.getURL()) return
    await this.loadState()
    await view.webContents.loadURL(this.currentDiveUrl ?? HOLO_CHATGPT_HOME_URL)
  }

  private getStatePath(): string {
    return join(getNiraiRoot(), 'runtime', 'holo', 'state.json')
  }

  private async loadState(): Promise<void> {
    if (this.stateLoaded) return
    this.stateLoaded = true
    try {
      const parsed = JSON.parse(await readFile(this.getStatePath(), 'utf8')) as Partial<PersistedHoloState>
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

  private async persistState(): Promise<void> {
    const path = this.getStatePath()
    const state: PersistedHoloState = {
      current_dive_url: this.currentDiveUrl,
      current_dive_session_id: this.currentDiveSessionId,
      updated_at: new Date().toISOString()
    }
    await mkdir(dirname(path), { recursive: true })
    await writeFile(path, `${JSON.stringify(state, null, 2)}\n`, 'utf8')
  }
}
