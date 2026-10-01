import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { HubRuntime } from "../src/hub/runtime.js";
import { HubStore } from "../src/hub/store.js";
import type { HoloChatDispatch, HoloDispatch, HoloObservation } from "../src/shared/holo.js";
import type { HubCommandEnvelope } from "../src/shared/types.js";

const ready: HoloObservation = { surface_mode: "chat", state: "ready", reason: "fixture", url: "https://chatgpt.com/",
  conversation_id: null, task_id: null, busy: false, draft: false };

async function until(predicate: () => unknown): Promise<void> {
  for (let i = 0; i < 300; i++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error("Holo Chat state did not settle");
}

async function fixture() {
  const root = mkdtempSync(join(tmpdir(), "nirai-holo-chat-"));
  const runtime = await HubRuntime.start(root);
  const chats: HoloChatDispatch[] = [];
  const tasks: HoloDispatch[] = [];
  const released: string[] = [];
  runtime.holo.send = message => {
    if (message.type === "holo:chat-dispatch") chats.push(message.dispatch);
    else if (message.type === "holo:dispatch") tasks.push(message.dispatch);
    else if (message.type === "holo:chat-release") released.push(message.message_id);
  };
  runtime.holo.observe(ready);
  const send = (type: string, payload: Record<string, unknown>, commandId = randomUUID()) => {
    const command: HubCommandEnvelope = { protocol_version: 1, command_id: commandId,
      issued_at: new Date().toISOString(), target: null, type, payload };
    return runtime.service.handleMasterCommand(command);
  };
  return { root, runtime, chats, tasks, released, send,
    async close() { await runtime.close(); rmSync(root, { recursive: true, force: true }); } };
}

test("Holo normally speaks through one shared Say/Whisper conversation without a Task or MCP grant", async () => {
  const f = await fixture();
  try {
    assert.ok(f.runtime.conversation.providers.list().some(provider => provider.id === "holo"));
    assert.equal(f.runtime.store.getSettings().value.holo_app_name, null);
    const persona = join(f.root, "persona.md");
    writeFileSync(persona, "相手との信頼を大切にして、自分で話す内容を判断する。");
    f.send("UpdateResident", { resident_id: "holo", persona_path: persona });
    f.runtime.conversation.providers.register({ id: "test-serina", availability: () => ({ state: "ready" }),
      async generate(input) { return input.conversation.kind === "say" ? "SerinaがSayで言ったこと" : "Serinaの内密の返答"; } });
    f.send("CreateResident", { id: "serina", display_name: "Serina", capability_id: "test-serina" });
    f.send("SendChatMessage", { channel: "whisper", resident_id: "serina", content: "別の当事者だけの話" });
    await f.runtime.conversation.idle();
    const privateContent = "  Masterの原文\n秘密の相談  ";
    const privateInput = f.send("SendChatMessage", { channel: "whisper", resident_id: "holo", content: privateContent });
    await until(() => f.chats.length === 1);
    const first = f.chats[0]!;
    assert.equal(first.channel, "whisper");
    assert.deepEqual(first.audience, ["holo"]);
    assert.equal(first.prompt.split("\n")[0], `message_id=${privateInput.message_id}`);
    assert.ok(first.prompt.endsWith(privateContent));
    assert.ok(first.prompt.includes("Persona:"));
    assert.ok(first.prompt.includes("Whisper Master → Holo"));
    assert.equal(first.prompt.includes("別の当事者だけの話"), false);
    assert.equal(first.prompt.includes("turn_id="), false);
    assert.equal(first.prompt.includes("@nirai"), false);
    assert.equal(first.target_conversation_id, null);
    f.runtime.holo.chatSync(first.message_id, "途中", false);
    assert.equal(f.runtime.store.getChatResponse(first.message_id, "holo")?.state, "running");
    f.runtime.holo.chatDelivered(first.message_id, "https://chatgpt.com/c/holo-normal");
    const privateReply = "  Holoの返答原文\n相談を覚えている。  ";
    f.runtime.holo.chatSync(first.message_id, privateReply, true);
    await f.runtime.conversation.idle();
    const stored = f.runtime.store.getChatContext(String(privateInput.conversation_id), "holo").messages;
    assert.deepEqual(stored.map(message => message.content), [privateContent, privateReply]);
    assert.equal(stored[1]!.reply_to_message_id, first.message_id);
    assert.ok(f.released.includes(first.message_id));
    f.runtime.holo.chatSync(first.message_id, "遅い別回答", true);
    assert.equal(f.runtime.store.getChatContext(String(privateInput.conversation_id), "holo").messages.length, 2);

    const publicInput = f.send("SendChatMessage", { channel: "say", content: "みんなと話す" });
    await until(() => f.chats.length === 2);
    const second = f.chats[1]!;
    assert.equal(second.channel, "say");
    assert.deepEqual(second.audience, ["holo", "serina"]);
    assert.equal(second.target_conversation_id, "holo-normal");
    assert.equal(second.prompt.includes("秘密の相談"), false, "the native conversation already has this input");
    assert.equal(second.prompt.includes("相談を覚えている"), false, "Holo's native reply is not pasted back");
    assert.equal(second.prompt.includes("Persona:"), false, "unchanged Persona is not sent again");
    assert.equal(second.prompt.includes("Task・Run"), false);
    assert.equal(second.prompt.includes("参考"), false);
    assert.ok(second.prompt.includes("Say Master → Holo、Serina"));
    assert.equal(second.prompt.includes("別の当事者だけの話"), false);
    f.runtime.holo.chatDelivered(second.message_id, "https://chatgpt.com/c/holo-normal");
    f.runtime.holo.chatSync(second.message_id, "本人の判断で先ほどの相談にも触れる。", true);
    await f.runtime.conversation.idle();
    const publicMessages = f.runtime.store.getChatContext(String(publicInput.conversation_id), "serina").messages;
    assert.deepEqual(publicMessages.map(message => message.content), ["みんなと話す", "本人の判断で先ほどの相談にも触れる。", "SerinaがSayで言ったこと"]);
    f.send("SendChatMessage", { channel: "whisper", resident_id: "holo", content: "続けて" });
    await until(() => f.chats.length === 3);
    const third = f.chats[2]!;
    assert.ok(third.prompt.includes("参考（過去の会話）"));
    assert.ok(third.prompt.includes("SerinaがSayで言ったこと"));
    assert.equal(third.prompt.includes("Serinaの内密の返答"), false);
    assert.equal(third.prompt.includes("本人の判断で先ほどの相談にも触れる"), false);
    f.runtime.holo.chatDelivered(third.message_id, "https://chatgpt.com/c/holo-normal");
    f.runtime.holo.chatSync(third.message_id, "続ける返答", true);
    await f.runtime.conversation.idle();
    f.send("SendChatMessage", { channel: "whisper", resident_id: "holo", content: "次の話" });
    await until(() => f.chats.length === 4);
    const fourth = f.chats[3]!;
    assert.equal(fourth.prompt.includes("SerinaがSayで言ったこと"), false, "other-person speech is supplied only once");
    assert.equal(fourth.prompt.split("\n").length, 4, "ordinary input contains only its marker, address and raw text");
    f.runtime.holo.chatDelivered(fourth.message_id, "https://chatgpt.com/c/holo-normal");
    f.runtime.holo.chatSync(fourth.message_id, "次の返答", true);
    await f.runtime.conversation.idle();
    assert.deepEqual(f.runtime.store.listTasks(), []);
    assert.deepEqual(f.runtime.store.listRuns(), []);
    assert.deepEqual(f.runtime.store.listHoloTurns(), []);
  } finally { await f.close(); }
});

test("Holo Task and ordinary speech reserve the same surface in both directions", async () => {
  const f = await fixture();
  try {
    f.runtime.store.updateSettings({ holo_app_name: "nirai-v2" }, 1);
    f.send("SendChatMessage", { channel: "whisper", resident_id: "holo", content: "通常会話" });
    await until(() => f.chats.length === 1);
    assert.equal(f.runtime.holo.availability().state, "busy");
    const task = f.runtime.store.createTask("holo");
    f.runtime.store.addMasterMessage(task.id, "Taskの指示");
    f.runtime.engine.schedule();
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(f.tasks.length, 0);
    f.runtime.holo.chatDelivered(f.chats[0]!.message_id, "https://chatgpt.com/c/holo-normal");
    f.runtime.holo.chatSync(f.chats[0]!.message_id, "通常会話の返答", true);
    await f.runtime.conversation.idle();
    assert.equal(f.tasks.length, 0, "normal ChatMode never reserves a queued Task");
    f.runtime.holo.observe({ ...ready, surface_mode: "task", task_id: task.id });
    await until(() => f.tasks.length === 1);
    assert.equal(f.runtime.holo.chatAvailability().state, "busy");
    const unavailable = f.send("SendChatMessage", { channel: "whisper", resident_id: "holo", content: "Task処理中の会話" });
    await f.runtime.conversation.idle();
    assert.equal(f.runtime.store.getChatResponse(String(unavailable.message_id), "holo")?.state, "failed");
    assert.equal(f.chats.length, 1);
    const taskDispatch = f.tasks[0]!;
    f.runtime.holo.delivered(taskDispatch.turn_id, "https://chatgpt.com/c/holo-task");
    f.runtime.holo.sync(taskDispatch.turn_id, "Taskの返答", true);
  } finally { await f.close(); }
});

test("Holo normal binding survives reopening and cannot be rebound into Task routing", async () => {
  const f = await fixture();
  let reopened: HubStore | null = null;
  try {
    f.send("SendChatMessage", { channel: "say", content: "記録" });
    await until(() => f.chats.length === 1);
    const dispatch = f.chats[0]!;
    f.runtime.holo.chatDelivered(dispatch.message_id, "https://chatgpt.com/c/holo-normal");
    f.runtime.holo.chatSync(dispatch.message_id, "返答", true);
    await f.runtime.conversation.idle();
    const task = f.runtime.store.createTask("holo");
    assert.throws(() => f.runtime.store.bindConversation(task.id, "chatgpt", "holo-normal"), /cannot be used for a Task/);
    await f.runtime.close();
    reopened = new HubStore(join(f.root, "hub.sqlite3"));
    assert.deepEqual(reopened.getHoloChatBinding(), { external_conversation_id: "holo-normal", external_url: "https://chatgpt.com/c/holo-normal" });
    assert.equal(reopened.getChatResponse(dispatch.message_id, "holo")?.state, "completed");
  } finally { reopened?.close(); await f.close(); }
});

test("closing interrupts Holo speech and its queue without replaying or accepting late reports", async () => {
  const f = await fixture();
  try {
    const first = f.send("SendChatMessage", { channel: "say", content: "生成中" });
    await until(() => f.chats.length === 1);
    const second = f.send("SendChatMessage", { channel: "whisper", resident_id: "holo", content: "待機中" });
    await f.runtime.close();
    assert.ok(f.released.includes(String(first.message_id)));
    // The Connector drops stale reports before touching a closed Store.
    f.runtime.holo.chatDelivered(String(first.message_id), "https://chatgpt.com/c/late");
    f.runtime.holo.chatSync(String(first.message_id), "遅れて返答", true);
    const reopened = new HubStore(join(f.root, "hub.sqlite3"));
    try {
      assert.equal(reopened.getChatResponse(String(first.message_id), "holo")?.state, "interrupted");
      assert.equal(reopened.getChatResponse(String(second.message_id), "holo")?.state, "interrupted");
      assert.equal(reopened.claimNextChatResponse(), null);
      assert.equal((reopened.snapshot().messages as unknown[]).length, 2);
    } finally { reopened.close(); }
  } finally { await f.close(); }
});

test("Persona is synchronized only on file changes or removal, and an unsent prompt does not advance memory", async () => {
  const f = await fixture();
  try {
    const persona = join(f.root, "persona.md");
    writeFileSync(persona, "最初の人物設定");
    f.send("UpdateResident", { resident_id: "holo", persona_path: persona });
    async function input(content: string) {
      const count = f.chats.length + 1;
      f.send("SendChatMessage", { channel: "whisper", resident_id: "holo", content });
      await until(() => f.chats.length === count);
      return f.chats.at(-1)!;
    }
    async function answer(dispatch: HoloChatDispatch) {
      f.runtime.holo.chatDelivered(dispatch.message_id, "https://chatgpt.com/c/holo-normal");
      f.runtime.holo.chatSync(dispatch.message_id, "返答", true);
      await f.runtime.conversation.idle();
    }
    const unsent = await input("不達の入力");
    assert.ok(unsent.prompt.includes("最初の人物設定"));
    assert.equal(f.runtime.store.getHoloChatPromptMemory(), null);
    f.runtime.holo.chatEnded({ message_id: unsent.message_id, reason: "送信前に接続できません", sent: false });
    await f.runtime.conversation.idle();
    assert.equal(f.runtime.store.getHoloChatPromptMemory(), null);
    f.runtime.holo.observe(ready);
    writeFileSync(persona, "読み直した人物設定");
    const firstDelivered = await input("次の入力");
    assert.ok(firstDelivered.prompt.includes("読み直した人物設定"));
    assert.equal(firstDelivered.prompt.includes("最初の人物設定"), false);
    assert.ok(firstDelivered.prompt.includes("参考（過去の会話）"));
    assert.ok(firstDelivered.prompt.includes("不達の入力"));
    assert.ok(firstDelivered.prompt.endsWith("次の入力"));
    await answer(firstDelivered);
    const unchanged = await input("同じ設定で話す");
    assert.equal(unchanged.prompt.includes("Persona:"), false);
    assert.equal(unchanged.prompt.includes("不達の入力"), false);
    await answer(unchanged);
    writeFileSync(persona, "変更後の人物設定");
    const changed = await input("変更を読む");
    assert.ok(changed.prompt.includes("変更後の人物設定"));
    await answer(changed);
    f.send("UpdateResident", { resident_id: "holo", persona_path: null });
    const removed = await input("指定を外した");
    assert.ok(removed.prompt.includes("Personaファイルの指定はありません"));
    await answer(removed);
    const afterRemoval = await input("指定なしを続ける");
    assert.equal(afterRemoval.prompt.includes("Personaファイル"), false);
    await answer(afterRemoval);
    assert.equal(f.runtime.store.getHoloChatPromptMemory()?.persona_fingerprint, null);
  } finally { await f.close(); }
});

test("existing native history is not echoed during upgrade or restart while missing speech stays reference data", async () => {
  const f = await fixture();
  let reopened: HubRuntime | null = null;
  try {
    const store = f.runtime.store;
    store.ensureResident("serina", "Serina");
    const older = store.addChatMasterMessage("whisper", "holo", "旧会話で送った話");
    store.claimNextChatResponse();
    // The legacy Adapter saved its binding without any prompt checkpoint.
    store.confirmHoloChatConversation(older.message_id, "https://chatgpt.com/c/holo-normal");
    const queued = store.addChatMasterMessage("whisper", "holo", "旧生成中に待っていた話");
    store.completeChatResponse(older.message_id, "holo", "旧会話の返答");
    const queuedResponse = store.claimNextChatResponse()!;
    store.failChatResponse(queuedResponse.message_id, "holo", "未送信");
    const pastSay = store.addChatMasterMessage("say", undefined, "未送信だった公の話");
    const missedHolo = store.claimNextChatResponse()!;
    store.failChatResponse(missedHolo.message_id, "holo", "未送信");
    const serina = store.claimNextChatResponse()!;
    store.completeChatResponse(serina.message_id, "serina", "後から届いたSerinaの話");
    assert.equal(store.getHoloChatPromptMemory(), null);
    f.send("SendChatMessage", { channel: "whisper", resident_id: "holo", content: "短い方式へ切り替え" });
    await until(() => f.chats.length === 1);
    const migrated = f.chats[0]!;
    assert.equal(migrated.target_conversation_id, "holo-normal");
    assert.equal(migrated.prompt.includes("旧会話で送った話"), false);
    assert.equal(migrated.prompt.includes("旧会話の返答"), false);
    assert.ok(migrated.prompt.includes("参考（過去の会話）"));
    assert.ok(migrated.prompt.includes("旧生成中に待っていた話"));
    assert.ok(migrated.prompt.includes("未送信だった公の話"));
    assert.ok(migrated.prompt.includes("後から届いたSerinaの話"));
    assert.ok(migrated.prompt.includes("Personaファイルの指定はありません"));
    assert.ok(migrated.prompt.endsWith("短い方式へ切り替え"));
    f.runtime.holo.chatDelivered(migrated.message_id, "https://chatgpt.com/c/holo-normal");
    f.runtime.holo.chatSync(migrated.message_id, "短縮後の返答", true);
    await f.runtime.conversation.idle();
    const checkpoint = store.getHoloChatPromptMemory()!;
    assert.ok(checkpoint.seen_message_ids.includes(queued.message_id));
    assert.ok(checkpoint.seen_message_ids.includes(pastSay.message_id));
    assert.equal(checkpoint.conversation_id, "holo-normal");
    await f.runtime.close();

    reopened = await HubRuntime.start(f.root);
    const sends: HoloChatDispatch[] = [];
    reopened.holo.send = message => { if (message.type === "holo:chat-dispatch") sends.push(message.dispatch); };
    reopened.holo.observe(ready);
    reopened.service.handleMasterCommand({ protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(),
      target: null, type: "SendChatMessage", payload: { channel: "whisper", resident_id: "holo", content: "再起動しても続ける" } });
    await until(() => sends.length === 1);
    assert.equal(sends[0]!.target_conversation_id, "holo-normal");
    assert.equal(sends[0]!.prompt.includes("参考"), false);
    assert.equal(sends[0]!.prompt.includes("Persona"), false);
    assert.equal(sends[0]!.prompt.includes("旧"), false);
    assert.ok(sends[0]!.prompt.endsWith("再起動しても続ける"));
    reopened.holo.chatDelivered(sends[0]!.message_id, "https://chatgpt.com/c/holo-normal");
    reopened.holo.chatSync(sends[0]!.message_id, "続く返答", true);
    await reopened.conversation.idle();
    assert.ok(reopened.store.getHoloChatPromptMemory()!.seen_message_ids.length <= 100);
  } finally { await reopened?.close(); await f.close(); }
});

test("a history byte limit advances only actually delivered speech and a wrong conversation cannot advance it", async () => {
  const f = await fixture();
  try {
    const store = f.runtime.store;
    store.ensureResident("serina", "Serina");
    function priorSay(content: string, reply: string) {
      store.addChatMasterMessage("say", undefined, content);
      const holo = store.claimNextChatResponse()!;
      store.failChatResponse(holo.message_id, "holo", "未送信");
      const serina = store.claimNextChatResponse()!;
      return store.completeChatResponse(serina.message_id, "serina", reply);
    }
    const earlier = priorSay("以前の公の入力", `EARLIER:${"a".repeat(32 * 1024)}`);
    const later = priorSay("後の公の入力", `LATER:${"b".repeat(32 * 1024)}`);
    f.send("SendChatMessage", { channel: "whisper", resident_id: "holo", content: "今の話" });
    await until(() => f.chats.length === 1);
    const first = f.chats[0]!;
    assert.ok(first.prompt.includes("LATER:"));
    assert.equal(first.prompt.includes("EARLIER:"), false);
    f.runtime.holo.chatDelivered(first.message_id, "https://chatgpt.com/c/holo-normal");
    f.runtime.holo.chatSync(first.message_id, "今の返答", true);
    await f.runtime.conversation.idle();
    const firstMemory = store.getHoloChatPromptMemory()!;
    assert.ok(firstMemory.seen_message_ids.includes(later.id));
    assert.equal(firstMemory.seen_message_ids.includes(earlier.id), false);

    f.send("SendChatMessage", { channel: "whisper", resident_id: "holo", content: "続きの話" });
    await until(() => f.chats.length === 2);
    const second = f.chats[1]!;
    assert.ok(second.prompt.includes("EARLIER:"), "history excluded by the previous byte limit remains eligible");
    assert.equal(second.prompt.includes("LATER:"), false);
    assert.throws(() => f.runtime.holo.chatDelivered(second.message_id, "https://chatgpt.com/c/wrong-conversation"), /changed during delivery/);
    await f.runtime.conversation.idle();
    assert.deepEqual(store.getHoloChatPromptMemory(), firstMemory);
    f.runtime.holo.chatSync(second.message_id, "遅れた返答", true);
    assert.deepEqual(store.getHoloChatPromptMemory(), firstMemory);
    assert.equal(store.getChatResponse(second.message_id, "holo")?.state, "failed");
  } finally { await f.close(); }
});
