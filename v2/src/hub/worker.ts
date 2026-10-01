import { appendFileSync } from "node:fs";

import type { HubCommandEnvelope } from "../shared/types.js";
import { HubRuntime } from "./runtime.js";
import { HUB_SCHEMA_VERSION } from "./store.js";
import { commandError } from "../shared/errors.js";
import type { HoloChatEnded, HoloEnded, HoloObservation } from "../shared/holo.js";
import type { AvatarRuntimeReport } from "../shared/appearance.js";

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
  | ({ id: string; type: "avatar-report" } & AvatarRuntimeReport)
  | { id: string; type: "avatar-reset"; resident_id?: string }
  | { id: string; type: "holo-observe"; observation: HoloObservation }
  | { id: string; type: "holo-delivered"; turn_id: string; url: string }
  | { id: string; type: "holo-sync"; turn_id: string; content: string; complete: boolean }
  | ({ id: string; type: "holo-ended" } & HoloEnded)
  | { id: string; type: "holo-native-send"; event_id: string; task_id: string; content: string; issued_at: string }
  | { id: string; type: "holo-chat-delivered"; message_id: string; url: string }
  | { id: string; type: "holo-chat-sync"; message_id: string; content: string; complete: boolean }
  | ({ id: string; type: "holo-chat-ended" } & HoloChatEnded)
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
const verification = process.env.NIRAI_V2_UI_SMOKE === "1"
  ? await import("../verification/capability.js")
  : null;
const registry = verification?.verificationRegistry();
const runtime = await HubRuntime.start(dataRoot, registry);
if (verification) {
  runtime.engine.holo = verification.verificationHoloDriver(runtime);
  runtime.engine.schedule();
}
markSmoke("runtime-ready");

let closing = false;
let lifetimePort: LifetimePort | undefined;

runtime.holo.send = message => parentPort.postMessage(message);
runtime.conversation.onChanged = () => {
  if (!closing) parentPort.postMessage({ type: "changed" });
};
runtime.engine.onChanged = () => {
  if (!closing) {
    runtime.holo.reconcile();
    parentPort.postMessage({ type: "changed" });
  }
};

parentPort.on("message", event => {
  if ((event.data as { type?: string })?.type === "attach-lifetime" && !lifetimePort) {
    lifetimePort = event.ports?.[0];
    lifetimePort?.on("close", () => {
      closing = true;
      void runtime.close().finally(() => process.exit(0));
    });
    lifetimePort?.start();
    return;
  }
  void handle(event.data as HubRequest);
});

parentPort.postMessage({ type: "ready", schema_version: HUB_SCHEMA_VERSION });
markSmoke("ready-sent");

async function handle(request: HubRequest): Promise<void> {
  try {
    if (closing) throw new Error("unavailable: Hub is shutting down");

    if (request.type === "avatar-report" || request.type === "avatar-reset") {
      let changed: boolean;
      if (request.type === "avatar-reset") changed = runtime.avatar.reset(request.resident_id);
      else {
        const { id: _id, type: _type, ...report } = request;
        changed = runtime.avatar.report(report);
      }
      parentPort!.postMessage({ id: request.id, ok: true, result: { changed } });
      if (changed) parentPort!.postMessage({ type: "changed" });
      return;
    }

    if (request.type.startsWith("holo-")) {
      if (request.type === "holo-native-send") {
        const result = runtime.service.handleNativeHoloMessage(request);
        parentPort!.postMessage({ id: request.id, ok: true, result });
        parentPort!.postMessage({ type: "changed" });
        return;
      }
      if (request.type === "holo-observe") runtime.holo.observe(request.observation);
      else if (request.type === "holo-chat-delivered") runtime.holo.chatDelivered(request.message_id, request.url);
      else if (request.type === "holo-chat-sync") runtime.holo.chatSync(request.message_id, request.content, request.complete);
      else if (request.type === "holo-chat-ended") runtime.holo.chatEnded(request);
      else if (request.type === "holo-delivered") runtime.holo.delivered(request.turn_id, request.url);
      else if (request.type === "holo-sync") runtime.holo.sync(request.turn_id, request.content, request.complete);
      else if (request.type === "holo-ended") runtime.holo.ended(request);
      else throw new Error("invalid: unsupported Holo report");
      parentPort!.postMessage({ id: request.id, ok: true, result: { observed: true } });
      parentPort!.postMessage({ type: "changed" });
      return;
    }

    if (request.type === "command") {
      const result = runtime.service.handleMasterCommand(request.envelope);
      parentPort!.postMessage({ id: request.id, ok: true, result });
      parentPort!.postMessage({ type: "changed" });
      return;
    }
    if (request.type === "receipt") {
      parentPort!.postMessage({
        id: request.id,
        ok: true,
        result: runtime.service.getMasterCommandReceipt(request.command_id),
      });
      return;
    }
    if (request.type === "snapshot") {
      parentPort!.postMessage({
        id: request.id,
        ok: true,
        result: {
          ...runtime.store.snapshot(),
          capabilities: runtime.registry.list(),
          conversation_providers: runtime.conversation.providers.list(),
          avatar_states: runtime.avatar.states(),
          verification_mode: Boolean(registry),
          holo: runtime.holo.availability(),
          holo_chat_binding: runtime.store.getHoloChatBinding(),
        },
      });
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
