import type { ResidentChatContext, ChatMessageRecord, ChatResponseRecord } from "../shared/types.js";
import { HubError } from "../shared/errors.js";
import type { HubStore } from "./store.js";
import { readPersona } from "./persona.js";

export interface ConversationInput extends ResidentChatContext {
  message: ChatMessageRecord;
  persona: Awaited<ReturnType<typeof readPersona>> | null;
}

/** Conversational generation receives no Task, Run, Tool, or execution authority. */
export interface ConversationProvider {
  readonly id: string;
  availability(): { state: "ready" | "busy" | "blocked" | "unavailable"; reason?: string };
  generate(input: ConversationInput, signal: AbortSignal): Promise<string>;
}

export class ConversationProviders {
  private readonly providers = new Map<string, ConversationProvider>();

  register(provider: ConversationProvider): void {
    if (!provider.id || this.providers.has(provider.id)) {
      throw new Error("invalid or duplicate conversation provider");
    }
    this.providers.set(provider.id, provider);
  }

  get(id: string | null): ConversationProvider {
    const provider = id ? this.providers.get(id) : undefined;
    if (!provider) throw new HubError("unavailable", "会話用AIが接続されていません。");
    const availability = provider.availability();
    if (availability.state !== "ready") {
      throw new HubError("unavailable", `会話用AIを現在利用できません。${availability.reason ? ` ${availability.reason}` : ""}`);
    }
    return provider;
  }

  list(): Array<{ id: string; availability: ReturnType<ConversationProvider["availability"]> }> {
    return [...this.providers.values()].map(provider => {
      try { return { id: provider.id, availability: provider.availability() }; }
      catch { return { id: provider.id, availability: { state: "unavailable" as const } }; }
    });
  }
}

/** Persist before generation; a failed or interrupted request is never automatically resent. */
export class ConversationRuntime {
  onChanged: () => void = () => {};
  private running: Promise<void> | null = null;
  private closing = false;
  private controller: AbortController | null = null;
  private requested = false;

  constructor(private readonly store: HubStore, readonly providers = new ConversationProviders()) {}

  schedule(): void {
    if (this.closing) return;
    if (this.running) { this.requested = true; return; }
    this.requested = false;
    this.running = this.drain().finally(() => {
      this.running = null;
      if (this.requested) this.schedule();
    });
  }

  async idle(): Promise<void> { await this.running; }

  private async drain(): Promise<void> {
    while (!this.closing) {
      const response = this.store.claimNextChatResponse();
      if (!response) return;
      this.onChanged();
      await this.respond(response);
    }
  }

  private async respond(response: ChatResponseRecord): Promise<void> {
    const controller = new AbortController();
    this.controller = controller;
    let abortListener: () => void = () => {};
    const aborted = new Promise<never>((_resolve, reject) => {
      abortListener = () => reject(new HubError("unavailable", "返答を中断しました。自動で再送はしません。"));
      controller.signal.addEventListener("abort", abortListener, { once: true });
    });
    void aborted.catch(() => {});
    const timer = setTimeout(() => controller.abort(), this.store.getSettings().value.conversation_timeout_ms);
    try {
      const message = this.store.getChatMessage(response.message_id);
      if (!message) throw new HubError("invalid", "会話の入力が見つかりません。");
      const context = this.store.getResidentChatContext(message.id, response.resident_id, 40);
      const provider = this.providers.get(context.resident.id === "holo" ? "holo" : context.resident.capability_id);
      const persona = context.resident.persona_path ? await readPersona(context.resident.persona_path) : null;
      controller.signal.throwIfAborted();
      if (this.closing) throw new HubError("unavailable", "返答を中断しました。");
      const content = await Promise.race([provider.generate({ ...context, message, persona }, controller.signal), aborted]);
      controller.signal.throwIfAborted();
      if (this.closing) return;
      if (typeof content !== "string" || !content.trim() || Buffer.byteLength(content) > 64 * 1024) {
        throw new HubError("invalid", "会話用AIの返答を保存できません。");
      }
      this.store.completeChatResponse(response.message_id, response.resident_id, content);
    } catch (error) {
      if (!this.closing) this.store.failChatResponse(response.message_id, response.resident_id,
        error instanceof HubError ? error.message : "会話用AIから返答を取得できませんでした。");
    } finally {
      clearTimeout(timer);
      controller.signal.removeEventListener("abort", abortListener);
      this.controller = null;
      this.onChanged();
    }
  }

  async close(): Promise<void> {
    this.closing = true;
    this.controller?.abort();
    await this.running;
    this.store.recoverChatResponses();
  }
}
