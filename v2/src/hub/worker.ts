import { appendFileSync } from "node:fs";

import type { HubCommandEnvelope } from "../shared/types.js";
import { HubRuntime } from "./runtime.js";
import { HUB_SCHEMA_VERSION } from "./store.js";
import { commandError } from "../shared/errors.js";

interface ParentPortLike {
  on(event: "message", listener: (event: { data: unknown; ports?: LifetimePort[] }) => void): this;
  postMessage(message: unknown): void;
}

interface LifetimePort {
  on(event: "close", listener: () => void): this;
  start(): void;
}

interface UtilityProcess extends NodeJS.Process {
  parentPort?: ParentPortLike;
}

type HubRequest =
  | { id: string; type: "command"; envelope: HubCommandEnvelope }
  | { id: string; type: "receipt"; command_id: string }
  | { id: string; type: "snapshot" }
  | { id: string; type: "shutdown" };

const smokeLog = process.env.NIRAI_V2_SMOKE_LOG;
const markSmoke = (phase: string) => {
  if (smokeLog) appendFileSync(smokeLog, `${new Date().toISOString()} hub:${phase}\n`);
};

markSmoke("worker-start");
const parentPort = (process as UtilityProcess).parentPort;
if (!parentPort) throw new Error("Hub worker must run as an Electron utilityProcess");
markSmoke("parent-port");

const dataRoot = process.env.NIRAI_V2_DATA_ROOT;
if (!dataRoot) throw new Error("NIRAI_V2_DATA_ROOT is required");

markSmoke("runtime-starting");
const registry = process.env.NIRAI_V2_UI_SMOKE === "1"
  ? (await import("../verification/capability.js")).verificationRegistry() : undefined;
const runtime = await HubRuntime.start(dataRoot, registry);
markSmoke("runtime-ready");
let closing = false;
let lifetimePort: LifetimePort | undefined;
runtime.engine.onChanged = () => { if (!closing) parentPort.postMessage({ type: "changed" }); };

parentPort.on("message", (event) => {
  if ((event.data as { type?: string })?.type === "attach-lifetime" && !lifetimePort) {
    lifetimePort = event.ports?.[0];
    lifetimePort?.on("close", () => {
      closing = true;
      void runtime.close().finally(() => process.exit(0));
    });
    lifetimePort?.start();
    return;
  }
  const request = event.data as HubRequest;
  void handle(request);
});

parentPort.postMessage({ type: "ready", schema_version: HUB_SCHEMA_VERSION });
markSmoke("ready-sent");

async function handle(request: HubRequest): Promise<void> {
  try {
    if (closing) throw new Error("unavailable: Hub is shutting down");
    if (request.type === "command") {
      const result = runtime.service.handleMasterCommand(request.envelope);
      parentPort!.postMessage({ id: request.id, ok: true, result });
      parentPort!.postMessage({ type: "changed" });
      return;
    }
    if (request.type === "receipt") {
      const result = runtime.service.getMasterCommandReceipt(request.command_id);
      parentPort!.postMessage({ id: request.id, ok: true, result });
      return;
    }
    if (request.type === "snapshot") {
      parentPort!.postMessage({ id: request.id, ok: true, result: {
        ...runtime.store.snapshot(), capabilities: runtime.registry.list(), verification_mode: Boolean(registry),
      } });
      return;
    }
    if (request.type === "shutdown") {
      closing = true;
      await runtime.close();
      parentPort!.postMessage({ id: request.id, ok: true, result: { closed: true } });
      process.exit(0);
    }
    throw new Error("invalid: unsupported Hub request");
  } catch (error) {
    parentPort!.postMessage({
      id: request.id,
      ok: false,
      error: commandError(error),
    });
  }
}

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.once(signal, () => {
    closing = true;
    void runtime.close().finally(() => process.exit(0));
  });
}
