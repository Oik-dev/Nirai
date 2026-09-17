import { app, BrowserWindow } from 'electron'
import { join } from 'node:path'
import { HoloAddonHost } from './holo/HoloWebHost'
import { registerAvatarIpc } from './ipc/avatarIpc'
import { registerExternalIpc } from './ipc/externalIpc'
import { registerHoloIpc } from './ipc/holoIpc'
import { registerPersonaIpc } from './ipc/personaIpc'
import { registerVoicevoxIpc } from './ipc/voicevoxIpc'

const APP_ICON_PATH = join(__dirname, '../../resources/nirai.ico')

let holoAddonHost: HoloAddonHost | null = null

function createWindow(): void {
  const window = new BrowserWindow({
    width: 1280,
    height: 720,
    show: false,
    icon: APP_ICON_PATH,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: true,
      sandbox: true
    }
  })

  holoAddonHost = new HoloAddonHost(window)
  void holoAddonHost.resumePendingAutoResume().catch((error) => {
    console.warn('holo_auto_resume_restore_failed', error)
  })
  window.once('close', () => {
    // Dispose the child WebContentsView before BrowserWindow destroys its native contentView.
    // Running this from `closed` is too late and can raise "Object has been destroyed".
    holoAddonHost?.dispose()
    holoAddonHost = null
  })

  window.once('ready-to-show', () => {
    window.show()
  })

  const rendererUrl = process.env.ELECTRON_RENDERER_URL

  if (rendererUrl) {
    void window.loadURL(rendererUrl)
  } else {
    void window.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(() => {
  registerAvatarIpc()
  registerExternalIpc()
  registerHoloIpc(() => holoAddonHost)
  registerPersonaIpc()
  registerVoicevoxIpc()
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

app.on('window-all-closed', () => {
  app.quit()
})
