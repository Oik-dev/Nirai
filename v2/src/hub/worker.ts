import { appendFileSync } from "node:fs";

import type { HubCommandEnvelope } from "../shared/types.js";
import { HubRuntime } from "./runtime.js";

interface ParentPortLike {
  on(event: "message", listener: (event: { data: unknown }) => void): this;
  postMessage(message: unknown): void;
}

interface UtilityProcess extends NodeJS.Process {
  parentPort?: ParentPortLike;
}

type HubRequest =
  | { id: string; type: "command"; envelope: HubCommandEnvelope }
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
const runtime = await HubRuntime.start(dataRoot);
markSmoke("runtime-ready");

parentPort.on("message", (event) => {
  const request = event.data as HubRequest;
  void handle(request);
});

parentPort.postMessage({ type: "ready", schema_version: 1 });
markSmoke("ready-sent");

async function handle(request: HubRequest): Promise<void> {
  try {
    if (request.type === "command") {
      const result = runtime.service.handleMasterCommand(request.envelope);
      parentPort!.postMessage({ id: request.id, ok: true, result });
      parentPort!.postMessage({ type: "changed" });
      return;
    }
    if (request.type === "snapshot") {
      parentPort!.postMessage({ id: request.id, ok: true, result: runtime.store.snapshot() });
      return;
    }
    if (request.type === "shutdown") {
      await runtime.close();
      parentPort!.postMessage({ id: request.id, ok: true, result: { closed: true } });
      process.exit(0);
    }
  } catch (error) {
    parentPort!.postMessage({
      id: request.id,
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.once(signal, () => {
    void runtime.close().finally(() => process.exit(0));
  });
}
