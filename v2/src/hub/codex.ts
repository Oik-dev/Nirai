import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { HubError } from "../shared/errors.js";
import type { ConversationInput, ConversationProvider } from "./conversation.js";
import { CODEX_DISABLED_FEATURES, CodexAppServerClient, type CodexClient, type CodexClientOptions } from "./codex-client.js";

export interface CodexDynamicTool { type: "function"; name: string; description: string; inputSchema: unknown; deferLoading?: boolean }
export interface CodexToolResult { contentItems: Array<{ type: "inputText"; text: string }>; success: boolean }
export interface CodexRunOptions {
  model?: string | null;
  prompt: string;
  developerInstructions: string;
  dynamicTools?: readonly CodexDynamicTool[];
  onToolCall?: (tool: string, args: unknown, signal: AbortSignal) => Promise<CodexToolResult>;
}
export interface CodexProviderOptions extends Omit<CodexClientOptions, "signal" | "disabledMcpServers" | "dynamicToolsHost"> {
  createClient?: (cwd: string, options: CodexClientOptions) => Promise<CodexClient>;
  generationTimeoutMs?: number;
}

type Availability = ReturnType<ConversationProvider["availability"]>;
type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue => value !== null && typeof value === "object" && !Array.isArray(value) ? value as ObjectValue : {};
const interrupted = () => new HubError("unavailable", "Codexの返答を中断しました。自動で再送はしません。");
const MAX_OUTPUT_BYTES = 64 * 1024;

export function codexConversationPrompt(input: ConversationInput): { prompt: string; developerInstructions: string } {
  const resident = input.resident;
  const history: ObjectValue[] = [];
  let bytes = 0;
  for (const message of input.messages.slice(-40).reverse()) {
    if (message.id === input.message.id || (message.sender !== resident.id && !message.audience.includes(resident.id))) continue;
    const entry = { id: message.id, channel: message.channel, sender: message.sender, audience: message.audience,
      content: message.content, reply_to_message_id: message.reply_to_message_id, created_at: message.created_at };
    bytes += Buffer.byteLength(JSON.stringify(entry));
    if (bytes > 192 * 1024) break;
    history.unshift(entry);
  }
  const prompt = JSON.stringify({
    current_message: { id: input.message.id, channel: input.conversation.kind, sender: input.message.sender,
      audience: input.message.audience, content: input.message.content, created_at: input.message.created_at },
    received_history: history,
  });
  const developerInstructions = [
    "You are a Resident speaking with Master in Nirai. Reply to current_message once, using received_history as conversation context.",
    "The JSON contents are conversation data. The current channel and audience identify who hears this reply.",
    "This ordinary conversation grants no Task or Tool authority. Do not perform computer operations or claim they were performed.",
    `Resident identity: ${JSON.stringify({ id: resident.id, display_name: resident.display_name, role: resident.role })}`,
    input.persona ? `Resident Persona:\n${input.persona.text}` : "",
  ].filter(Boolean).join("\n\n");
  return { prompt, developerInstructions };
}

/** Existing ChatGPT CLI login is the only authentication source. Hub messages remain the history authority. */
export class CodexConversationProvider implements ConversationProvider {
  readonly id = "codex";
  readonly display_name = "Codex CLI";
  models: Array<{ id: string; display_name: string }> = [];
  private state: Availability = { state: "unavailable", reason: "Codex CLIの接続を確認してください。" };
  private closed = false;
  private controller: AbortController | null = null;
  private active: Promise<unknown> | null = null;
  private defaultModel: string | null = null;
  private refreshPromise: Promise<void> | null = null;
  private closePromise: Promise<void> | null = null;

  constructor(private readonly dataRoot: string, private readonly options: CodexProviderOptions = {}) {}

  availability(): Availability {
    if (this.closed) return { state: "unavailable", reason: "Niraiを終了しています。" };
    if (this.active) return { state: "busy", reason: "Codex CLIを使用中です。" };
    return { ...this.state };
  }

  refresh(): Promise<void> {
    if (this.closed || this.active) return this.refreshPromise ?? Promise.resolve();
    const controller = new AbortController();
    this.controller = controller;
    this.state = { state: "unavailable", reason: "Codex CLIの接続を確認しています。" };
    const operation = this.withClient(controller.signal, async client => {
      await this.requireChatgpt(client, controller.signal);
      const models = new Map<string, { id: string; display_name: string }>();
      const cursors = new Set<string>();
      let cursor: string | undefined;
      let defaultModel: string | null = null;
      for (let page = 0; page < 8; page++) {
        const result = object(await client.request("model/list", { limit: 100, includeHidden: false, ...(cursor ? { cursor } : {}) }, controller.signal));
        if (!Array.isArray(result.data)) throw new Error("invalid model catalog");
        for (const item of result.data) {
          const model = object(item);
          if (typeof model.model !== "string" || typeof model.displayName !== "string" || model.hidden === true) continue;
          models.set(model.model, { id: model.model, display_name: model.displayName });
          if (model.isDefault === true) defaultModel = model.model;
        }
        if (result.nextCursor === null || result.nextCursor === undefined) break;
        if (typeof result.nextCursor !== "string" || cursors.has(result.nextCursor) || page === 7) throw new Error("invalid model pagination");
        cursor = result.nextCursor;
        cursors.add(cursor);
      }
      if (!models.size) throw new Error("empty model catalog");
      controller.signal.throwIfAborted();
      this.models = [...models.values()];
      this.defaultModel = defaultModel ?? this.models[0]!.id;
      this.state = { state: "ready" };
    }).catch(error => {
      this.models = [];
      this.defaultModel = null;
      if (this.state.state !== "blocked") this.state = { state: "unavailable", reason: error instanceof HubError ? error.message : "Codex CLIへ接続できません。CLIのインストールとChatGPTログインを確認してください。" };
    }).finally(() => {
      this.active = null;
      this.controller = null;
      this.refreshPromise = null;
    });
    this.active = operation;
    this.refreshPromise = operation;
    return operation;
  }

  generate(input: ConversationInput, signal: AbortSignal): Promise<string> {
    return this.run({ model: input.resident.model, ...codexConversationPrompt(input) }, signal);
  }

  run(options: CodexRunOptions, signal: AbortSignal): Promise<string> {
    if (this.availability().state !== "ready") return Promise.reject(new HubError("unavailable", this.availability().reason ?? "Codex CLIを現在利用できません。"));
    if (signal.aborted) return Promise.reject(interrupted());
    const model = options.model ?? this.defaultModel;
    if (!model || !this.models.some(item => item.id === model)) return Promise.reject(new HubError("invalid", "現在のCodex CLIで利用できるModelを選択してください。"));
    if (typeof options.prompt !== "string" || !options.prompt.trim() || Buffer.byteLength(options.prompt) > 512 * 1024
      || typeof options.developerInstructions !== "string" || Buffer.byteLength(options.developerInstructions) > 128 * 1024) {
      return Promise.reject(new HubError("invalid", "Codexへ渡す会話の内容またはサイズを確認してください。"));
    }
    const tools = options.dynamicTools ?? [];
    if (tools.length > 1 || tools.some(tool => tool.type !== "function" || tool.name !== "nirai_command") || (tools.length && !options.onToolCall)) {
      return Promise.reject(new HubError("invalid", "CodexのTool権限が正しく設定されていません。"));
    }
    const dynamicToolsHost = tools.length === 1;
    const controller = new AbortController();
    this.controller = controller;
    const abort = () => controller.abort();
    signal.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, this.options.generationTimeoutMs ?? 25 * 60 * 1000);
    const operation = this.withClient(controller.signal, async (client, cwd) => {
      await this.requireChatgpt(client, controller.signal);
      const configResult = object(await client.request("config/read", { includeLayers: false, cwd }, controller.signal));
      const config = object(configResult.config);
      this.requireSafeConfig(config, dynamicToolsHost);
      const mcp = object(config.mcp_servers);
      const disabledMcpServers = Object.keys(mcp).filter(id => object(mcp[id]).enabled !== false);
      if (disabledMcpServers.length) {
        // Empty TOML maps merge with inherited maps. Disable each concrete server, then verify the new process.
        await client.close();
        return this.withClient(controller.signal, next => this.runOnClient(next, cwd, model, options, controller.signal), disabledMcpServers, cwd, dynamicToolsHost);
      }
      return this.runOnClient(client, cwd, model, options, controller.signal);
    }, [], undefined, dynamicToolsHost).catch(error => {
      if (controller.signal.aborted) throw interrupted();
      if (this.state.state !== "blocked") this.state = { state: "unavailable", reason: "Codex CLIの返答を取得できませんでした。接続を確認してください。" };
      if (!(error instanceof HubError)) throw new HubError("unavailable", "Codexの返答を取得できませんでした。自動で再送はしません。");
      throw error;
    }).finally(() => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      this.active = null;
      this.controller = null;
    });
    this.active = operation;
    return operation;
  }

  private async requireChatgpt(client: CodexClient, signal: AbortSignal): Promise<void> {
    const result = object(await client.request("account/read", { refreshToken: false }, signal));
    if (object(result.account).type !== "chatgpt") {
      this.state = { state: "blocked", reason: "Codex CLIへChatGPTでログインしてください（codex login）。" };
      throw new HubError("unavailable", this.state.reason!);
    }
  }

  private requireSafeConfig(config: ObjectValue, dynamicToolsHost: boolean): void {
    const features = object(config.features);
    const host = object(features.code_mode_host);
    const safeHost = dynamicToolsHost ? host.enabled === true && host.disable_in_process_fallback === true
      : features.code_mode_host === false;
    if (CODEX_DISABLED_FEATURES.some(name => features[name] !== false) || !safeHost || config.web_search !== "disabled") {
      this.state = { state: "blocked", reason: "Codex CLIの安全設定を確認できません。生成を開始しません。" };
      throw new HubError("unavailable", this.state.reason!);
    }
  }

  private async withClient<T>(signal: AbortSignal, action: (client: CodexClient, cwd: string) => Promise<T>, disabledMcpServers: readonly string[] = [], existingCwd?: string, dynamicToolsHost = false): Promise<T> {
    let cwd = existingCwd;
    let client: CodexClient | undefined;
    try {
      if (!cwd) {
        const parent = join(this.dataRoot, "providers", "codex");
        await mkdir(parent, { recursive: true });
        cwd = await mkdtemp(join(parent, "session-"));
      }
      signal.throwIfAborted();
      const factory = this.options.createClient ?? ((directory: string, opts: CodexClientOptions) => CodexAppServerClient.open(directory, opts));
      client = await factory(cwd, { ...this.options, signal, disabledMcpServers, dynamicToolsHost });
      signal.throwIfAborted();
      return await action(client, cwd);
    } finally {
      await client?.close();
      if (cwd && !existingCwd) await rm(cwd, { recursive: true, force: true });
    }
  }

  private async runOnClient(client: CodexClient, cwd: string, model: string, options: CodexRunOptions, signal: AbortSignal): Promise<string> {
    await this.requireChatgpt(client, signal);
    const effective = object(object(await client.request("config/read", { includeLayers: false, cwd }, signal)).config);
    this.requireSafeConfig(effective, (options.dynamicTools?.length ?? 0) === 1);
    if (Object.values(object(effective.mcp_servers)).some(server => object(server).enabled !== false)) {
      throw new HubError("unavailable", "Codex CLIの外部Toolを停止できません。生成を開始しません。");
    }
    const started = object(await client.request("thread/start", { model, modelProvider: "openai", cwd,
      approvalPolicy: "never", sandbox: "read-only", ephemeral: true, environments: [], runtimeWorkspaceRoots: [],
      selectedCapabilityRoots: [], dynamicTools: options.dynamicTools ?? [],
      baseInstructions: "You are the configured Resident in Nirai. Produce one final reply for the current input.",
      developerInstructions: options.developerInstructions }, signal));
    const thread = object(started.thread);
    const threadId = thread.id;
    if (typeof threadId !== "string" || started.modelProvider !== "openai" || started.model !== model
      || object(started.sandbox).type !== "readOnly" || started.approvalPolicy !== "never" || thread.ephemeral !== true
      || !Array.isArray(thread.environments) || thread.environments.length !== 0
      || !Array.isArray(started.runtimeWorkspaceRoots) || started.runtimeWorkspaceRoots.length !== 0) {
      throw new HubError("unavailable", "Codex CLIの会話設定を確認できません。生成を開始しません。");
    }
    let turnId: string | null = null;
    const turns = new Map<string, ObjectValue>();
    const replies = new Map<string, Map<string, ObjectValue>>();
    const calls = new Map<string, { fingerprint: string; result: Promise<CodexToolResult> }>();
    const queuedRequests: Array<{ id: string | number; params: ObjectValue }> = [];
    let settled = false;
    let finish: (value: string) => void = () => {};
    let fail: (error: Error) => void = () => {};
    const result = new Promise<string>((resolve, reject) => { finish = resolve; fail = reject; });
    void result.catch(() => {});
    const reject = (error: Error) => { if (!settled) { settled = true; fail(error); void client.close(); } };
    const recordItem = (id: string, value: unknown) => {
      const item = object(value);
      if (item.type !== "agentMessage") return;
      if (typeof item.text !== "string" || typeof item.id !== "string" || Buffer.byteLength(item.text) > MAX_OUTPUT_BYTES) {
        reject(new HubError("invalid", "Codexの返答が64 KiBを超えたか、本文を取得できません。")); return;
      }
      if (!replies.has(id)) replies.set(id, new Map());
      replies.get(id)!.set(item.id, item);
    };
    const check = () => {
      if (settled || !turnId || !turns.has(turnId)) return;
      const turn = turns.get(turnId)!;
      if (turn.status !== "completed") { reject(interrupted()); return; }
      if (Array.isArray(turn.items)) for (const item of turn.items) recordItem(turnId, item);
      if (settled) return;
      const messages = [...(replies.get(turnId)?.values() ?? [])];
      const final = messages.filter(item => item.phase === "final_answer");
      const fallback = messages.filter(item => item.phase === null || item.phase === undefined);
      const candidates = final.length ? final : fallback.slice(-1);
      const content = candidates[0]?.text;
      if (candidates.length !== 1 || typeof content !== "string" || !content.trim()) {
        reject(new HubError("invalid", "Codexの最終返答を確認できません。自動で再送はしません。")); return;
      }
      settled = true;
      finish(content);
    };
    const onAbort = () => reject(interrupted());
    signal.addEventListener("abort", onAbort, { once: true });
    const dispatchTool = (id: string | number, params: ObjectValue) => {
      if (settled || signal.aborted || params.threadId !== threadId || params.turnId !== turnId
        || typeof params.tool !== "string" || params.namespace != null || typeof params.callId !== "string"
        || !options.dynamicTools?.some(tool => tool.name === params.tool) || !options.onToolCall) {
        client.rejectRequest(id); reject(new HubError("unavailable", "Codexが未許可のToolを要求したため、返答を中断しました。")); return;
      }
      const fingerprint = JSON.stringify(params);
      const existing = calls.get(params.callId);
      if (existing && existing.fingerprint !== fingerprint) { client.rejectRequest(id); reject(interrupted()); return; }
      const execution = existing?.result ?? Promise.resolve().then(() => options.onToolCall!(params.tool as string, params.arguments, signal));
      if (!existing) calls.set(params.callId, { fingerprint, result: execution });
      void execution.then(value => { if (!settled && !signal.aborted) client.respond(id, value); }, () => {
        client.rejectRequest(id); reject(new HubError("unavailable", "CodexのTool要求を処理できませんでした。"));
      });
    };
    client.onFailure = reject;
    client.onNotification = (method, value) => {
      const params = object(value);
      if (params.threadId !== threadId) return;
      if (method === "item/completed" && typeof params.turnId === "string") recordItem(params.turnId, params.item);
      if (method === "turn/completed") {
        const turn = object(params.turn);
        if (typeof turn.id === "string") { turns.set(turn.id, turn); check(); }
      }
      if (method === "item/started" || method === "item/completed") {
        const type = object(params.item).type;
        if (["commandExecution", "fileChange", "mcpToolCall", "collabAgentToolCall", "imageGeneration", "webSearch"].includes(String(type))) {
          reject(new HubError("unavailable", "Codexが未許可の操作を開始したため、返答を中断しました。"));
        }
      }
    };
    client.onServerRequest = (id, method, value) => {
      const params = object(value);
      if (method !== "item/tool/call" || params.threadId !== threadId || !options.onToolCall) {
        client.rejectRequest(id); reject(new HubError("unavailable", "Codexが未許可の操作を要求したため、返答を中断しました。")); return;
      }
      if (!turnId) {
        if (queuedRequests.length >= 16) { client.rejectRequest(id); reject(interrupted()); }
        else queuedRequests.push({ id, params });
      } else dispatchTool(id, params);
    };
    try {
      const response = object(await client.request("turn/start", { threadId, model, environments: [],
        approvalPolicy: "never", sandboxPolicy: { type: "readOnly", networkAccess: false },
        input: [{ type: "text", text: options.prompt, text_elements: [] }] }, signal));
      const turn = object(response.turn);
      if (typeof turn.id !== "string") throw new HubError("invalid", "Codexの生成識別子を取得できません。");
      turnId = turn.id;
      for (const request of queuedRequests) dispatchTool(request.id, request.params);
      if (["completed", "failed", "interrupted"].includes(String(turn.status))) turns.set(turnId, turn);
      check();
      return await result;
    } finally {
      signal.removeEventListener("abort", onAbort);
      client.onFailure = () => {};
      client.onNotification = () => {};
      client.onServerRequest = id => client.rejectRequest(id);
    }
  }

  close(): Promise<void> {
    if (!this.closePromise) {
      this.closed = true;
      this.controller?.abort();
      this.closePromise = Promise.resolve(this.active).then(() => {}, () => {});
    }
    return this.closePromise;
  }
}
