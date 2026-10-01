import type { HoloTurnRecord, HubCommandEnvelope, ResidentRecord } from "../shared/types.js";
import type { HubSettings } from "../shared/settings.js";
import { HubError } from "../shared/errors.js";
import { niraiCommandTool, serverInstructions } from "../bridge/metadata.js";
import type { TaskDriver } from "./engine.js";
import type { CodexConversationProvider } from "./codex.js";
import type { HubStore } from "./store.js";
import type { HubService } from "./service.js";
import { readPersona } from "./persona.js";

type CodexTaskTransport = Pick<CodexConversationProvider, "availability" | "run">;

/** Provider generation uses the existing Hub Turn; all actions go through nirai_command. */
export class CodexTaskDriver implements TaskDriver {
  readonly supports_resume = false;
  onChanged: () => void = () => {};
  private closing = false;
  private readonly active = new Map<string, { controller: AbortController; finished: Promise<void> }>();

  constructor(private readonly store: HubStore, private readonly service: HubService,
    private readonly provider: CodexTaskTransport) {}

  availability(): ReturnType<TaskDriver["availability"]> {
    if (this.closing) return { state: "unavailable", reason: "Codex CLIの接続を終了しています。" };
    if (this.active.size) return { state: "busy", reason: "Codex CLIがTaskへ返答しています。" };
    return this.provider.availability();
  }

  start(turn: HoloTurnRecord): void {
    if (this.closing || this.active.size) {
      this.store.endHoloTurn(turn.id, "Codex CLIを現在利用できません。");
      return;
    }
    const task = this.store.getTask(turn.task_id);
    const resident = task && this.store.getResident(task.resident_id);
    if (!resident) { this.store.endHoloTurn(turn.id, "Residentが見つかりません。"); return; }
    // Freeze the selected provider settings before any asynchronous preparation.
    const controller = new AbortController();
    const finished = Promise.resolve().then(() => this.generate(turn, { ...resident }, controller));
    this.active.set(turn.id, { controller, finished });
    void finished.finally(() => { this.active.delete(turn.id); this.onChanged(); });
  }

  private async generate(turn: HoloTurnRecord, resident: ResidentRecord, controller: AbortController): Promise<void> {
    const settings = JSON.parse(turn.settings_json) as HubSettings;
    const timer = setTimeout(() => controller.abort(), settings.holo_turn_timeout_ms);
    try {
      const persona = resident.persona_path ? await readPersona(resident.persona_path) : null;
      controller.signal.throwIfAborted();
      this.store.assertHoloTurn(turn.id);
      const task = this.store.getTask(turn.task_id)!;
      const history = this.store.getTaskTurnMessages(turn.id);
      const prompt = JSON.stringify({ task_id: task.id, turn_id: turn.id, objective: task.objective,
        completion_criteria: task.completion_criteria, history, action_runs: this.store.getTaskTurnRunReferences(turn.id),
        current_input: this.store.getHoloInput(turn.id) });
      const instructions = `${serverInstructions}\n\nあなたはResident ${resident.display_name}です。${resident.role ? `役割: ${resident.role}` : ""}
Taskと実行許可の正本はNirai Hubです。ローカル操作はnirai_commandのInvokeCapabilityだけから要求してください。
このTaskのturn_id=${turn.id}を使い、他のTurnを使わないでください。通常のassistant本文がそのままMasterに保存されます。
Runは非同期です。action_runsは同Taskの直近40件の操作記録です。同じ操作をやり直す前にGetRunResultで結果を確認してください。承認待ちなら無断で迂回せずMasterへ伝えてください。
Codexに自動Resumeはありません。未完了のまま返答を終える場合は、次に必要なMaster操作を説明してください。
${persona ? `Persona:\n${persona.text}` : ""}`;
      if (Buffer.byteLength(prompt) + Buffer.byteLength(instructions) > 256 * 1024) {
        throw new HubError("invalid", "Codex CLIへ渡すTaskの文章が上限を超えています。");
      }
      const content = await this.provider.run({ model: resident.model, prompt, developerInstructions: instructions,
        dynamicTools: [{ type: "function", name: niraiCommandTool.name,
          description: niraiCommandTool.description, inputSchema: niraiCommandTool.inputSchema, deferLoading: false }],
        onToolCall: async (name, args, signal) => {
          try {
            signal.throwIfAborted();
            controller.signal.throwIfAborted();
            if (name !== niraiCommandTool.name || !args || typeof args !== "object" || Array.isArray(args)) {
              throw new HubError("invalid", "Nirai操作の入力が不正です。");
            }
            const input = args as { turn_id?: unknown; envelope?: Record<string, unknown> };
            if (input.turn_id !== turn.id || !input.envelope || typeof input.envelope !== "object" || Array.isArray(input.envelope)) {
              throw new HubError("unauthorized", "このTaskのTurnだけを操作できます。");
            }
            const envelope: HubCommandEnvelope = { protocol_version: 1, target: null, issued_at: new Date().toISOString(),
              command_id: input.envelope.command_id as string, type: input.envelope.type as string,
              payload: input.envelope.payload as Record<string, unknown> };
            const result = this.service.handleTurnCommand(turn.id, envelope);
            this.onChanged();
            return { contentItems: [{ type: "inputText" as const, text: JSON.stringify(result) }], success: true };
          } catch (error) {
            return { contentItems: [{ type: "inputText" as const,
              text: error instanceof HubError ? error.message : "Nirai操作を受け付けられませんでした。" }], success: false };
          }
        },
      }, controller.signal);
      controller.signal.throwIfAborted();
      const current = this.store.getHoloTurn(turn.id);
      const currentTask = this.store.getTask(turn.task_id);
      if (this.closing || !current || current.ended_at || currentTask?.state !== "Running"
        || currentTask.control_epoch !== turn.control_epoch) return;
      if (!content.trim() || Buffer.byteLength(content) > 64 * 1024) throw new HubError("invalid", "Codex CLIの返答を保存できません。");
      this.store.syncHoloTurn(turn.id, content, true);
    } catch (error) {
      const current = this.store.getHoloTurn(turn.id);
      if (current && !current.ended_at) this.store.endHoloTurn(turn.id,
        controller.signal.aborted ? "Codex CLIの生成を中断しました。自動で再送しません。"
          : error instanceof HubError ? error.message : "Codex CLIから返答を取得できませんでした。自動で再送しません。");
    } finally { clearTimeout(timer); }
  }

  reconcile(): void {
    for (const [id, active] of this.active) {
      const turn = this.store.getHoloTurn(id);
      const task = turn && this.store.getTask(turn.task_id);
      if (!turn || turn.ended_at || task?.state !== "Running" || task.control_epoch !== turn.control_epoch) {
        active.controller.abort();
      }
    }
  }

  async close(): Promise<void> {
    this.closing = true;
    for (const active of this.active.values()) active.controller.abort();
    await Promise.allSettled([...this.active.values()].map(active => active.finished));
  }
}
