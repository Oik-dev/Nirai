import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import test from "node:test";
import { ConversationProviders, ConversationRuntime, type ConversationInput } from "../src/hub/conversation.js";
import { HubStore } from "../src/hub/store.js";
import { HubService } from "../src/hub/service.js";
import { PERSONA_MAX_BYTES, readPersona } from "../src/hub/persona.js";

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "nirai-conversation-"));
  const store = new HubStore(join(root, "hub.sqlite3"));
  const providers = new ConversationProviders();
  const runtime = new ConversationRuntime(store, providers);
  const service = new HubService(store);
  service.onChatMessage = () => runtime.schedule();
  function send(type: string, payload: Record<string, unknown>) {
    return service.handleMasterCommand({
      protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(),
      type, target: null, payload,
    });
  }
  return { root, store, providers, runtime, send,
    async close() { await runtime.close(); store.close(); rmSync(root, { recursive: true, force: true }); } };
}

test("Persona is read from its current UTF-8 file and rejects oversized or remote input", async () => {
  const f = fixture();
  try {
    const path = join(f.root, "persona.md");
    writeFileSync(path, "落ち着いた言葉で話す。");
    const first = await readPersona(path);
    writeFileSync(path, "明るく話す。");
    const second = await readPersona(path);
    assert.notEqual(first.fingerprint, second.fingerprint);
    assert.equal(second.text, "明るく話す。");
    writeFileSync(path, Buffer.alloc(PERSONA_MAX_BYTES + 1, 65));
    await assert.rejects(readPersona(path), /64 KiB/);
    await assert.rejects(readPersona("\\\\remote\\share\\persona.md"), /ローカル/);
    await assert.rejects(readPersona("relative.md"), /ローカル/);
  } finally { await f.close(); }
});

test("normal generation receives one person's Say and Whisper with their Persona without Task authority", async () => {
  const f = fixture();
  const inputs: ConversationInput[] = [];
  try {
    const path = join(f.root, "persona.md");
    writeFileSync(path, "静かに話す。");
    f.providers.register({ id: "test-conversation", availability: () => ({ state: "ready" }),
      async generate(input) { inputs.push(input); return `返答:${input.message.content}`; } });
    f.send("CreateResident", { id: "serina", display_name: "Serina", capability_id: "test-conversation", persona_path: path });
    f.send("SendChatMessage", { channel: "whisper", resident_id: "serina", content: "秘密の話" });
    await f.runtime.idle();
    f.send("SendChatMessage", { channel: "say", content: "公開の話" });
    await f.runtime.idle();
    assert.equal(inputs.length, 2);
    assert.equal(inputs[0]!.persona?.text, "静かに話す。");
    assert.deepEqual(inputs[1]!.messages.map(item => item.content), ["秘密の話", "返答:秘密の話", "公開の話"]);
    assert.deepEqual(inputs[1]!.messages.map(item => item.channel), ["whisper", "whisper", "say"]);
    assert.equal(inputs[1]!.conversation.kind, "say");
    assert.equal("task_id" in inputs[1]!, false);
    assert.equal(f.store.listTasks().length, 0);
    assert.equal(f.store.listRuns().length, 0);
    const snapshot = f.store.snapshot();
    const messages = snapshot.messages as Array<{ content: string }>;
    assert.ok(messages.some(item => item.content === "返答:秘密の話"));
    assert.ok(messages.some(item => item.content === "返答:公開の話"));
  } finally { await f.close(); }
});

test("unconnected AI leaves an honest failed response and never fabricates a Resident reply", async () => {
  const f = fixture();
  try {
    f.send("CreateResident", { id: "serina", display_name: "Serina" });
    f.send("SendChatMessage", { channel: "say", content: "こんにちは" });
    await f.runtime.idle();
    const snapshot = f.store.snapshot();
    assert.equal((snapshot.messages as unknown[]).length, 1);
    const responses = snapshot.chat_responses as Array<{ state: string; error: string }>;
    assert.equal(responses[0]!.state, "failed");
    assert.match(responses[0]!.error, /接続されていません/);
    f.runtime.schedule();
    await f.runtime.idle();
    assert.equal((f.store.snapshot().messages as unknown[]).length, 1);
  } finally { await f.close(); }
});

test("provider unavailability explains the next action from one current observation", () => {
  const providers = new ConversationProviders();
  let observations = 0;
  providers.register({ id: "holo", availability() {
    observations++;
    return { state: "blocked", reason: "ChatGPTへログインしてください" };
  }, async generate() { throw new Error("must not generate"); } });
  assert.throws(() => providers.get("holo"), /会話用AIを現在利用できません。 ChatGPTへログインしてください/);
  assert.equal(observations, 1);
  assert.throws(() => providers.get("missing"), /会話用AIが接続されていません/);
});

test("queued generation identifies its current question while including a late earlier reply", async () => {
  const f = fixture();
  const inputs: ConversationInput[] = [];
  let releaseFirst: (content: string) => void = () => {};
  let enteredFirst: () => void = () => {};
  const firstStarted = new Promise<void>(resolve => { enteredFirst = resolve; });
  try {
    f.providers.register({ id: "test-conversation", availability: () => ({ state: "ready" }),
      generate(input) {
        inputs.push(input);
        if (input.message.content === "質問1") {
          enteredFirst();
          return new Promise<string>(resolve => { releaseFirst = resolve; });
        }
        return Promise.resolve(`返答:${input.message.content}`);
      } });
    f.send("CreateResident", { id: "serina", display_name: "Serina", capability_id: "test-conversation" });
    f.send("SendChatMessage", { channel: "whisper", resident_id: "serina", content: "質問1" });
    await firstStarted;
    const second = f.send("SendChatMessage", { channel: "whisper", resident_id: "serina", content: "質問2" });
    releaseFirst("返答:質問1");
    await f.runtime.idle();
    assert.equal(inputs.length, 2);
    assert.equal(inputs[1]!.message.id, second.message_id);
    assert.equal(inputs[1]!.message.content, "質問2");
    assert.deepEqual(inputs[1]!.messages.map(message => message.content), ["質問1", "質問2", "返答:質問1"]);
    assert.equal(inputs[1]!.messages.at(-1)!.sender, "serina");
    assert.deepEqual((f.store.snapshot().messages as Array<{ content: string }>).map(message => message.content),
      ["質問1", "質問2", "返答:質問1", "返答:質問2"]);
    assert.deepEqual(f.store.listTasks(), []);
    assert.deepEqual(f.store.listRuns(), []);
  } finally { await f.close(); }
});

test("closing aborts normal generation and rejects late replies without automatic resend", async () => {
  const f = fixture();
  let release: (value: string) => void = () => {};
  let entered: () => void = () => {};
  const started = new Promise<void>(resolve => { entered = resolve; });
  try {
    f.providers.register({ id: "test-conversation", availability: () => ({ state: "ready" }),
      generate() { entered(); return new Promise<string>(resolve => { release = resolve; }); } });
    f.send("CreateResident", { id: "serina", display_name: "Serina", capability_id: "test-conversation" });
    f.send("SendChatMessage", { channel: "say", content: "途中で終了" });
    await started;
    f.send("SendChatMessage", { channel: "say", content: "生成待ちも終了" });
    await f.runtime.close();
    release("遅れて返答");
    await Promise.resolve();
    const snapshot = f.store.snapshot();
    assert.equal((snapshot.messages as unknown[]).length, 2);
    assert.deepEqual((snapshot.chat_responses as Array<{ state: string }>).map(response => response.state), ["interrupted", "interrupted"]);
    assert.equal(f.store.claimNextChatResponse(), null);
  } finally { await f.close(); }
});
