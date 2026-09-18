import { fingerprint } from "../shared/stable.js";
import type { CommandResult, HubCommandEnvelope } from "../shared/types.js";
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

export class HubService {
  constructor(private readonly store: HubStore) {}

  handleMasterCommand(envelope: HubCommandEnvelope): CommandResult {
    if (envelope.protocol_version !== 1) throw new Error("unsupported protocol version");
    if (!envelope.command_id) throw new Error("missing command_id");

    const actor = "master";
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

    return this.store.transaction(() => {
      const raced = this.store.getCommandReceipt(actor, envelope.command_id);
      if (raced) {
        if (raced.input_fingerprint !== inputFingerprint) throw new Error("command_id conflict");
        return raced.result;
      }

      const result = this.executeMasterCommand(envelope);
      this.store.saveCommandReceipt(actor, envelope.command_id, inputFingerprint, result);
      return result;
    });
  }

  private executeMasterCommand(envelope: HubCommandEnvelope): CommandResult {
    const payload = envelope.payload;

    switch (envelope.type) {
      case "CreateTask": {
        const residentId = stringPayload(payload, "resident_id");
        this.store.ensureResident(residentId, residentId === "holo" ? "Holo" : residentId);
        const task = this.store.createTask(residentId);
        return { task_id: task.id, conversation_id: task.conversation_id, task };
      }

      case "SendConversationMessage": {
        const taskId = stringPayload(payload, "task_id");
        const sender = stringPayload(payload, "sender");
        if (sender !== "master") throw new Error("Master command cannot impersonate another sender");
        const content = stringPayload(payload, "content");
        this.checkRevision(taskId, envelope.expected_revision);
        const result = this.store.addMasterMessage(taskId, content);
        return { task_id: taskId, message_id: result.message_id, seq: result.seq, task: result.task };
      }

      case "UpdateTaskDefinition": {
        const taskId = stringPayload(payload, "task_id");
        this.checkRevision(taskId, envelope.expected_revision);
        const residentId = stringPayload(payload, "resident_id");
        const task = this.store.updateDraftResident(taskId, residentId);
        return { task_id: taskId, task };
      }

      case "SetTaskResume": {
        const taskId = stringPayload(payload, "task_id");
        this.checkRevision(taskId, envelope.expected_revision);
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
        this.checkRevision(taskId, envelope.expected_revision);
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
        const request = this.store.resolveMasterRequest(requestId, payload.answer);
        return { request_id: requestId, request };
      }

      case "CompleteTask": {
        const taskId = stringPayload(payload, "task_id");
        this.checkRevision(taskId, envelope.expected_revision);
        const summary = stringPayload(payload, "result_summary");
        const task = this.store.completeTask(taskId, summary);
        return { task_id: taskId, task };
      }

      case "GetSnapshot":
        return this.store.snapshot();

      default:
        throw new Error(`unsupported Master command: ${envelope.type}`);
    }
  }

  private checkRevision(taskId: string, expected: number | undefined): void {
    if (expected === undefined) return;
    const task = this.store.getTask(taskId);
    if (!task) throw new Error(`task not found: ${taskId}`);
    if (task.revision !== expected) throw new Error(`stale revision: expected ${expected}, current ${task.revision}`);
  }
}
