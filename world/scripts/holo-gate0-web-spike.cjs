const { app, BrowserWindow, WebContentsView } = require('electron')
const { mkdirSync, writeFileSync } = require('node:fs')
const path = require('node:path')

const CHATGPT_URL = 'https://chatgpt.com/'
const PARTITION = 'persist:nirai-holo-chatgpt-gate0-spike'
const EVIDENCE_DIR = path.resolve(__dirname, '..', '..', 'Docs', 'evidence')
const JSON_PATH = path.join(EVIDENCE_DIR, 'holo-gate0-web-spike.json')
const PNG_PATH = path.join(EVIDENCE_DIR, 'holo-gate0-chatgpt.png')

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function run() {
  const startedAt = new Date().toISOString()
  let window = null
  let view = null
  const failures = []

  try {
    window = new BrowserWindow({
      width: 1100,
      height: 760,
      show: false,
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true
      }
    })

    view = new WebContentsView({
      webPreferences: {
        partition: PARTITION,
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true
      }
    })
    window.contentView.addChildView(view)
    view.setBounds({ x: 0, y: 0, width: 1100, height: 760 })
    view.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
      failures.push({ errorCode, errorDescription, validatedURL })
    })

    await view.webContents.loadURL(CHATGPT_URL)
    console.log(`[holo-gate0] loadURL finished: ${view.webContents.getURL()}`)
    await delay(5000)

    const currentUrl = view.webContents.getURL()
    const title = view.webContents.getTitle()
    const bodyText = await view.webContents.executeJavaScript(
      "document.body?.innerText?.slice(0, 1200) ?? ''",
      true
    ).catch((error) => {
      console.error('[holo-gate0] body inspection failed', error)
      return ''
    })
    console.log(`[holo-gate0] body text length: ${bodyText.length}`)
    const cookieCount = (await view.webContents.session.cookies.get({ url: CHATGPT_URL })).length
    console.log(`[holo-gate0] cookie count: ${cookieCount}`)
    let captureError = null

    mkdirSync(EVIDENCE_DIR, { recursive: true })
    try {
      const image = await view.webContents.capturePage()
      writeFileSync(PNG_PATH, image.toPNG())
      console.log(`[holo-gate0] screenshot captured -> ${PNG_PATH}`)
    } catch (error) {
      captureError = error instanceof Error ? error.message : String(error)
      console.error('[holo-gate0] screenshot capture failed', error)
    }
    writeFileSync(JSON_PATH, `${JSON.stringify({
      started_at: startedAt,
      finished_at: new Date().toISOString(),
      requested_url: CHATGPT_URL,
      final_url: currentUrl,
      title,
      loaded_chatgpt_origin: currentUrl.startsWith('https://chatgpt.com/'),
      body_text_present: bodyText.trim().length > 0,
      body_text_excerpt: bodyText.trim().slice(0, 500),
      persistent_partition: PARTITION,
      cookie_count: cookieCount,
      did_fail_load: failures,
      capture_error: captureError
    }, null, 2)}\n`, 'utf8')
    console.log(`[holo-gate0] evidence written -> ${JSON_PATH}`)
  } finally {
    if (view && !view.webContents.isDestroyed()) view.webContents.close()
    if (window && !window.isDestroyed()) window.destroy()
  }
}

app.whenReady().then(async () => {
  let exitCode = 0
  try {
    console.log('[holo-gate0] loading ChatGPT Web')
    await run()
  } catch (error) {
    exitCode = 1
    mkdirSync(EVIDENCE_DIR, { recursive: true })
    writeFileSync(JSON_PATH, `${JSON.stringify({
      started_at: new Date().toISOString(),
      error: error instanceof Error ? error.stack ?? error.message : String(error)
    }, null, 2)}\n`, 'utf8')
    console.error(error)
  } finally {
    process.exitCode = exitCode
    app.quit()
  }
})
