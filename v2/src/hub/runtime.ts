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
import { AvatarCapability } from "./avatar.js";
import { ConversationProviders, ConversationRuntime } from "./conversation.js";
import { CodexConversationProvider } from "./codex.js";
import { CodexTaskDriver } from "./codex-task.js";
import { HubError } from "../shared/errors.js";

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
  readonly conversation: ConversationRuntime;
  control!: ControlServer;
  holo!: HoloConnector;
  avatar!: AvatarCapability;
  private closing: Promise<void> | null = null;

  private constructor(
    readonly dataRoot: string,
    private readonly lockServer: Server,
    store: HubStore,
    registry: CapabilityRegistry,
    conversationProviders: ConversationProviders,
  ) {
    this.store = store;
    this.registry = registry;
    this.engine = new TaskEngine(store, registry);
    this.service = new HubService(store, this.engine);
    this.conversation = new ConversationRuntime(store, conversationProviders);
    this.service.onChatMessage = () => this.conversation.schedule();
  }

  static async start(dataRoot: string, registry = new CapabilityRegistry(), conversationProviders = new ConversationProviders()): Promise<HubRuntime> {
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
      store.recoverChatResponses();
      await privateDirectory(join(dataRoot, "runs"));
      const product = fileURLToPath(new URL("../../../", import.meta.url));
      const policy = new LocalFilePolicy([dataRoot], [join(product, "out"), join(product, "src", "main"), join(product, "src", "renderer"),
        join(product, "src", "hub", "windows-host.ps1"), join(product, "src", "hub", "windows-host.cs"), join(product, "resources", "local-profiles.json"), dirname(process.execPath)]);
      registry.register(localFiles(policy, dataRoot, initialCommandProfiles(product, process.execPath)));
      await recoverLocalRuns(dataRoot, policy, store);
      const runtime = new HubRuntime(dataRoot, lockServer, store, registry, conversationProviders);
      runtime.avatar = new AvatarCapability(store, () => registry.onChanged());
      registry.register(runtime.avatar);
      runtime.control = await ControlServer.attach(lockServer, pipeName, dataRoot, store, runtime.service);
      runtime.holo = new HoloConnector(store);
      conversationProviders.register({ id: "holo", availability: () => runtime.holo.chatAvailability(),
        generate: (input, signal) => runtime.holo.generateChat(input, signal) });
      const codex = new CodexConversationProvider(dataRoot);
      conversationProviders.register(codex);
      runtime.holo.onChanged = () => runtime.engine.schedule();
      runtime.engine.registerTaskDriver("holo", resident => resident.id === "holo", {
        supports_resume: true,
        availability: id => runtime.holo.availability(id), start: turn => runtime.holo.start(turn),
        reconcile: () => runtime.holo.reconcile(), close: () => runtime.holo.close(),
      });
      const codexTask = new CodexTaskDriver(store, runtime.service, codex);
      codexTask.onChanged = () => { runtime.engine.schedule(); runtime.engine.onChanged(); };
      runtime.engine.registerTaskDriver("codex", resident => resident.id !== "holo" && resident.capability_id === codex.id, codexTask);
      runtime.engine.start();
      runtime.conversation.schedule();
      // Refresh saved connections without restarting interrupted conversation requests.
      for (const id of new Set(store.listResidents().map(resident => resident.capability_id).filter(Boolean))) {
        if (!id || !conversationProviders.list().some(provider => provider.id === id)) continue;
        void conversationProviders.refresh(id).then(() => {
          runtime.engine.schedule();
          runtime.conversation.onChanged();
        }).catch(() => {});
      }
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

  async refreshConversationProvider(id: string): Promise<void> {
    if (this.conversation.usesProvider(id) || this.engine.isTaskDriverBusy(id)) {
      throw new HubError("unavailable", "AIが返答を準備・生成しています。返答後に接続を確認してください。");
    }
    await this.conversation.providers.refresh(id);
    this.engine.schedule();
  }

  private async closeOnce(): Promise<void> {
    this.service.close();
    await this.conversation.close();
    await this.control.close();
    await this.engine.close();
    await this.conversation.providers.close();
    this.holo.close();
    this.store.close();
    await new Promise<void>((resolveClose) => this.lockServer.close(() => resolveClose()));
  }
}
