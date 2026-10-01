import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { HubService } from "../src/hub/service.js";
import { HubStore } from "../src/hub/store.js";
import type { ChatMessageRecord, ChatResponseRecord, ConversationRecord, HubCommandEnvelope } from "../src/shared/types.js";

function command(type: string, payload: Record<string, unknown>, id = randomUUID()): HubCommandEnvelope {
  return { protocol_version: 1, command_id: id, issued_at: new Date().toISOString(), type, target: null, payload };
}

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "nirai-chat-store-"));
  const path = join(root, "hub.sqlite3");
  const store = new HubStore(path);
  const service = new HubService(store);
  return { root, path, store, service, close() { store.close(); rmSync(root, { recursive: true, force: true }); } };
}

test("normal conversations freeze Say audience and preserve Whisper privacy without a Task", () => {
  const f = fixture();
  try {
    assert.throws(() => f.service.handleMasterCommand(command("SendChatMessage", { channel: "say", content: "nobody" })), /no Resident/);
    assert.equal((f.store.snapshot().conversations as ConversationRecord[]).length, 0);
    f.service.handleMasterCommand(command("CreateResident", { id: "serina", display_name: "Serina", role: "相談相手" }));
    const say = f.service.handleMasterCommand(command("SendChatMessage", { channel: "say", content: "みんなへ" }));
    assert.deepEqual(say.audience, ["serina"]);
    f.service.handleMasterCommand(command("CreateResident", { id: "alice", display_name: "Alice" }));
    const whisper = f.service.handleMasterCommand(command("SendChatMessage", { channel: "whisper", resident_id: "serina", content: "Serinaだけの話" }));
    const aliceWhisper = f.service.handleMasterCommand(command("SendChatMessage", { channel: "whisper", resident_id: "alice", content: "Aliceだけの話" }));
    assert.deepEqual(f.store.getChatContext(String(say.conversation_id), "alice").messages, []);
    assert.throws(() => f.store.getChatContext(String(whisper.conversation_id), "alice"), /another Whisper/);
    assert.throws(() => f.store.getChatContext(String(say.conversation_id), "missing"), /unavailable/);
    assert.throws(() => f.store.addChatAssistantMessage(String(whisper.conversation_id), "alice", String(whisper.message_id), "覗き見"), /another Whisper/);
    assert.throws(() => f.service.handleMasterCommand(command("SendChatMessage", { channel: "whisper", resident_id: "missing", content: "Holo" })), /unavailable/);
    assert.deepEqual(f.store.getChatContext(String(whisper.conversation_id), "serina").messages.map(m => m.content), ["Serinaだけの話"]);
    assert.deepEqual(f.store.getChatContext(String(aliceWhisper.conversation_id), "alice").messages.map(m => m.content), ["Aliceだけの話"]);
    const nextSay = f.service.handleMasterCommand(command("SendChatMessage", { channel: "say", content: "二人へ" }));
    assert.equal(nextSay.conversation_id, say.conversation_id);
    assert.deepEqual(nextSay.audience, ["serina", "alice"]);
    assert.deepEqual(f.store.getChatContext(String(say.conversation_id), "serina").messages.map(m => m.content), ["みんなへ", "二人へ"]);
    assert.deepEqual(f.store.getChatContext(String(say.conversation_id), "alice").messages.map(m => m.content), ["二人へ"]);
    const claimed = f.store.claimNextChatResponse()!;
    assert.equal(claimed.message_id, say.message_id);
    const reply = f.store.completeChatResponse(String(say.message_id), "serina", "みんなへの返答");
    assert.deepEqual(reply.audience, ["serina"]);
    assert.deepEqual(f.store.getChatContext(String(say.conversation_id), "alice").messages.map(m => m.content), ["二人へ"]);
    assert.throws(() => f.store.addChatAssistantMessage(String(say.conversation_id), "alice", String(say.message_id), "参加前の返答"), /cannot reply/);
    const failed = f.store.claimNextChatResponse()!;
    f.store.failChatResponse(failed.message_id, failed.resident_id, "未接続");
    assert.equal(f.store.getChatResponse(failed.message_id, failed.resident_id)?.state, "failed");
    assert.equal(f.store.getChatResponse(failed.message_id, failed.resident_id)?.error, "未接続");
    const snapshot = f.store.snapshot();
    assert.deepEqual(snapshot.tasks, []); assert.deepEqual(snapshot.runs, []); assert.deepEqual(snapshot.holo_turns, []);
    assert.equal((snapshot.chat_responses as ChatResponseRecord[]).some(r => r.resident_id === "holo"), false);
  } finally { f.close(); }
});

test("chat command receipts, timestamps and response outcomes survive reopen without replay", () => {
  const f = fixture();
  let reopened: HubStore | null = null;
  try {
    f.store.ensureResident("serina", "Serina");
    let callbacks = 0;
    f.service.onChatMessage = () => { callbacks++; };
    const envelope = command("SendChatMessage", { channel: "whisper", resident_id: "serina", content: "最初" });
    const result = f.service.handleMasterCommand(envelope);
    assert.deepEqual(f.service.handleMasterCommand(envelope), result);
    assert.equal(callbacks, 1);
    const input = f.store.getChatMessage(String(result.message_id))!;
    const pending = f.store.claimNextChatResponse()!;
    assert.equal(pending.state, "running");
    const reply = f.store.completeChatResponse(input.id, "serina", "返答");
    assert.deepEqual(f.store.completeChatResponse(input.id, "serina", "返答"), reply);
    assert.throws(() => f.store.completeChatResponse(input.id, "serina", "異なる返答"), /conflict/);
    const later = f.service.handleMasterCommand(command("SendChatMessage", { channel: "whisper", resident_id: "serina", content: "後の入力" }));
    assert.deepEqual(f.store.getChatContext(input.conversation_id, "serina", 40, input.seq).messages.map(m => m.content), ["最初", "返答"]);
    f.store.claimNextChatResponse();
    const waiting = f.service.handleMasterCommand(command("SendChatMessage", { channel: "whisper", resident_id: "serina", content: "生成前の入力" }));
    f.store.close();
    reopened = new HubStore(f.path);
    const reopenedService = new HubService(reopened);
    assert.deepEqual(reopenedService.handleMasterCommand({ ...envelope, issued_at: "2000-01-01T00:00:00Z" }), result);
    reopened.recoverChatResponses();
    assert.equal(reopened.getChatResponse(String(later.message_id), "serina")?.state, "interrupted");
    assert.equal(reopened.getChatResponse(String(waiting.message_id), "serina")?.state, "interrupted");
    assert.equal(reopened.claimNextChatResponse(), null);
    assert.throws(() => reopened!.completeChatResponse(String(later.message_id), "serina", "遅い返答"), /no longer running/);
    const restored = reopened.getChatContext(input.conversation_id, "serina").messages;
    assert.equal(restored[0]!.created_at, input.created_at);
    assert.equal(restored[1]!.created_at, reply.created_at);
    assert.equal(restored.length, 4);
  } finally {
    if (reopened) reopened.close(); else f.store.close();
    rmSync(f.root, { recursive: true, force: true });
  }
});

test("Resident settings retain immutable identity and Persona file references", () => {
  const f = fixture();
  try {
    const persona = join(f.root, "persona.md");
    const resident = f.service.handleMasterCommand(command("CreateResident", {
      id: "serina", display_name: "Serina", persona_path: persona, capability_id: "serina", model: "fixture-model",
    })).resident;
    assert.equal((resident as { persona_path: string }).persona_path, persona);
    const updated = f.service.handleMasterCommand(command("UpdateResident", { resident_id: "serina", display_name: "セリナ", role: "友人", model: null }));
    assert.equal((updated.resident as { id: string }).id, "serina");
    assert.equal(f.store.getResident("serina")!.persona_path, persona);
    assert.equal(f.store.getResident("serina")!.model, null);
    assert.throws(() => f.service.handleMasterCommand(command("UpdateResident", { resident_id: "serina", id: "another" })), /invalid payload field/);
    assert.throws(() => f.service.handleMasterCommand(command("CreateResident", { id: "serina", display_name: "again" })), /already exists/);
    for (const path of ["relative.md", "\\\\server\\persona.md", "//server/persona.md"]) {
      assert.throws(() => f.service.handleMasterCommand(command("UpdateResident", { resident_id: "serina", persona_path: path })), /ローカルファイル/);
    }
    assert.throws(() => f.service.handleMasterCommand(command("CreateResident", { id: "master", display_name: "偽Master" })), /invalid Resident ID/);
    assert.throws(() => f.service.handleMasterCommand(command("SendChatMessage", { channel: "whisper", resident_id: "serina", content: "x", sender: "serina" })), /invalid payload field/);
    assert.throws(() => f.service.handleMasterCommand({ ...command("SendChatMessage", { channel: "say", content: "x" }), target: "serina" }), /target must be null/);
    const task = f.store.createTask("serina");
    assert.throws(() => f.store.getChatContext(task.conversation_id, "serina"), /normal Conversation/);
    const snapshot = f.store.snapshot();
    assert.equal(JSON.stringify(snapshot.residents).includes("persona_body"), false);
    assert.deepEqual((snapshot.messages as ChatMessageRecord[]), []);
  } finally { f.close(); }
});

test("queued Chat context includes replies to earlier inputs without exposing newer questions", () => {
  const f = fixture();
  try {
    f.store.ensureResident("serina", "Serina");
    const first = f.store.addChatMasterMessage("whisper", "serina", "質問1");
    const firstResponse = f.store.claimNextChatResponse()!;
    const second = f.store.addChatMasterMessage("whisper", "serina", "質問2");
    const firstReply = f.store.completeChatResponse(firstResponse.message_id, "serina", "質問1への返答");
    assert.ok(firstReply.seq > second.seq);
    const third = f.store.addChatMasterMessage("whisper", "serina", "まだ先の質問3");
    assert.deepEqual(f.store.getChatContext(first.conversation_id, "serina", 40, second.seq).messages.map(m => m.content),
      ["質問1", "質問2", "質問1への返答"]);
    f.store.claimNextChatResponse();
    f.store.completeChatResponse(String(second.message_id), "serina", "質問2への返答");
    f.store.claimNextChatResponse();
    f.store.completeChatResponse(String(third.message_id), "serina", "質問3への返答");
    assert.deepEqual(f.store.getChatContext(first.conversation_id, "serina", 40, second.seq).messages.map(m => m.content),
      ["質問1", "質問2", "質問1への返答", "質問2への返答"]);
    assert.deepEqual(f.store.getChatContext(first.conversation_id, "serina", 40, first.seq).messages.map(m => m.content),
      ["質問1", "質問1への返答"]);
    f.store.ensureResident("alice", "Alice");
    assert.throws(() => f.store.getChatContext(first.conversation_id, "alice", 40, second.seq), /another Whisper/);
  } finally { f.close(); }
});

test("one Resident receives cross-channel memories while other people's Whisper and future inputs stay excluded", () => {
  const f = fixture();
  try {
    f.store.ensureResident("holo", "Holo");
    f.store.ensureResident("serina", "Serina");
    const secret = f.store.addChatMasterMessage("whisper", "holo", "HoloとMasterの話");
    f.store.claimNextChatResponse();
    f.store.addChatMasterMessage("whisper", "serina", "SerinaとMasterの話");
    const say = f.store.addChatMasterMessage("say", undefined, "この場の話");
    const secretReply = f.store.completeChatResponse(secret.message_id, "holo", "覚えているよ");
    const future = f.store.addChatMasterMessage("whisper", "holo", "まだ先の話");
    const context = f.store.getResidentChatContext(say.message_id, "holo");
    assert.deepEqual(context.messages.map(message => message.content), ["HoloとMasterの話", "この場の話", "覚えているよ"]);
    assert.deepEqual(context.messages.map(message => message.channel), ["whisper", "say", "whisper"]);
    assert.equal(context.messages.some(message => message.id === future.message_id), false);
    assert.equal(context.messages.at(-1)!.id, secretReply.id);
    assert.deepEqual(f.store.getResidentChatContext(say.message_id, "serina").messages.map(message => message.content), ["SerinaとMasterの話", "この場の話"]);
    assert.throws(() => f.store.getResidentChatContext(secret.message_id, "serina"), /did not receive/);
    const task = f.store.createTask("holo");
    f.store.addMasterMessage(task.id, "Taskの指示");
    assert.equal(f.store.getResidentChatContext(say.message_id, "holo").messages.some(message => message.content === "Taskの指示"), false);
    assert.equal(f.store.getResidentChatContext(say.message_id, "holo", 1).messages.length, 1);
  } finally { f.close(); }
});
