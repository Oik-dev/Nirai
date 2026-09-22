import { app, BrowserWindow, Menu, Tray, dialog, ipcMain, utilityProcess, MessageChannelMain } from "electron/main";
import { appendFileSync, mkdirSync, mkdtempSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const workerPath = join(here, "..", "..", "out", "src", "hub", "worker.js");
const preloadPath = join(here, "preload.cjs");
const rendererPath = join(here, "..", "renderer", "index.html");
const trayIconPath = join(here, "..", "..", "resources", "nirai.ico");
const smoke =
  process.env.NIRAI_V2_SMOKE === "1" ||
  process.argv.includes("--smoke") ||
  app.commandLine.hasSwitch("smoke");
const uiSmoke = process.env.NIRAI_V2_UI_SMOKE === "1";
const testMode = smoke || uiSmoke;
const smokeRoot = testMode
  ? process.env.NIRAI_V2_SMOKE_DATA_ROOT ?? mkdtempSync(join(tmpdir(), "nirai-v2-smoke-")) : null;
const requestedRoot = resolve(smokeRoot ?? process.env.NIRAI_V2_DATA_ROOT ??
  join(process.env.LOCALAPPDATA ?? app.getPath("userData"), "Nirai-v2"));
mkdirSync(requestedRoot, { recursive: true });
const dataRoot = realpathSync.native(requestedRoot);
const userData = join(dataRoot, "electron");
mkdirSync(userData, { recursive: true });
app.setPath("userData", userData);

let hub = null;
let lifetimePort = null;
let mainWindow = null;
let tray = null;
let quitting = false;
let hubReady = false;
let smokeFailed = false;
let smokePhase = "before-fork";
const pending = new Map();
let smokeTimer = null;
let startupTimer = null;
let hubRestartCount = 0;
let uiSmokeStarted = false;
let dropNextCommandReply = false;
const HUB_RESTART_LIMIT = 1;
const smokeLog = process.env.NIRAI_V2_SMOKE_LOG ?? null;

function markSmoke(phase) {
  smokePhase = phase;
  if (testMode && smokeLog) appendFileSync(smokeLog, `${new Date().toISOString()} main:${phase}\n`);
}

function request(type, extra = {}) {
  if (!hub) return Promise.reject(new Error("transport: Hub is not running"));
  const id = randomUUID();
  return new Promise((resolveRequest, rejectRequest) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      rejectRequest(new Error("transport: Hub request timed out; receipt must be checked"));
    }, 15_000);
    pending.set(id, { resolve: resolveRequest, reject: rejectRequest, timer });
    try {
      hub.postMessage({ id, type, ...extra });
    } catch (error) {
      clearTimeout(timer);
      pending.delete(id);
      rejectRequest(new Error(`transport: ${error instanceof Error ? error.message : String(error)}`));
    }
  });
}

function isTrustedRenderer(event) {
  if (!mainWindow || event.sender !== mainWindow.webContents
    || event.senderFrame !== mainWindow.webContents.mainFrame) return false;
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
    if (!hubReady) throw new Error("transport: Hub is not ready");
    return request("snapshot");
  });

  ipcMain.handle("nirai:command", async (event, envelope) => {
    try {
      if (!isTrustedRenderer(event)) throw new Error("untrusted renderer");
      if (!hubReady) throw new Error("transport: Hub is not ready");
      const result = await request("command", { envelope });
      if (uiSmoke && dropNextCommandReply) {
        dropNextCommandReply = false;
        lifetimePort.close();
        throw new Error("transport: verification interrupted an accepted command reply");
      }
      return { ok: true, result };
    } catch (error) {
      return {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
        code: error?.code ?? (String(error).includes('transport:') ? 'unavailable' : 'invalid'),
        current: error?.current,
      };
    }
  });

  ipcMain.handle("nirai:command-receipt", async (event, commandId) => {
    if (!isTrustedRenderer(event)) throw new Error("untrusted renderer");
    if (!hubReady) throw new Error("transport: Hub is not ready");
    if (typeof commandId !== "string" || !commandId) throw new Error("invalid command id");
    return request("receipt", { command_id: commandId });
  });
}

function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) return mainWindow;

  mainWindow = new BrowserWindow({
    width: 1500,
    height: 930,
    minWidth: 360,
    minHeight: 600,
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
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url !== pathToFileURL(rendererPath).href) event.preventDefault();
  });
  mainWindow.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  mainWindow.webContents.session.setPermissionCheckHandler(() => false);
  mainWindow.loadFile(rendererPath);
  mainWindow.webContents.on("did-finish-load", () => {
    if (hubReady) void publishSnapshot();
  });
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
  hubReady = false;
  try {
    if (hub) await request("shutdown");
  } catch {
    hub?.kill();
  } finally {
    app.quit();
  }
}

function startHub() {
  markSmoke("app-ready");

  hub = utilityProcess.fork(workerPath, [], {
    env: {
      ...process.env,
      NIRAI_V2_DATA_ROOT: dataRoot,
      NIRAI_V2_APP_VERSION: app.getVersion(),
    },
    serviceName: "Nirai v2 Hub",
    stdio: smoke ? "pipe" : "inherit",
  });
  startupTimer = setTimeout(() => {
    if (hubReady) return;
    console.error("Nirai v2 Hub startup timed out");
    hub?.kill();
  }, 15_000);

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
    const channel = new MessageChannelMain();
    lifetimePort = channel.port1;
    lifetimePort.start();
    hub.postMessage({ type: "attach-lifetime" }, [channel.port2]);
    if (smoke) markSmoke("spawned");
  });

  hub.on("message", async (message) => {
    if (message?.type === "ready") {
      clearTimeout(startupTimer);
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
        void publishSnapshot();
        if (uiSmoke && !uiSmokeStarted) {
          uiSmokeStarted = true;
          void import("./ui-smoke.mjs").then(({ runUiSmoke }) => runUiSmoke(window, {
            request, userData, interruptNextReply: () => { dropNextCommandReply = true; },
            finish: async () => {
              markSmoke("ui-complete");
              quitting = true;
              await request("shutdown");
              app.quit();
            },
          })).catch((error) => {
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
    clearTimeout(waiter.timer);
    if (message.ok) waiter.resolve(message.result);
    else waiter.reject(Object.assign(new Error(message.error?.message ?? "Hub request failed"), message.error));
  });

  hub.on("exit", (code) => {
    const wasReady = hubReady;
    clearTimeout(startupTimer);
    hubReady = false;
    if (smokeTimer) clearTimeout(smokeTimer);
    for (const { reject, timer } of pending.values()) {
      clearTimeout(timer);
      reject(new Error(`transport: Hub exited with code ${code}`));
    }
    pending.clear();
    hub = null;
    lifetimePort?.close();
    lifetimePort = null;

    if (testMode && (smoke || quitting || !uiSmokeStarted)) {
      const completed = smokePhase === (smoke ? "shutdown" : "ui-complete");
      app.exit(!smokeFailed && completed && code === 0 ? 0 : 1);
      return;
    }

    if (!quitting) {
      if (!mainWindow || mainWindow.isDestroyed()) {
        dialog.showErrorBox("Nirai v2 Hub 起動失敗", `Hubが準備完了前に終了しました (code ${code})。`);
        app.exit(1);
        return;
      }

      mainWindow.webContents.send("nirai:hub-disconnected");
      if (wasReady && hubRestartCount < HUB_RESTART_LIMIT) {
        hubRestartCount += 1;
        setTimeout(() => {
          if (!quitting && !hub) startHub();
        }, 250);
      }
    }
  });
}

const ownsSingleInstance = testMode || app.requestSingleInstanceLock();

if (!ownsSingleInstance) {
  app.quit();
} else {
  if (!testMode) {
    app.on("second-instance", () => {
      const window = createWindow();
      window.show();
      window.focus();
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
      if (!testMode) dialog.showErrorBox("Nirai v2 起動失敗", error instanceof Error ? error.message : String(error));
      app.exit(1);
    });
}
