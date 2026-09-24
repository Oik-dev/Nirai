import { createHash } from "node:crypto";
import { mkdirSync, realpathSync } from "node:fs";
import { createServer, type Server } from "node:net";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { HubService } from "./service.js";
import { HubStore } from "./store.js";
import { CapabilityRegistry } from "./capability.js";
import { TaskEngine } from "./engine.js";
import { localFiles, LocalFilePolicy } from "./local-files.js";
import { ControlServer, privateDirectory } from "./control.js";
import { initialCommandProfiles } from "./local-process.js";
import { recoverLocalRuns } from "./local-recovery.js";
import { HoloConnector } from "./holo.js";

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
  control!: ControlServer;
  holo!: HoloConnector;
  private closing: Promise<void> | null = null;

  private constructor(
    readonly dataRoot: string,
    private readonly lockServer: Server,
    store: HubStore,
    registry: CapabilityRegistry,
    worldRulesPath?: string,
  ) {
    this.store = store;
    this.registry = registry;
    this.engine = new TaskEngine(store, registry);
    this.service = new HubService(store, this.engine, worldRulesPath);
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
      await privateDirectory(join(dataRoot, "runs"));
      const product = fileURLToPath(new URL("../../../", import.meta.url));
      const policy = new LocalFilePolicy([dataRoot], [join(product, "out"), join(product, "src", "main"), join(product, "src", "renderer"),
        join(product, "src", "hub", "windows-host.ps1"), join(product, "src", "hub", "windows-host.cs"), join(product, "resources", "local-profiles.json"), dirname(process.execPath)]);
      registry.register(localFiles(policy, dataRoot, initialCommandProfiles(product, process.execPath)));
      await recoverLocalRuns(dataRoot, policy, store);
      const worldRulesPath = join(product, "..", "WORLD_RULES.md");
      const runtime = new HubRuntime(dataRoot, lockServer, store, registry, worldRulesPath);
      runtime.control = await ControlServer.attach(lockServer, pipeName, dataRoot, store, runtime.service);
      runtime.holo = new HoloConnector(store);
      runtime.service.onTurnContextLoaded = turnId => runtime.holo.contextLoaded(turnId);
      runtime.holo.onChanged = () => runtime.engine.schedule();
      runtime.engine.holo = runtime.holo;
      runtime.engine.start();
      return runtime;
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
    await this.control.close();
    await this.engine.close();
    this.holo.close();
    this.store.close();
    await new Promise<void>((resolveClose) => this.lockServer.close(() => resolveClose()));
  }
}
