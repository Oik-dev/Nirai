import { app, BrowserWindow, Menu, Tray, ipcMain, utilityProcess } from "electron/main";
import { appendFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..", "..");
const workerPath = join(here, "..", "..", "out", "src", "hub", "worker.js");
const preloadPath = join(here, "preload.cjs");
const rendererPath = join(repoRoot, "v2", "src", "renderer", "index.html");
const trayIconPath = join(repoRoot, "world", "resources", "nirai.ico");
const smoke =
  process.env.NIRAI_V2_SMOKE === "1" ||
  process.argv.includes("--smoke") ||
  app.commandLine.hasSwitch("smoke");
const uiSmoke = process.env.NIRAI_V2_UI_SMOKE === "1";
const testMode = smoke || uiSmoke;
const smokeRoot = testMode ? mkdtempSync(join(tmpdir(), "nirai-v2-smoke-")) : null;
const dataRoot =
  process.env.NIRAI_V2_DATA_ROOT ??
  smokeRoot ??
  join(process.env.LOCALAPPDATA ?? app.getPath("userData"), "Nirai-v2");

let hub = null;
let mainWindow = null;
let tray = null;
let quitting = false;
let hubReady = false;
let smokeFailed = false;
let smokePhase = "before-fork";
const pending = new Map();
let smokeTimer = null;
const smokeLog = process.env.NIRAI_V2_SMOKE_LOG ?? null;

function markSmoke(phase) {
  smokePhase = phase;
  if (testMode && smokeLog) appendFileSync(smokeLog, `${new Date().toISOString()} main:${phase}\n`);
}

function request(type, extra = {}) {
  if (!hub) return Promise.reject(new Error("Hub is not running"));
  const id = randomUUID();
  return new Promise((resolveRequest, rejectRequest) => {
    pending.set(id, { resolve: resolveRequest, reject: rejectRequest });
    hub.postMessage({ id, type, ...extra });
  });
}

function isTrustedRenderer(event) {
  const url = event.senderFrame?.url ?? "";
  if (!url.startsWith("file:")) return false;
  try {
    const rendererUrl = pathToFileURL(rendererPath).href;
    return url === rendererUrl;
  } catch {
    return false;
  }
}

async function publishSnapshot() {
  if (!hubReady || !mainWindow || mainWindow.isDestroyed()) return;
  try {
    const snapshot = await request("snapshot");
    mainWindow.webContents.send("nirai:snapshot-changed", snapshot);
  } catch {
    // Renderer will show the last confirmed state until the Hub reconnects.
  }
}

function installIpc() {
  ipcMain.handle("nirai:snapshot", async (event) => {
    if (!isTrustedRenderer(event)) throw new Error("untrusted renderer");
    if (!hubReady) throw new Error("Hub is not ready");
    return request("snapshot");
  });

  ipcMain.handle("nirai:command", async (event, envelope) => {
    if (!isTrustedRenderer(event)) throw new Error("untrusted renderer");
    if (!hubReady) throw new Error("Hub is not ready");
    return request("command", { envelope });
  });
}

function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) return mainWindow;

  mainWindow = new BrowserWindow({
    width: 1500,
    height: 930,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    backgroundColor: "#07131d",
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.removeMenu();
  mainWindow.loadFile(rendererPath);
  mainWindow.once("ready-to-show", () => {
    if (!uiSmoke) mainWindow?.show();
  });
  mainWindow.on("close", (event) => {
    if (quitting) return;
    event.preventDefault();
    mainWindow?.hide();
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  return mainWindow;
}

function createTray() {
  if (tray || testMode) return;
  tray = new Tray(trayIconPath);
  tray.setToolTip("Nirai v2");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: "Niraiを開く",
        click: () => {
          const window = createWindow();
          window.show();
          window.focus();
        },
      },
      {
        label: "終了",
        click: () => {
          void quitNirai();
        },
      },
    ]),
  );
  tray.on("double-click", () => {
    const window = createWindow();
    window.show();
    window.focus();
  });
}

async function quitNirai() {
  if (quitting) return;
  quitting = true;
  try {
    if (hubReady) await request("shutdown");
  } catch {
    hub?.kill();
  } finally {
    app.quit();
  }
}

async function waitFor(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await predicate();
    if (value) return value;
    await new Promise((resolveWait) => setTimeout(resolveWait, 40));
  }
  throw new Error("UI smoke wait timed out");
}

async function runUiSmoke(window) {
  await new Promise((resolveLoad, rejectLoad) => {
    if (!window.webContents.isLoading()) {
      resolveLoad();
      return;
    }
    window.webContents.once("did-finish-load", resolveLoad);
    window.webContents.once("did-fail-load", (_event, _code, description) => rejectLoad(new Error(description)));
  });

  const bridgeReady = await window.webContents.executeJavaScript("Boolean(window.niraiDashboard)");
  if (!bridgeReady) throw new Error("Dashboard bridge is unavailable");

  await window.webContents.executeJavaScript("document.getElementById('addTaskButton').click()");
  await waitFor(async () => (await request("snapshot")).tasks.length === 1);
  await waitFor(async () =>
    window.webContents.executeJavaScript("document.querySelectorAll('[data-task-id]').length === 1")
  );

  await window.webContents.executeJavaScript(`
    (() => {
      const input = document.getElementById('chatInput');
      input.value = 'M2 UI smoke';
      document.getElementById('chatForm').requestSubmit();
    })()
  `);
  await waitFor(async () => {
    const current = await request("snapshot");
    return current.tasks[0]?.state === "Running" && current.messages.length === 1;
  });
  await waitFor(async () =>
    window.webContents.executeJavaScript("document.getElementById('chatMessages').textContent.includes('M2 UI smoke')")
  );

  await window.webContents.executeJavaScript("document.getElementById('pauseButton').click()");
  await waitFor(async () => (await request("snapshot")).tasks[0]?.state === "Paused");
  await waitFor(async () =>
    window.webContents.executeJavaScript("document.getElementById('pauseButton').textContent === '再開'")
  );

  await window.webContents.executeJavaScript("document.getElementById('pauseButton').click()");
  await waitFor(async () => (await request("snapshot")).tasks[0]?.state === "Running");
  await waitFor(async () =>
    window.webContents.executeJavaScript("document.getElementById('pauseButton').textContent === 'Pause'")
  );

  await window.webContents.executeJavaScript("document.getElementById('resumeButton').click()");
  await waitFor(async () => (await request("snapshot")).tasks[0]?.resume_enabled === true);
  await waitFor(async () =>
    window.webContents.executeJavaScript("document.getElementById('dashboard').getAttribute('aria-busy') !== 'true'")
  );

  await window.webContents.executeJavaScript("document.querySelector('[data-task-action=complete]').click()");
  await waitFor(async () => (await request("snapshot")).tasks[0]?.state === "Completed");
  await waitFor(async () =>
    window.webContents.executeJavaScript("document.getElementById('taskAccordion').textContent.includes('Completed')")
  );

  markSmoke("ui-complete");
  quitting = true;
  await request("shutdown");
  app.quit();
}

function startHub() {
  markSmoke("app-ready");

  hub = utilityProcess.fork(workerPath, [], {
    env: { ...process.env, NIRAI_V2_DATA_ROOT: dataRoot },
    serviceName: "Nirai v2 Hub",
    stdio: "pipe",
  });

  if (smoke) {
    markSmoke("forked");
    hub.stdout?.on("data", (chunk) => process.stdout.write(`[hub] ${chunk}`));
    hub.stderr?.on("data", (chunk) => process.stderr.write(`[hub] ${chunk}`));
    smokeTimer = setTimeout(() => {
      console.error(`Nirai v2 smoke timeout at phase: ${smokePhase}`);
      hub?.kill();
      app.exit(2);
    }, 8_000);
  }

  hub.on("spawn", () => {
    if (smoke) markSmoke("spawned");
  });

  hub.on("message", async (message) => {
    if (message?.type === "ready") {
      hubReady = true;
      if (smoke) {
        markSmoke("ready");
        try {
          const created = await request("command", {
            envelope: {
              protocol_version: 1,
              command_id: "smoke-create",
              issued_at: new Date().toISOString(),
              type: "CreateTask",
              target: null,
              payload: { resident_id: "holo" },
            },
          });
          markSmoke("created");
          const snapshot = await request("snapshot");
          markSmoke("snapshotted");
          if (!created?.task_id || snapshot.tasks?.length !== 1) {
            throw new Error("Hub smoke assertion failed");
          }
          await request("shutdown");
          markSmoke("shutdown");
        } catch (error) {
          console.error(error);
          app.exit(1);
        }
      } else {
        createTray();
        const window = createWindow();
        if (uiSmoke) {
          void runUiSmoke(window).catch((error) => {
            console.error(error);
            smokeFailed = true;
            quitting = true;
            hub?.kill();
            app.exit(1);
          });
        }
      }
      return;
    }

    if (message?.type === "changed") {
      void publishSnapshot();
      return;
    }

    const waiter = message?.id ? pending.get(message.id) : null;
    if (!waiter) return;
    pending.delete(message.id);
    if (message.ok) waiter.resolve(message.result);
    else waiter.reject(new Error(message.error ?? "Hub request failed"));
  });

  hub.on("exit", (code) => {
    hubReady = false;
    if (smokeTimer) clearTimeout(smokeTimer);
    for (const { reject } of pending.values()) reject(new Error(`Hub exited with code ${code}`));
    pending.clear();
    hub = null;

    if (smokeRoot) rmSync(smokeRoot, { recursive: true, force: true });
    if (testMode) {
      app.exit(smokeFailed ? 1 : code === 0 ? 0 : 1);
      return;
    }

    if (!quitting) {
      mainWindow?.webContents.send("nirai:hub-disconnected");
    }
  });
}

installIpc();
app.on("activate", () => {
  if (testMode) return;
  const window = createWindow();
  window.show();
});

app.on("before-quit", (event) => {
  if (testMode || quitting || !hubReady) return;
  event.preventDefault();
  void quitNirai();
});

markSmoke("main-start");
void app
  .whenReady()
  .then(startHub)
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
