import { app, BrowserWindow } from 'electron'
import { writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { registerAvatarIpc } from './ipc/avatarIpc'

function createWindow(): void {
  const window = new BrowserWindow({
    width: 1280,
    height: 720,
    show: false,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: true,
      sandbox: true
    }
  })

  window.once('ready-to-show', () => {
    window.show()
  })

  if (!app.isPackaged) {
    window.webContents.once('did-finish-load', () => {
      setTimeout(() => {
        void window.capturePage().then((image) =>
          writeFile(
            join(__dirname, '../../../Docs/evidence/live-qa.png'),
            image.toPNG()
          )
        ).catch((error) => console.error('[qa-capture]', error))
      }, 5500)
    })
  }

  const rendererUrl = process.env.ELECTRON_RENDERER_URL

  if (rendererUrl) {
    void window.loadURL(rendererUrl)
  } else {
    void window.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(() => {
  registerAvatarIpc()
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
