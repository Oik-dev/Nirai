import { createHash } from "node:crypto";
import { mkdirSync } from "node:fs";
import { createServer, type Server } from "node:net";
import { join, resolve } from "node:path";

import { HubService } from "./service.js";
import { HubStore } from "./store.js";

function pipeNameFor(dataRoot: string): string {
  const key = createHash("sha256").update(resolve(dataRoot).toLowerCase()).digest("hex").slice(0, 20);
  return `\\\\.\\pipe\\nirai-v2-hub-${key}`;
}

function acquirePipe(pipeName: string): Promise<Server> {
  return new Promise((resolvePromise, reject) => {
    const server = createServer();
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
  private closed = false;

  private constructor(
    readonly dataRoot: string,
    private readonly lockServer: Server,
    store: HubStore,
  ) {
    this.store = store;
    this.service = new HubService(store);
  }

  static async start(dataRoot: string): Promise<HubRuntime> {
    mkdirSync(dataRoot, { recursive: true });
    const pipeName = pipeNameFor(dataRoot);
    const lockServer = await acquirePipe(pipeName);

    try {
      const store = new HubStore(join(dataRoot, "hub.sqlite3"));
      store.ensureResident("holo", "Holo");
      store.recoverAfterRestart();
      return new HubRuntime(dataRoot, lockServer, store);
    } catch (error) {
      await new Promise<void>((resolveClose) => lockServer.close(() => resolveClose()));
      throw error;
    }
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    this.store.close();
    await new Promise<void>((resolveClose) => this.lockServer.close(() => resolveClose()));
  }
}
