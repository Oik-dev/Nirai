import type { HoloChatDispatch, HoloChatEnded, HoloDispatch, HoloEnded, HoloObservation } from "../shared/holo.js";
import type { HoloTurnRecord } from "../shared/types.js";
import { HubStore, type HoloChatPromptMemory } from "./store.js";
import { HubError } from "../shared/errors.js";
import type { ConversationInput } from "./conversation.js";

interface ActiveChat {
  message_id: string;
  delivered: boolean;
  resolve: (content: string) => void;
  reject: (error: Error) => void;
  removeAbort: () => void;
  promptMemory: Omit<HoloChatPromptMemory, "conversation_id">;
}

function chatPrompt(input: ConversationInput, store: HubStore): {
  prompt: string; memory: Omit<HoloChatPromptMemory, "conversation_id">;
} {
  const name = (id: string) => (id === "master" ? "Master" : store.getResident(id)?.display_name ?? id).replace(/\s+/g, " ");
  const address = (channel: string, sender: string, audience: string[]) => `${channel === "say" ? "Say" : "Whisper"} ${name(sender)} → ${audience.filter(id => id !== sender).map(name).join("、")}`;
  const isNew = store.getHoloChatBinding().external_conversation_id === null;
  const memory = store.getHoloChatPromptMemory();
  const known = new Set(memory?.seen_message_ids ?? []);
  if (!isNew && !memory) {
    // Existing conversations already contain the former prompts. Initialize their
    // checkpoint without echoing that history; only newer other-person speech is missing.
    let lastReply = -1;
    for (let index = input.messages.length - 1; index >= 0; index--) {
      if (input.messages[index]!.sender === "holo") { lastReply = index; break; }
    }
    const lastInput = lastReply >= 0
      ? input.messages.findIndex(message => message.id === input.messages[lastReply]!.reply_to_message_id) : -1;
    input.messages.forEach((message, index) => {
      const completed = message.sender === "master" && store.getChatResponse(message.id, "holo")?.state === "completed";
      const earlierInput = message.sender === "master" && index <= lastInput;
      const earlierReply = message.sender !== "master" && index <= lastReply
        && !input.messages.slice(lastInput + 1).some(item => item.id === message.reply_to_message_id);
      if (message.sender === "holo" || earlierInput || earlierReply || completed) known.add(message.id);
    });
  }
  const history: Array<{ id: string; text: string }> = [];
  let bytes = 0;
  for (const message of [...input.messages].reverse()) {
    if (message.id === input.message.id) continue;
    // Holo's own replies are already in this native conversation.
    if (!isNew && message.sender === "holo") { known.add(message.id); continue; }
    if (known.has(message.id)) continue;
    const text = `${address(message.channel, message.sender, ["master", ...message.audience])}:\n${message.content}`;
    const size = Buffer.byteLength(text);
    if (bytes + size > 64 * 1024) break;
    bytes += size;
    history.unshift({ id: message.id, text });
  }
  const personaFingerprint = input.persona?.fingerprint ?? null;
  const personaChanged = !memory || personaFingerprint !== memory.persona_fingerprint;
  const prompt = [
    `message_id=${input.message.id}`,
    ...(isNew ? ["Sayは公衆、Whisperは個人間。記憶は共通で、内容を再び話すかは自分で判断してください。"] : []),
    ...(personaChanged && input.persona ? [`Persona:\n${input.persona.text}`] : []),
    ...(personaChanged && !input.persona && (memory?.persona_fingerprint || (!isNew && !memory))
      ? ["Personaファイルの指定はありません。ChatGPT側の人物設定を使ってください。"] : []),
    ...(history.length ? [`参考（過去の会話）:\n${history.map(entry => entry.text).join("\n\n")}`] : []),
    address(input.conversation.kind, input.message.sender, input.message.audience),
    "",
    input.message.content,
  ].join("\n");
  for (const entry of history) known.add(entry.id);
  known.add(input.message.id);
  return { prompt, memory: { seen_message_ids: [...known].slice(-100), persona_fingerprint: personaFingerprint } };
}

export class HoloConnector {
  private observation: HoloObservation = {
    surface_mode: "chat",
    state: "unavailable",
    reason: "Holoを開いてログインしてください",
    url: null,
    conversation_id: null,
    task_id: null,
    busy: false,
    draft: false,
  };
  private readonly active = new Map<string, NodeJS.Timeout>();
  private chatActive: ActiveChat | null = null;

  send: (message:
    | { type: "holo:dispatch"; dispatch: HoloDispatch }
    | { type: "holo:cancel" | "holo:release"; turn_id: string }
    | { type: "holo:chat-dispatch"; dispatch: HoloChatDispatch }
    | { type: "holo:chat-cancel" | "holo:chat-release"; message_id: string }
  ) => void = () => { throw new Error("Holo Main transport unavailable"); };

  onChanged: () => void = () => {};

  constructor(private readonly store: HubStore) {}

  availability(taskId?: string): { state: HoloObservation["state"]; reason: string } {
    if (this.chatActive) return { state: "busy", reason: "Holoが通常会話へ返答しています" };
    if (this.observation.surface_mode === "chat") {
      return { state: "blocked", reason: "TaskのHolo Conversationを選択してください" };
    }
    if (!this.store.getSettings().value.holo_app_name) {
      return { state: "blocked", reason: "Resident設定でv2専用MCP接続名を設定してください" };
    }
    if (this.observation.state === "ready" && this.active.size) {
      return { state: "busy", reason: "Holo Turnを処理しています" };
    }
    if (taskId && this.observation.task_id !== null && this.observation.task_id !== taskId) {
      return { state: "blocked", reason: "このTaskのHolo Conversationが選択されていません" };
    }
    return { state: this.observation.state, reason: this.observation.reason };
  }

  chatAvailability(): { state: HoloObservation["state"]; reason: string } {
    if (this.active.size || this.chatActive) return { state: "busy", reason: "Holoが返答しています" };
    return { state: this.observation.state, reason: this.observation.reason };
  }

  observe(value: HoloObservation): void {
    if (!value || !["task", "chat"].includes(value.surface_mode) || !["ready", "busy", "blocked", "unavailable"].includes(value.state)) {
      throw new Error("invalid Holo observation");
    }
    this.observation = value;
    this.onChanged();
  }

  start(turn: HoloTurnRecord): void {
    if (this.chatActive || this.active.size) {
      this.store.endHoloTurn(turn.id, "Holo is busy with another response");
      this.onChanged();
      return;
    }
    try {
      const binding = this.store.prepareHoloConversation(turn.id);
      const settings = JSON.parse(turn.settings_json);
      const dispatch: HoloDispatch = {
        task_id: turn.task_id,
        turn_id: turn.id,
        target_conversation_id: binding.external_conversation_id,
        prompt: [
          `@${settings.holo_app_name}`,
          `turn_id=${turn.id}`,
          this.store.getHoloInput(turn.id),
        ].join("\n"),
        settings,
      };
      const timer = setTimeout(() => {
        this.store.endHoloTurn(turn.id, "Holo Turn timeout");
        this.reconcile();
        this.onChanged();
      }, settings.holo_turn_timeout_ms);
      this.active.set(turn.id, timer);
      this.send({ type: "holo:dispatch", dispatch });
    } catch (error) {
      const reason = `Holo送信を開始できません: ${error instanceof Error ? error.message : String(error)}`;
      this.observation = { ...this.observation, state: "blocked", reason };
      this.store.endHoloTurn(turn.id, reason);
      this.reconcile();
      this.onChanged();
    }
  }

  delivered(turnId: string, url: string): void {
    if (!this.active.has(turnId)) return;
    this.store.confirmHoloConversation(turnId, url);
    this.onChanged();
  }

  sync(turnId: string, content: string, complete: boolean): void {
    if (!this.active.has(turnId)) return;
    this.store.syncHoloTurn(turnId, content, complete);
    if (!complete) return;
    this.reconcile();
    this.onChanged();
  }

  ended({ turn_id, reason, sent }: HoloEnded): void {
    if (!this.active.has(turn_id)) return;
    if (!sent) {
      this.observation = { ...this.observation, state: "blocked", reason };
    }
    this.store.endHoloTurn(turn_id, reason);
    this.reconcile();
    this.onChanged();
  }

  generateChat(input: ConversationInput, signal: AbortSignal): Promise<string> {
    signal.throwIfAborted();
    if (input.resident.id !== "holo" || this.chatAvailability().state !== "ready") {
      throw new HubError("unavailable", "Holoを現在利用できません。");
    }
    const binding = this.store.getHoloChatBinding();
    const prepared = chatPrompt(input, this.store);
    const dispatch: HoloChatDispatch = {
      message_id: input.message.id,
      channel: input.conversation.kind as "say" | "whisper",
      audience: input.message.audience,
      target_conversation_id: binding.external_conversation_id,
      prompt: prepared.prompt,
      settings: this.store.getSettings().value,
    };
    return new Promise<string>((resolve, reject) => {
      const onAbort = () => this.finishChat(input.message.id,
        new HubError("unavailable", "Holoの返答を中断しました。自動で再送しません。"));
      this.chatActive = { message_id: input.message.id, delivered: false, resolve, reject,
        promptMemory: prepared.memory,
        removeAbort: () => signal.removeEventListener("abort", onAbort) };
      signal.addEventListener("abort", onAbort, { once: true });
      try { this.send({ type: "holo:chat-dispatch", dispatch }); }
      catch (error) { this.finishChat(input.message.id, new HubError("unavailable",
        `Holo送信を開始できません: ${error instanceof Error ? error.message : String(error)}`)); }
      this.onChanged();
    });
  }

  chatDelivered(messageId: string, url: string): void {
    const active = this.chatActive;
    if (!active || active.message_id !== messageId) return;
    try {
      this.store.confirmHoloChatConversation(messageId, url, active.promptMemory);
      active.delivered = true;
      this.onChanged();
    } catch (error) {
      this.finishChat(messageId, error instanceof Error ? error : new Error(String(error)));
      throw error;
    }
  }

  chatSync(messageId: string, content: string, complete: boolean): void {
    const active = this.chatActive;
    if (!active || active.message_id !== messageId || !complete) return;
    if (!active.delivered) {
      this.finishChat(messageId, new HubError("invalid", "Holoの送信先を確認できません。自動で再送しません。"));
      return;
    }
    if (typeof content !== "string" || !content.trim() || Buffer.byteLength(content) > 64 * 1024) {
      this.finishChat(messageId, new HubError("invalid", "Holoの返答を保存できません。"));
      return;
    }
    this.finishChat(messageId, null, content);
  }

  chatEnded({ message_id, reason, sent }: HoloChatEnded): void {
    if (this.chatActive?.message_id !== message_id) return;
    if (!sent) this.observation = { ...this.observation, state: "blocked", reason };
    this.finishChat(message_id, new HubError("unavailable", `${reason} 自動で再送しません。`));
  }

  private finishChat(messageId: string, error: Error | null, content?: string): void {
    const active = this.chatActive;
    if (!active || active.message_id !== messageId) return;
    this.chatActive = null;
    active.removeAbort();
    if (error) {
      try { this.send({ type: "holo:chat-cancel", message_id: messageId }); } catch {}
    }
    try { this.send({ type: "holo:chat-release", message_id: messageId }); } catch {}
    if (error) active.reject(error); else active.resolve(content!);
    this.onChanged();
  }

  reconcile(): void {
    for (const [id, timer] of this.active) {
      const turn = this.store.getHoloTurn(id);
      if (turn?.ended_at === null) continue;
      clearTimeout(timer);
      if (turn?.end_reason !== "assistant") {
        try { this.send({ type: "holo:cancel", turn_id: id }); } catch {}
      }
      try { this.send({ type: "holo:release", turn_id: id }); } catch {}
      this.active.delete(id);
    }
  }

  close(): void {
    if (this.chatActive) this.finishChat(this.chatActive.message_id, new HubError("unavailable", "Holoを終了しました。"));
    for (const timer of this.active.values()) clearTimeout(timer);
    this.active.clear();
  }
}
