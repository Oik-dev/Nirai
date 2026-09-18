import { app, utilityProcess } from "electron/main";
import { appendFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const workerPath = join(here, "..", "..", "out", "src", "hub", "worker.js");
const smoke =
  process.env.NIRAI_V2_SMOKE === "1" ||
  process.argv.includes("--smoke") ||
  app.commandLine.hasSwitch("smoke");
const smokeRoot = smoke ? mkdtempSync(join(tmpdir(), "nirai-v2-smoke-")) : null;
const dataRoot =
  process.env.NIRAI_V2_DATA_ROOT ??
  smokeRoot ??
  join(process.env.LOCALAPPDATA ?? app.getPath("userData"), "Nirai-v2");

let hub = null;
let smokePhase = "before-fork";
const pending = new Map();
let smokeTimer = null;
const smokeLog = process.env.NIRAI_V2_SMOKE_LOG ?? null;

function markSmoke(phase) {
  smokePhase = phase;
  if (smoke && smokeLog) appendFileSync(smokeLog, `${new Date().toISOString()} main:${phase}\n`);
}

function request(type, extra = {}) {
  if (!hub) return Promise.reject(new Error("Hub is not running"));
  const id = randomUUID();
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    hub.postMessage({ id, type, ...extra });
  });
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
      }
      return;
    }

    const waiter = message?.id ? pending.get(message.id) : null;
    if (!waiter) return;
    pending.delete(message.id);
    if (message.ok) waiter.resolve(message.result);
    else waiter.reject(new Error(message.error ?? "Hub request failed"));
  });

  hub.on("exit", (code) => {
    if (smokeTimer) clearTimeout(smokeTimer);
    for (const { reject } of pending.values()) reject(new Error(`Hub exited with code ${code}`));
    pending.clear();
    hub = null;
    if (smokeRoot) rmSync(smokeRoot, { recursive: true, force: true });
    app.exit(code === 0 ? 0 : 1);
  });
}

markSmoke("main-start");
void app
  .whenReady()
  .then(startHub)
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
