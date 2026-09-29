import type { HoloDispatch, HoloEnded, HoloObservation } from "../shared/holo.js";
import type { HoloTurnRecord } from "../shared/types.js";
import { HubStore } from "./store.js";

export class HoloConnector {
  private observation: HoloObservation = {
    state: "unavailable",
    reason: "Holoを開いてログインしてください",
    url: null,
    conversation_id: null,
    task_id: null,
    busy: false,
    draft: false,
  };
  private readonly active = new Map<string, NodeJS.Timeout>();

  send: (message:
    | { type: "holo:dispatch"; dispatch: HoloDispatch }
    | { type: "holo:cancel" | "holo:release"; turn_id: string }
  ) => void = () => { throw new Error("Holo Main transport unavailable"); };

  onChanged: () => void = () => {};

  constructor(private readonly store: HubStore) {}

  availability(taskId?: string): { state: HoloObservation["state"]; reason: string } {
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

  observe(value: HoloObservation): void {
    if (!value || !["ready", "busy", "blocked", "unavailable"].includes(value.state)) {
      throw new Error("invalid Holo observation");
    }
    this.observation = value;
    this.onChanged();
  }

  start(turn: HoloTurnRecord): void {
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
    for (const timer of this.active.values()) clearTimeout(timer);
    this.active.clear();
  }
}
