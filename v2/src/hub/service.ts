import { readFileSync } from "node:fs";
import { fingerprint } from "../shared/stable.js";
import type { CommandResult, CompletionEvidence, HubCommandEnvelope } from "../shared/types.js";
import { HubError } from "../shared/errors.js";
import type { TaskEngine } from "./engine.js";
import { HubStore } from "./store.js";

function stringPayload(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== "string" || value.length === 0) throw new Error(`invalid ${key}`);
  return value;
}

function booleanPayload(payload: Record<string, unknown>, key: string): boolean {
  const value = payload[key];
  if (typeof value !== "boolean") throw new Error(`invalid ${key}`);
  return value;
}

const FUTURE_SKEW_MS = 60 * 1000;

export class HubService {
  private closing = false;
  onTurnContextLoaded: (turnId: string) => void = () => {};

  constructor(
    private readonly store: HubStore,
    private readonly engine?: TaskEngine,
    private readonly worldRulesPath?: string,
  ) {}

  close(): void {
    this.closing = true;
  }

  handleMasterCommand(envelope: HubCommandEnvelope): CommandResult {
    return this.handleCommand(envelope, "master");
  }

  handleTurnCommand(turnId: string, envelope: HubCommandEnvelope): CommandResult {
    return this.handleCommand(envelope, `turn:${turnId}`, turnId);
  }

  private handleCommand(envelope: HubCommandEnvelope, actor: string, turnId?: string): CommandResult {
    if (this.closing) throw new HubError("unavailable", "Hub is shutting down");
    if (!envelope || typeof envelope !== "object" || !envelope.payload
      || typeof envelope.payload !== "object" || Array.isArray(envelope.payload)) {
      throw new HubError("invalid", "invalid command envelope");
    }
    if (Buffer.byteLength(JSON.stringify(envelope)) > this.store.getSettings().value.control_message_bytes) {
      throw new HubError("invalid", "control message is too large");
    }
    if (envelope.protocol_version !== 1) throw new Error("unsupported protocol version");
    if (typeof envelope.command_id !== "string" || !envelope.command_id) throw new Error("missing command_id");
    if (!(envelope.target === null || typeof envelope.target === "string")) throw new Error("invalid target");

    const inputFingerprint = fingerprint({
      type: envelope.type,
      target: envelope.target,
      expected_revision: envelope.expected_revision ?? null,
      payload: envelope.payload,
    });

    const existing = this.store.getCommandReceipt(actor, envelope.command_id);
    if (existing) {
      if (existing.input_fingerprint !== inputFingerprint) throw new Error("command_id conflict");
      return existing.result;
    }

    const issuedAt = Date.parse(envelope.issued_at);
    if (!Number.isFinite(issuedAt)) throw new Error("invalid issued_at");
    const age = Date.now() - issuedAt;
    if (age > this.store.getSettings().value.command_acceptance_ms) throw new Error("command expired");
    if (age < -FUTURE_SKEW_MS) throw new Error("command issued_at is in the future");

    this.validatePayload(envelope, Boolean(turnId));
    if (turnId) {
      if (envelope.target !== null) throw new HubError("invalid", "Holo command target must be null");
    } else {
      const targetKey = envelope.type === "ResolveMasterRequest" ? "request_id" : "task_id";
      const payloadTarget = typeof envelope.payload[targetKey] === "string" ? envelope.payload[targetKey] : null;
      if (envelope.target !== null && payloadTarget !== null && envelope.target !== payloadTarget) {
        throw new Error("command target mismatch");
      }
    }

    const result = this.store.transaction(() => {
      const raced = this.store.getCommandReceipt(actor, envelope.command_id);
      if (raced) {
        if (raced.input_fingerprint !== inputFingerprint) throw new Error("command_id conflict");
        return raced.result;
      }

      if (turnId) this.store.assertHoloTurn(turnId);
      const executed = turnId
        ? this.executeTurnCommand(turnId, envelope)
        : this.executeMasterCommand(envelope);
      this.store.saveCommandReceipt(actor, envelope.command_id, inputFingerprint, executed);
      return this.store.getCommandReceipt(actor, envelope.command_id)!.result;
    });

    this.engine?.schedule();
    return result;
  }

  private validatePayload(envelope: HubCommandEnvelope, turn: boolean): void {
    const master: Record<string, string[]> = {
      CreateTask: ["resident_id"],
      UpdateTaskDefinition: ["task_id", "resident_id", "title", "completion_criteria", "workspace_scope", "objective"],
      SetTaskResume: ["task_id", "enabled"],
      PauseTask: ["task_id"],
      ResumeTask: ["task_id"],
      CancelTask: ["task_id"],
      ResolveMasterRequest: ["request_id", "answer"],
      GetSnapshot: [],
      GetSettings: [],
      UpdateSettings: ["settings"],
      SendConversationMessage: ["task_id", "sender", "content"],
      CompleteTask: ["task_id", "result_summary", "completion"],
    };
    const holo: Record<string, string[]> = {
      GetTaskContext: [],
      GetRunResult: ["run_id", "max_bytes"],
      InvokeCapability: ["capability_id", "operation", "input"],
      RequestMasterInput: ["prompt"],
      CompleteTask: ["result_summary", "completion"],
    };

    const permitted = turn ? holo : master;
    const fields = Object.hasOwn(permitted, envelope.type) ? permitted[envelope.type] : undefined;
    if (!fields) throw new HubError("unauthorized", `unsupported ${turn ? "Holo" : "Master"} command: ${envelope.type}`);

    for (const key of Object.keys(envelope.payload)) {
      if (!fields.includes(key)) throw new HubError("invalid", `invalid payload field: ${key}`);
    }

    for (const [key, value] of Object.entries(envelope.payload)) {
      if (["completion", "completion_criteria"].includes(key)) {
        if (!Array.isArray(value) || value.some(item => !item || typeof item !== "object" || Array.isArray(item))) {
          throw new HubError("invalid", `invalid ${key}`);
        }
      } else if (["settings", "answer", "input"].includes(key)) {
        if (!value || typeof value !== "object" || Array.isArray(value)) throw new HubError("invalid", `invalid ${key}`);
      } else if (key === "max_bytes") {
        if (!Number.isSafeInteger(value) || Number(value) < 1024 || Number(value) > 256 * 1024) {
          throw new HubError("invalid", "invalid max_bytes");
        }
      } else if (key === "enabled") {
        if (typeof value !== "boolean") throw new HubError("invalid", "invalid enabled");
      } else if (!(key === "workspace_scope" && value === null) && typeof value !== "string") {
        throw new HubError("invalid", `invalid ${key}`);
      }
    }
  }

  private executeTurnCommand(turnId: string, envelope: HubCommandEnvelope): CommandResult {
    const payload = envelope.payload;
    const turn = this.store.assertHoloTurn(turnId);

    switch (envelope.type) {
      case "GetTaskContext": {
        const context = {
          world_rules: this.worldRulesPath ? readFileSync(this.worldRulesPath, "utf8").trim() : "",
          capabilities: this.engine?.registry.list() ?? [],
          ...this.store.getTaskContext(turn.id),
        };
        this.onTurnContextLoaded(turn.id);
        return context;
      }

      case "GetRunResult":
        return this.store.getRunResultForTurn(
          turn.id,
          stringPayload(payload, "run_id"),
          payload.max_bytes === undefined ? undefined : Number(payload.max_bytes),
        );

      case "InvokeCapability": {
        if (!this.engine) throw new HubError("unavailable", "Capability Engine unavailable");
        const child = this.engine.createRun({
          task_id: turn.task_id,
          turn_id: turn.id,
          control_epoch: turn.control_epoch,
          capability_id: stringPayload(payload, "capability_id"),
          operation: stringPayload(payload, "operation"),
          input: payload.input,
        });
        return { accepted: true, run_id: child.id };
      }

      case "RequestMasterInput":
        return {
          request_id: this.store.createMasterRequest({
            task_id: turn.task_id,
            turn_id: turn.id,
            kind: "input",
            prompt: stringPayload(payload, "prompt"),
          }).id,
        };

      case "CompleteTask": {
        const task = this.store.completeTask(
          turn.task_id,
          stringPayload(payload, "result_summary"),
          payload.completion as CompletionEvidence[] | undefined,
          turn.id,
        );
        return { task_id: task.id, task };
      }

      default:
        throw new HubError("unauthorized", "unsupported Holo command");
    }
  }

  private executeMasterCommand(envelope: HubCommandEnvelope): CommandResult {
    const payload = envelope.payload;

    switch (envelope.type) {
      case "CreateTask": {
        const residentId = stringPayload(payload, "resident_id");
        const task = this.store.createTask(residentId);
        return { task_id: task.id, conversation_id: task.conversation_id, task };
      }

      case "SendConversationMessage": {
        const taskId = stringPayload(payload, "task_id");
        const sender = stringPayload(payload, "sender");
        if (sender !== "master") throw new Error("Master command cannot impersonate another sender");
        const content = stringPayload(payload, "content");
        this.checkRevision(taskId, this.requireRevision(envelope));
        const result = this.store.addMasterMessage(taskId, content);
        return { task_id: taskId, message_id: result.message_id, seq: result.seq, task: result.task };
      }

      case "UpdateTaskDefinition": {
        const taskId = stringPayload(payload, "task_id");
        this.checkRevision(taskId, this.requireRevision(envelope));
        const task = this.store.updateTaskDefinition(taskId, payload);
        return { task_id: taskId, task };
      }

      case "SetTaskResume": {
        const taskId = stringPayload(payload, "task_id");
        this.checkRevision(taskId, this.requireRevision(envelope));
        const task = this.store.setTaskResume(taskId, booleanPayload(payload, "enabled"));
        return { task_id: taskId, task };
      }

      case "PauseTask": {
        const taskId = stringPayload(payload, "task_id");
        const task = this.store.pauseTask(taskId);
        return { task_id: taskId, task };
      }

      case "ResumeTask": {
        const taskId = stringPayload(payload, "task_id");
        this.checkRevision(taskId, this.requireRevision(envelope));
        const task = this.store.resumeTask(taskId);
        return { task_id: taskId, task };
      }

      case "CancelTask": {
        const taskId = stringPayload(payload, "task_id");
        const task = this.store.cancelTask(taskId);
        return { task_id: taskId, task };
      }

      case "ResolveMasterRequest": {
        const requestId = stringPayload(payload, "request_id");
        if (!Object.prototype.hasOwnProperty.call(payload, "answer")) throw new Error("missing answer");
        const request = this.store.resolveMasterRequest(requestId, payload.answer, this.requireRevision(envelope));
        return { request_id: requestId, request };
      }

      case "CompleteTask": {
        const taskId = stringPayload(payload, "task_id");
        this.checkRevision(taskId, this.requireRevision(envelope));
        const task = this.store.confirmTaskCompletion(
          taskId,
          stringPayload(payload, "result_summary"),
          payload.completion as CompletionEvidence[] | undefined,
        );
        return { task_id: taskId, task };
      }

      case "GetSnapshot":
        return this.store.snapshot();

      case "GetSettings":
        return this.store.getSettings();

      case "UpdateSettings":
        return this.store.updateSettings(
          payload.settings as Record<string, unknown>,
          this.requireRevision(envelope),
        );

      default:
        throw new Error(`unsupported Master command: ${envelope.type}`);
    }
  }

  getMasterCommandReceipt(commandId: string): CommandResult | null {
    return this.store.getCommandReceipt("master", commandId)?.result ?? null;
  }

  private requireRevision(envelope: HubCommandEnvelope): number {
    if (!Number.isSafeInteger(envelope.expected_revision) || envelope.expected_revision! < 1) {
      throw new Error("expected_revision is required");
    }
    return envelope.expected_revision as number;
  }

  private checkRevision(taskId: string, expected: number): void {
    const task = this.store.getTask(taskId);
    if (!task) throw new Error(`task not found: ${taskId}`);
    if (task.revision !== expected) {
      throw new HubError("stale", `stale revision: expected ${expected}, current ${task.revision}`, task);
    }
  }
}
