import { createHash } from "node:crypto";
import { mkdirSync, realpathSync } from "node:fs";
import { createServer, type Server } from "node:net";
import { join, resolve } from "node:path";

import { HubService } from "./service.js";
import { HubStore } from "./store.js";
import { CapabilityRegistry } from "./capability.js";
import { TaskEngine } from "./engine.js";

function pipeNameFor(dataRoot: string): string {
  const key = createHash("sha256").update(resolve(dataRoot).toLowerCase()).digest("hex").slice(0, 20);
  return `\\\\.\\pipe\\nirai-v2-hub-${key}`;
}

function acquirePipe(pipeName: string): Promise<Server> {
  return new Promise((resolvePromise, reject) => {
    const server = createServer((socket) => socket.destroy());
    const onError = (error: NodeJS.ErrnoException) => {
      server.close();
      reject(
        error.code === "EADDRINUSE"
          ? new Error(`Nirai v2 Hub is already using this Data Root (${pipeName})`)
          : error,
      );
    };
    server.once("error", onError);
    server.listen(pipeName, () => {
      server.off("error", onError);
      server.on("error", () => {
        // The lock server is not a transport. Existing Hub state remains authoritative.
      });
      resolvePromise(server);
    });
  });
}

export class HubRuntime {
  readonly store: HubStore;
  readonly service: HubService;
  readonly registry: CapabilityRegistry;
  readonly engine: TaskEngine;
  private closing: Promise<void> | null = null;

  private constructor(
    readonly dataRoot: string,
    private readonly lockServer: Server,
    store: HubStore,
    registry: CapabilityRegistry,
  ) {
    this.store = store;
    this.registry = registry;
    this.engine = new TaskEngine(store, registry);
    this.service = new HubService(store, this.engine);
    this.engine.start();
  }

  static async start(dataRoot: string, registry = new CapabilityRegistry()): Promise<HubRuntime> {
    mkdirSync(dataRoot, { recursive: true });
    dataRoot = realpathSync.native(dataRoot);
    const pipeName = pipeNameFor(dataRoot);
    const lockServer = await acquirePipe(pipeName);

    let store: HubStore | undefined;
    try {
      store = await HubStore.open(
        join(dataRoot, "hub.sqlite3"),
        process.env.NIRAI_V2_APP_VERSION ?? "dev",
      );
      store.ensureResident("holo", "Holo");
      store.recoverAfterRestart();
      return new HubRuntime(dataRoot, lockServer, store, registry);
    } catch (error) {
      store?.close();
      await new Promise<void>((resolveClose) => lockServer.close(() => resolveClose()));
      throw error;
    }
  }

  close(): Promise<void> {
    if (this.closing) return this.closing;
    this.closing = this.closeOnce();
    return this.closing;
  }

  private async closeOnce(): Promise<void> {
    this.service.close();
    await this.engine.close();
    this.store.close();
    await new Promise<void>((resolveClose) => this.lockServer.close(() => resolveClose()));
  }
}
