import type { HoloDispatch, HoloObservation } from "../shared/holo.js";
import type { HoloTurnRecord } from "../shared/types.js";
import { HubStore } from "./store.js";

type ActiveTurn = {
  turn: HoloTurnRecord;
  dispatch: HoloDispatch;
  timer: NodeJS.Timeout;
  conversationId: string | null;
  contextLoaded: boolean;
};

export class HoloConnector {
  private observation: HoloObservation = {
    state: "unavailable",
    reason: "Holoを開いてログインしてください",
    url: null,
    conversation_id: null,
    busy: false,
    draft: false,
  };
  private readonly active = new Map<string, ActiveTurn>();

  send: (message:
    | { type: "holo:dispatch"; dispatch: HoloDispatch }
    | { type: "holo:cancel" | "holo:release"; turn_id: string }
  ) => void = () => { throw new Error("Holo Main transport unavailable"); };

  onChanged: () => void = () => {};

  constructor(private readonly store: HubStore) {}

  availability(): { state: HoloObservation["state"]; reason: string } {
    if (!this.store.getSettings().value.holo_app_name) {
      return { state: "blocked", reason: "Resident設定でv2専用MCP接続名を設定してください" };
    }
    return { state: this.observation.state, reason: this.observation.reason };
  }

  observe(value: HoloObservation): void {
    if (!value || !["ready", "busy", "blocked", "unavailable"].includes(value.state)) {
      throw new Error("invalid Holo observation");
    }
    this.observation = value;
    this.onChanged();
  }

  status(): HoloObservation {
    return { ...this.observation };
  }

  start(turn: HoloTurnRecord): void {
    this.store.assertHoloTurn(turn.id);
    const binding = this.store.prepareHoloConversation(turn.id);
    const settings = JSON.parse(turn.settings_json);
    const app = settings.holo_app_name;
    if (!app) throw new Error("Holo MCP connection name is not configured");

    const prompt = [
      `@${app}`,
      `turn_id=${turn.id}`,
      "Nirai-MCPでGetTaskContextを取得し、その内容に従ってTaskを続行してください。",
    ].join("\n");

    const dispatch: HoloDispatch = {
      turn_id: turn.id,
      task_id: turn.task_id,
      control_epoch: turn.control_epoch,
      target_conversation_id: binding.external_conversation_id,
      prompt,
      settings,
    };

    const timer = setTimeout(() => {
      const current = this.store.getHoloTurn(turn.id);
      if (!current || current.ended_at !== null) return;
      this.store.endHoloTurn(turn.id, "Holo Turn timeout");
      this.send({ type: "holo:cancel", turn_id: turn.id });
      this.reconcile();
      this.onChanged();
    }, settings.holo_turn_timeout_ms);

    this.active.set(turn.id, {
      turn,
      dispatch,
      timer,
      conversationId: binding.external_conversation_id,
      contextLoaded: false,
    });
    this.send({ type: "holo:dispatch", dispatch });
  }

  delivered(turnId: string, result: {
    status: "confirmed" | "not_sent" | "unknown";
    url?: string;
    reason?: string;
    retryable?: boolean;
  }): void {
    const active = this.active.get(turnId);
    if (!active) return;

    if (result.status === "confirmed") {
      if (result.url) {
        const binding = this.store.confirmHoloConversation(
          turnId,
          result.url,
          active.dispatch.target_conversation_id,
        );
        active.conversationId = binding.external_conversation_id;
      }
      return;
    }

    if (result.status === "not_sent" && result.retryable === true) return;

    this.store.endHoloTurn(turnId, result.reason ?? "Holo delivery ended");
    if (result.status === "unknown") this.store.clearConversationBinding(active.turn.task_id);
    this.reconcile();
    this.onChanged();
  }

  contextLoaded(turnId: string): void {
    const active = this.active.get(turnId);
    if (active) active.contextLoaded = true;
  }

  assistant(turnId: string, content: string, url?: string): void {
    const active = this.active.get(turnId);
    if (!active) return;
    if (!active.contextLoaded) {
      this.store.endHoloTurn(turnId, "Task Context was not loaded");
      this.store.clearConversationBinding(active.turn.task_id);
      this.reconcile();
      this.onChanged();
      return;
    }
    if (url) {
      try {
        const binding = this.store.confirmHoloConversation(turnId, url);
        active.conversationId = binding.external_conversation_id;
      } catch {
        // The message remains authoritative for the active Turn even if URL parsing fails.
      }
    }
    this.store.finishHoloTurn(turnId, content);
    this.reconcile();
    this.onChanged();
  }

  failed(turnId: string, reason: string): void {
    const active = this.active.get(turnId);
    if (!active) return;
    this.store.endHoloTurn(turnId, reason);
    this.store.clearConversationBinding(active.turn.task_id);
    this.reconcile();
    this.onChanged();
  }

  stopped(turnId: string): void {
    const active = this.active.get(turnId);
    if (!active) return;
    const current = this.store.getHoloTurn(turnId);
    if (current?.ended_at === null) this.store.endHoloTurn(turnId, "Holo generation stopped");
    this.reconcile();
    this.onChanged();
  }

  reconcile(): void {
    for (const [id, active] of this.active) {
      const turn = this.store.getHoloTurn(id);
      if (turn?.ended_at === null) continue;
      clearTimeout(active.timer);
      if (turn?.end_reason !== "assistant") {
        try { this.send({ type: "holo:cancel", turn_id: id }); } catch {}
      }
      try { this.send({ type: "holo:release", turn_id: id }); } catch {}
      this.active.delete(id);
    }
  }

  close(): void {
    for (const active of this.active.values()) clearTimeout(active.timer);
    this.active.clear();
  }
}
