import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import test from "node:test";
import { CodexConversationProvider, codexConversationPrompt, type CodexRunOptions } from "../src/hub/codex.js";
import { CODEX_DISABLED_FEATURES, CodexAppServerClient, codexProcessArgs, type CodexClient, type CodexClientOptions } from "../src/hub/codex-client.js";
import type { ConversationInput } from "../src/hub/conversation.js";

const textReply = "  同じ本文です。\n**整形は保持**\n";
const safeConfig = (servers: Record<string, unknown> = {}, dynamicToolsHost = false) => ({ features: {
  ...Object.fromEntries(CODEX_DISABLED_FEATURES.map(name => [name, false])),
  code_mode_host: dynamicToolsHost ? { enabled: true, disable_in_process_fallback: true } : false },
  web_search: "disabled", mcp_servers: servers });

class FakeClient implements CodexClient {
  readonly serverVersion = "0.159.2";
  onNotification: CodexClient["onNotification"] = () => {};
  onServerRequest: CodexClient["onServerRequest"] = () => {};
  onFailure: CodexClient["onFailure"] = () => {};
  requests: Array<{ method: string; params: unknown }> = [];
  responses: Array<{ id: string | number; result: unknown }> = [];
  rejected: Array<string | number> = [];
  closed = false;
  accountType = "chatgpt";
  threadEnvironments: unknown = [];
  runtimeWorkspaceRoots: unknown = [];
  approvalPolicy: unknown = "never";
  config = safeConfig();
  onTurn?: (client: FakeClient) => unknown | Promise<unknown>;
  onRequest?: (method: string) => void;
  onResponse?: (id: string | number) => void;

  async request(method: string, params: unknown): Promise<unknown> {
    this.requests.push({ method, params });
    this.onRequest?.(method);
    if (method === "account/read") return { account: { type: this.accountType }, requiresOpenaiAuth: true };
    if (method === "model/list") return { data: [{ id: "catalog-id", model: "test-model", displayName: "Test Model", isDefault: true },
      { id: "hidden", model: "hidden", displayName: "Hidden", hidden: true }], nextCursor: null };
    if (method === "config/read") return { config: this.config };
    if (method === "thread/start") return { thread: { id: "provider-thread", ephemeral: true, environments: this.threadEnvironments },
      model: "test-model", modelProvider: "openai", sandbox: { type: "readOnly" },
      runtimeWorkspaceRoots: this.runtimeWorkspaceRoots, approvalPolicy: this.approvalPolicy };
    if (method === "turn/start") return this.onTurn ? this.onTurn(this) : { turn: { id: "provider-turn", status: "completed",
      items: [{ type: "agentMessage", id: "reply", text: textReply, phase: "final_answer" }] } };
    throw new Error(`unexpected method ${method}`);
  }
  notify(): void {}
  respond(id: string | number, result: unknown): void { this.responses.push({ id, result }); this.onResponse?.(id); }
  rejectRequest(id: string | number): void { this.rejected.push(id); }
  async close(): Promise<void> { this.closed = true; }
}

async function providerFixture(configure?: (client: FakeClient, options: CodexClientOptions) => void, timeout?: number) {
  const root = await mkdtemp(join(tmpdir(), "nirai-codex-test-"));
  const clients: FakeClient[] = [];
  const factoryOptions: CodexClientOptions[] = [];
  const provider = new CodexConversationProvider(root, { ...(timeout === undefined ? {} : { generationTimeoutMs: timeout }),
    createClient: async (_cwd, options) => {
      const client = new FakeClient();
      client.config = safeConfig({}, options.dynamicToolsHost);
      clients.push(client);
      factoryOptions.push(options);
      configure?.(client, options);
      return client;
    } });
  return { root, provider, clients, factoryOptions,
    async close() { await provider.close(); await rm(root, { recursive: true, force: true }); } };
}

function conversationInput(): ConversationInput {
  const message = { id: "current", conversation_id: "whisper-one", seq: 4, sender: "master", content: "今の質問", audience: ["master", "resident-one"], reply_to_message_id: null, created_at: "2026-10-02T00:00:00Z" };
  return {
    resident: { id: "resident-one", display_name: "Resident One", role: "Guide", persona_path: "persona.md", capability_id: "codex", model: "test-model", created_at: message.created_at, updated_at: message.created_at },
    conversation: { id: "whisper-one", kind: "whisper", resident_id: "resident-one", task_id: null, created_at: message.created_at, updated_at: message.created_at },
    message, persona: { path: "persona.md", text: "穏やかな日本語で話す。", fingerprint: "current-persona" },
    messages: [
      { ...message, id: "say", content: "共有された話", channel: "say" },
      { ...message, id: "own", sender: "resident-one", content: "本人の前の返答", channel: "whisper" },
      { ...message, id: "other", audience: ["master", "resident-two"], content: "他人のWhisper", channel: "whisper" },
      { ...message, channel: "whisper" },
    ],
  };
}

test("Codex uses existing ChatGPT authentication and exposes the current model catalog without generation", async () => {
  const f = await providerFixture();
  try {
    await f.provider.refresh();
    assert.equal(f.provider.availability().state, "ready");
    assert.deepEqual(f.provider.models, [{ id: "test-model", display_name: "Test Model" }]);
    assert.deepEqual(f.clients[0]!.requests.map(item => item.method), ["account/read", "model/list"]);
    assert.deepEqual(f.clients[0]!.requests[0]!.params, { refreshToken: false });
    assert.ok(f.clients.every(client => client.closed));
    f.clients.length = 0;
    await f.provider.generate(conversationInput(), new AbortController().signal);
    assert.equal(f.clients.length, 1);
  } finally { await f.close(); }
});

test("API-key and missing CLI authentication block generation without login mutation or retry", async () => {
  for (const type of ["apiKey", "missing"]) {
    const f = await providerFixture(client => { client.accountType = type; });
    try {
      await f.provider.refresh();
      assert.equal(f.provider.availability().state, "blocked");
      assert.deepEqual(f.provider.models, []);
      await assert.rejects(f.provider.generate(conversationInput(), new AbortController().signal), /ChatGPT/);
      assert.deepEqual(f.clients[0]!.requests.map(item => item.method), ["account/read"]);
      assert.equal(f.clients.length, 1);
    } finally { await f.close(); }
  }
});

test("nonempty or unverified environments, workspace roots, and approval policy stop before generation", async () => {
  for (const configure of [
    (client: FakeClient) => { client.threadEnvironments = [{ id: "inherited-local" }]; },
    (client: FakeClient) => { client.threadEnvironments = null; },
    (client: FakeClient) => { client.runtimeWorkspaceRoots = ["inherited-workspace"]; },
    (client: FakeClient) => { client.runtimeWorkspaceRoots = undefined; },
    (client: FakeClient) => { client.approvalPolicy = "on-request"; },
  ]) {
    const f = await providerFixture(configure);
    try {
      await f.provider.refresh();
      await assert.rejects(f.provider.generate(conversationInput(), new AbortController().signal), /会話設定/);
      assert.ok(f.clients.every(client => client.closed));
      assert.equal(f.clients.some(client => client.requests.some(item => item.method === "turn/start")), false);
    } finally { await f.close(); }
  }
});

test("conversation Persona and received history preserve the current audience and final Provider text", async () => {
  const f = await providerFixture(client => {
    client.onTurn = current => {
      current.onNotification("item/completed", { threadId: "alien-thread", turnId: "provider-turn", item: { type: "agentMessage", id: "wrong", phase: "final_answer", text: "wrong" } });
      current.onNotification("item/completed", { threadId: "provider-thread", turnId: "old-turn", item: { type: "agentMessage", id: "old", phase: "final_answer", text: "old" } });
      current.onNotification("item/completed", { threadId: "provider-thread", turnId: "provider-turn", item: { type: "agentMessage", id: "comment", phase: "commentary", text: "working" } });
      current.onNotification("item/completed", { threadId: "provider-thread", turnId: "provider-turn", item: { type: "agentMessage", id: "reply", phase: "final_answer", text: textReply } });
      current.onNotification("turn/completed", { threadId: "provider-thread", turn: { id: "provider-turn", status: "completed", items: [] } });
      return { turn: { id: "provider-turn", status: "inProgress", items: [] } };
    };
  });
  try {
    await f.provider.refresh();
    const input = conversationInput();
    assert.equal(await f.provider.generate(input, new AbortController().signal), textReply);
    const current = f.clients.at(-1)!;
    const start = current.requests.find(item => item.method === "thread/start")!.params as Record<string, unknown>;
    assert.deepEqual(start.environments, []);
    assert.deepEqual(start.dynamicTools, []);
    assert.deepEqual(start.runtimeWorkspaceRoots, []);
    assert.equal(start.ephemeral, true);
    assert.equal(start.sandbox, "read-only");
    assert.equal(start.model, input.resident.model);
    assert.match(start.developerInstructions as string, /穏やかな日本語/);
    const turn = current.requests.find(item => item.method === "turn/start")!.params as { input: Array<{ text: string }>; environments: unknown[] };
    const prompt = JSON.parse(turn.input[0]!.text) as { current_message: { content: string; audience: string[] }; received_history: Array<{ content: string }> };
    assert.equal(prompt.current_message.content, input.message.content);
    assert.deepEqual(prompt.current_message.audience, input.message.audience);
    assert.deepEqual(prompt.received_history.map(item => item.content), ["共有された話", "本人の前の返答"]);
    assert.deepEqual(turn.environments, []);
    assert.ok(current.closed);
    assert.deepEqual(await readdir(join(f.root, "providers", "codex")), []);
  } finally { await f.close(); }
});

test("all inherited standalone MCP servers are disabled and rechecked before starting a thread", async () => {
  const f = await providerFixture((client, options) => {
    client.config = safeConfig({ external: { enabled: options.disabledMcpServers?.includes("external") === true ? false : true } });
  });
  try {
    await f.provider.refresh();
    await f.provider.generate(conversationInput(), new AbortController().signal);
    assert.equal(f.clients.length, 3);
    assert.equal(f.clients[1]!.requests.some(item => item.method === "thread/start"), false);
    assert.deepEqual(f.factoryOptions[2]!.disabledMcpServers, ["external"]);
    assert.equal(f.clients[2]!.requests.filter(item => item.method === "turn/start").length, 1);
    assert.ok(f.clients.every(client => client.closed));
  } finally { await f.close(); }
});

test("unverified safety settings and ignored MCP disables fail before Provider generation", async () => {
  for (const kind of ["feature", "mcp"]) {
    const f = await providerFixture(client => {
      if (kind === "feature") (client.config.features as Record<string, unknown>).shell_tool = true;
      else client.config = safeConfig({ ignoresDisable: { enabled: true } });
    });
    try {
      await f.provider.refresh();
      await assert.rejects(f.provider.generate(conversationInput(), new AbortController().signal), /生成を開始しません/);
      assert.equal(f.clients.some(client => client.requests.some(item => item.method === "thread/start" || item.method === "turn/start")), false);
      assert.ok(f.clients.every(client => client.closed));
    } finally { await f.close(); }
  }
});

test("ordinary conversation rejects Provider tool requests and never invokes a Task callback", async () => {
  const f = await providerFixture(client => {
    client.onTurn = current => {
      current.onServerRequest("approval", "item/commandExecution/requestApproval", { threadId: "provider-thread", turnId: "provider-turn" });
      return { turn: { id: "provider-turn", status: "inProgress", items: [] } };
    };
  });
  try {
    await f.provider.refresh();
    await assert.rejects(f.provider.generate(conversationInput(), new AbortController().signal), /未許可/);
    assert.deepEqual(f.clients.at(-1)!.rejected, ["approval"]);
    assert.ok(f.clients.at(-1)!.closed);
    assert.equal(f.clients.at(-1)!.requests.filter(item => item.method === "turn/start").length, 1);
  } finally { await f.close(); }
});

test("the registered Task tool is correlated to one turn and duplicate delivery invokes it once", async () => {
  let calls = 0;
  const f = await providerFixture(client => {
    client.onTurn = current => {
      const params = { threadId: "provider-thread", turnId: "provider-turn", callId: "same-call", namespace: null, tool: "nirai_command", arguments: { type: "CompleteTask" } };
      current.onServerRequest(10, "item/tool/call", params);
      current.onServerRequest(11, "item/tool/call", params);
      current.onResponse = id => {
        if (id === 11) current.onNotification("turn/completed", { threadId: "provider-thread", turn: { id: "provider-turn", status: "completed",
          items: [{ type: "agentMessage", id: "reply", phase: "final_answer", text: textReply }] } });
      };
      return { turn: { id: "provider-turn", status: "inProgress", items: [] } };
    };
  });
  const options: CodexRunOptions = { prompt: "Task input", developerInstructions: "Task rules", dynamicTools: [{ type: "function", name: "nirai_command", description: "Hub command", inputSchema: {} }],
    async onToolCall(tool, args) { calls++; assert.equal(tool, "nirai_command"); assert.deepEqual(args, { type: "CompleteTask" }); return { contentItems: [{ type: "inputText", text: "accepted" }], success: true }; } };
  try {
    await f.provider.refresh();
    assert.equal(await f.provider.run(options, new AbortController().signal), textReply);
    assert.equal(calls, 1);
    assert.equal(f.clients.at(-1)!.responses.length, 2);
  } finally { await f.close(); }
});

test("abort, generation timeout, EOF, failed turns, and oversized final output close the session without replay", async () => {
  for (const kind of ["abort", "timeout", "eof", "failed", "oversized"]) {
    let entered: () => void = () => {};
    const started = new Promise<void>(resolve => { entered = resolve; });
    const f = await providerFixture(client => {
      client.onTurn = current => {
        entered();
        if (kind === "eof") queueMicrotask(() => current.onFailure(new Error("EOF")));
        if (kind === "failed") return { turn: { id: "provider-turn", status: "failed", items: [] } };
        if (kind === "oversized") return { turn: { id: "provider-turn", status: "completed", items: [{ type: "agentMessage", id: "too-big", phase: "final_answer", text: "あ".repeat(30_000) }] } };
        return { turn: { id: "provider-turn", status: "inProgress", items: [] } };
      };
    }, kind === "timeout" ? 20 : undefined);
    try {
      await f.provider.refresh();
      const controller = new AbortController();
      const result = f.provider.generate(conversationInput(), controller.signal);
      const failed = assert.rejects(result);
      await started;
      if (kind === "abort") controller.abort();
      await failed;
      assert.ok(f.clients.at(-1)!.closed);
      assert.equal(f.clients.at(-1)!.requests.filter(item => item.method === "turn/start").length, 1);
      f.clients.at(-1)!.onNotification("turn/completed", { threadId: "provider-thread", turn: { id: "provider-turn", status: "completed", items: [{ type: "agentMessage", id: "late", phase: "final_answer", text: "late" }] } });
    } finally { await f.close(); }
  }
});

test("conversation history is byte bounded without cutting messages or leaking an unrelated Whisper", () => {
  const input = conversationInput();
  input.messages = Array.from({ length: 40 }, (_, index) => ({ ...input.messages[0]!, id: `history-${index}`, content: "あ".repeat(16_000) }));
  const prompt = JSON.parse(codexConversationPrompt(input).prompt) as { received_history: Array<{ content: string }> };
  assert.ok(prompt.received_history.length < 40);
  assert.ok(prompt.received_history.every(item => item.content === input.messages[0]!.content));
});

class WireChild extends EventEmitter {
  readonly stdin = new PassThrough();
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  exitCode: number | null = null;
  signalCode: NodeJS.Signals | null = null;
  kills = 0;
  userAgent = "nirai_v2/0.159.2 (Windows NT)";
  respondInitialize = true;
  ignoreInputEnd = false;
  readonly messages: Array<Record<string, unknown>> = [];
  onMessage: (message: Record<string, unknown>) => void = () => {};

  constructor() {
    super();
    let pending = "";
    this.stdin.on("data", (chunk: Buffer) => {
      pending += chunk.toString();
      let end: number;
      while ((end = pending.indexOf("\n")) >= 0) {
        const message = JSON.parse(pending.slice(0, end)) as Record<string, unknown>;
        pending = pending.slice(end + 1);
        this.messages.push(message);
        if (message.method === "initialize" && this.respondInitialize) this.send({ id: message.id, result: { userAgent: this.userAgent } });
        else this.onMessage(message);
      }
    });
    this.stdin.once("finish", () => { if (!this.ignoreInputEnd) this.exit(); });
  }
  send(value: unknown) {
    const bytes = Buffer.from(`${JSON.stringify(value)}\n`);
    const cut = Math.max(1, bytes.indexOf(Buffer.from("あ")) + 1);
    this.stdout.write(bytes.subarray(0, cut));
    this.stdout.write(bytes.subarray(cut));
  }
  kill(signal: NodeJS.Signals) { this.kills++; this.signalCode = signal; this.exit(); return true; }
  exit() { if (this.exitCode !== null) return; this.exitCode = 0; queueMicrotask(() => this.emit("close", 0, this.signalCode)); }
}

test("app-server transport handles split UTF-8 and rejects EOF or malformed messages without retry", async () => {
  for (const kind of ["split", "eof", "malformed", "size", "timeout"]) {
    const child = new WireChild();
    let spawned = 0;
    const client = await CodexAppServerClient.open(tmpdir(), { executable: process.execPath, requestTimeoutMs: 25,
      spawnProcess: (_executable, args, _cwd, env) => {
        spawned++;
        assert.ok(args.includes("shell_tool"));
        assert.equal(env.OPENAI_API_KEY, undefined);
        return child as unknown as ChildProcessWithoutNullStreams;
      } });
    try {
      child.onMessage = message => {
        if (message.method !== "test") return;
        if (kind === "split") child.send({ id: message.id, result: "あの返答" });
        if (kind === "eof") child.stdout.end();
        if (kind === "malformed") child.stdout.write("not JSON\n");
        if (kind === "size") child.stdout.write(Buffer.alloc(1024 * 1024 + 1, 65));
      };
      if (kind === "split") assert.equal(await client.request("test", {}), "あの返答");
      else await assert.rejects(client.request("test", {}));
      assert.equal(spawned, 1);
      assert.equal(child.messages.filter(item => item.method === "test").length, 1);
    } finally { await client.close(); }
  }
});

test("CLI safety arguments use concrete dotted MCP ids and reject unsupported path segments", () => {
  assert.ok(codexProcessArgs(["node_repl"]).includes("mcp_servers.node_repl.enabled=false"));
  assert.throws(() => codexProcessArgs(["unsafe.segment"]), /無効化/);
  assert.equal(codexProcessArgs().some(value => value.includes("api_key")), false);
});

test("an unsupported CLI version and cancellation during initialization close the owned process", async () => {
  const old = new WireChild();
  old.userAgent = "nirai_v2/0.100.0 (Windows NT)";
  await assert.rejects(CodexAppServerClient.open(tmpdir(), { executable: process.execPath,
    spawnProcess: () => old as unknown as ChildProcessWithoutNullStreams }), /0\.159\.2/);
  assert.equal(old.exitCode, 0);
  assert.equal(old.messages.some(message => message.method === "initialized"), false);

  const child = new WireChild();
  child.respondInitialize = false;
  let entered: () => void = () => {};
  const started = new Promise<void>(resolve => { entered = resolve; });
  child.onMessage = message => { if (message.method === "initialize") entered(); };
  const controller = new AbortController();
  const opening = CodexAppServerClient.open(tmpdir(), { executable: process.execPath, signal: controller.signal,
    spawnProcess: () => child as unknown as ChildProcessWithoutNullStreams });
  const failed = assert.rejects(opening);
  await started;
  controller.abort();
  await failed;
  assert.equal(child.exitCode, 0);
  assert.equal(child.messages.filter(message => message.method === "initialize").length, 1);
});

test("a server that ignores stdin shutdown is terminated and Provider errors do not expose raw diagnostics", async () => {
  const child = new WireChild();
  child.ignoreInputEnd = true;
  child.onMessage = message => { if (message.method === "test") child.send({ id: message.id, error: { code: -1, message: "private provider diagnostic" } }); };
  const client = await CodexAppServerClient.open(tmpdir(), { executable: process.execPath,
    spawnProcess: () => child as unknown as ChildProcessWithoutNullStreams });
  await assert.rejects(client.request("test", {}), error => error instanceof Error && !error.message.includes("private provider diagnostic"));
  await Promise.all([client.close(), client.close()]);
  assert.equal(child.kills, 1);
  assert.equal(child.exitCode, 0);
});
