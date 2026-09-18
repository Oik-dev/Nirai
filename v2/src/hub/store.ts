import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { fingerprint } from "../shared/stable.js";
import type {
  CreateRunInput,
  RunEffects,
  RunRecord,
  RunState,
  TaskRecord,
  TaskState,
} from "../shared/types.js";

type Row = Record<string, unknown>;

const TERMINAL_TASK_STATES = new Set<TaskState>(["Completed", "Failed", "Cancelled"]);
const TERMINAL_RUN_STATES = new Set<RunState>(["Completed", "Failed", "Cancelled", "Interrupted"]);

function now(): string {
  return new Date().toISOString();
}

function bool(value: unknown): boolean {
  return Number(value) !== 0;
}

function taskFromRow(row: Row): TaskRecord {
  return {
    id: String(row.id),
    title: String(row.title),
    resident_id: String(row.resident_id),
    objective: row.objective === null ? null : String(row.objective),
    initial_message_id: row.initial_message_id === null ? null : String(row.initial_message_id),
    workspace_scope: row.workspace_scope === null ? null : String(row.workspace_scope),
    completion_criteria: row.completion_criteria === null ? null : String(row.completion_criteria),
    state: String(row.state) as TaskState,
    resume_enabled: bool(row.resume_enabled),
    revision: Number(row.revision),
    control_epoch: Number(row.control_epoch),
    conversation_id: String(row.conversation_id),
    handled_instruction_seq: Number(row.handled_instruction_seq),
    wake_seq: Number(row.wake_seq),
    handled_wake_seq: Number(row.handled_wake_seq),
    created_at: String(row.created_at),
    updated_at: String(row.updated_at),
    started_at: row.started_at === null ? null : String(row.started_at),
    ended_at: row.ended_at === null ? null : String(row.ended_at),
    result_summary: row.result_summary === null ? null : String(row.result_summary),
  };
}

function runFromRow(row: Row): RunRecord {
  return {
    id: String(row.id),
    task_id: String(row.task_id),
    capability_id: String(row.capability_id),
    operation: String(row.operation),
    kind: String(row.kind) as RunRecord["kind"],
    parent_run_id: row.parent_run_id === null ? null : String(row.parent_run_id),
    state: String(row.state) as RunState,
    control_epoch: Number(row.control_epoch),
    dispatch_epoch: row.dispatch_epoch === null ? null : Number(row.dispatch_epoch),
    input_json: String(row.input_json),
    input_fingerprint: String(row.input_fingerprint),
    workspace_scope: row.workspace_scope === null ? null : String(row.workspace_scope),
    result_json: row.result_json === null ? null : String(row.result_json),
    supplemental_result_json:
      row.supplemental_result_json === null ? null : String(row.supplemental_result_json),
    error_json: row.error_json === null ? null : String(row.error_json),
    effects: String(row.effects) as RunEffects,
    cleanup_state: String(row.cleanup_state) as RunRecord["cleanup_state"],
    failure_resolution: String(row.failure_resolution) as RunRecord["failure_resolution"],
    resolution_note: row.resolution_note === null ? null : String(row.resolution_note),
    retry_of: row.retry_of === null ? null : String(row.retry_of),
    delivery_id: row.delivery_id === null ? null : String(row.delivery_id),
    delivery_state:
      row.delivery_state === null ? null : (String(row.delivery_state) as RunRecord["delivery_state"]),
    created_at: String(row.created_at),
    started_at: row.started_at === null ? null : String(row.started_at),
    ended_at: row.ended_at === null ? null : String(row.ended_at),
  };
}

export class HubStore {
  private readonly db: DatabaseSync;

  constructor(path: string) {
    mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    this.db.exec("PRAGMA foreign_keys = ON");
    this.db.exec("PRAGMA journal_mode = WAL");
    this.db.exec("PRAGMA synchronous = FULL");
    this.migrate();
  }

  private migrate(): void {
    const version = Number((this.db.prepare("PRAGMA user_version").get() as Row).user_version ?? 0);
    if (version > 1) {
      throw new Error(`unsupported hub schema version: ${version}`);
    }
    if (version === 1) return;

    this.transaction(() => {
      this.db.exec(`
        CREATE TABLE residents (
          id TEXT PRIMARY KEY,
          display_name TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE conversations (
          id TEXT PRIMARY KEY,
          task_id TEXT UNIQUE,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE tasks (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          resident_id TEXT NOT NULL REFERENCES residents(id),
          objective TEXT,
          initial_message_id TEXT,
          workspace_scope TEXT,
          completion_criteria TEXT,
          state TEXT NOT NULL CHECK(state IN ('Running','Paused','Completed','Failed','Cancelled')),
          resume_enabled INTEGER NOT NULL CHECK(resume_enabled IN (0,1)),
          revision INTEGER NOT NULL,
          control_epoch INTEGER NOT NULL,
          conversation_id TEXT NOT NULL UNIQUE REFERENCES conversations(id),
          handled_instruction_seq INTEGER NOT NULL,
          wake_seq INTEGER NOT NULL,
          handled_wake_seq INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          started_at TEXT,
          ended_at TEXT,
          result_summary TEXT
        );

        CREATE TABLE messages (
          id TEXT PRIMARY KEY,
          conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          seq INTEGER NOT NULL,
          sender TEXT NOT NULL,
          content TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(conversation_id, seq)
        );

        CREATE TABLE runs (
          id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL REFERENCES tasks(id),
          capability_id TEXT NOT NULL,
          operation TEXT NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('response','action')),
          parent_run_id TEXT REFERENCES runs(id),
          state TEXT NOT NULL CHECK(state IN ('Pending','Running','Completed','Failed','Cancelled','Interrupted')),
          control_epoch INTEGER NOT NULL,
          dispatch_epoch INTEGER,
          input_json TEXT NOT NULL,
          input_fingerprint TEXT NOT NULL,
          workspace_scope TEXT,
          result_json TEXT,
          supplemental_result_json TEXT,
          error_json TEXT,
          effects TEXT NOT NULL CHECK(effects IN ('none','applied','partial','unknown')),
          cleanup_state TEXT NOT NULL CHECK(cleanup_state IN ('clear','pending','unknown')),
          failure_resolution TEXT NOT NULL CHECK(failure_resolution IN ('not_required','unresolved','recovered','not_needed')),
          resolution_note TEXT,
          retry_of TEXT REFERENCES runs(id),
          delivery_id TEXT UNIQUE,
          delivery_state TEXT CHECK(delivery_state IN ('unsent','started','acknowledged','unknown')),
          created_at TEXT NOT NULL,
          started_at TEXT,
          ended_at TEXT
        );

        CREATE UNIQUE INDEX runs_one_active_response
          ON runs(task_id)
          WHERE kind='response' AND state IN ('Pending','Running');

        CREATE TABLE master_requests (
          id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL REFERENCES tasks(id),
          run_id TEXT REFERENCES runs(id),
          kind TEXT NOT NULL CHECK(kind IN ('approval','input')),
          state TEXT NOT NULL CHECK(state IN ('Pending','Resolved','Cancelled')),
          revision INTEGER NOT NULL,
          prompt TEXT NOT NULL,
          proposal_json TEXT,
          proposal_fingerprint TEXT,
          answer_json TEXT,
          created_at TEXT NOT NULL,
          resolved_at TEXT
        );

        CREATE TABLE settings (
          key TEXT PRIMARY KEY,
          value_json TEXT NOT NULL,
          revision INTEGER NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE provider_bindings (
          task_id TEXT PRIMARY KEY REFERENCES tasks(id),
          provider TEXT NOT NULL,
          external_conversation_id TEXT,
          external_url TEXT,
          binding_revision INTEGER NOT NULL,
          create_request_id TEXT,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE command_receipts (
          actor TEXT NOT NULL,
          command_id TEXT NOT NULL,
          input_fingerprint TEXT NOT NULL,
          result_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(actor, command_id)
        );

        PRAGMA user_version = 1;
      `);
    });
  }

  transaction<T>(fn: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const result = fn();
      this.db.exec("COMMIT");
      return result;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  close(): void {
    this.db.close();
  }

  ensureResident(id: string, displayName = id): void {
    const timestamp = now();
    this.db
      .prepare(`
        INSERT INTO residents(id, display_name, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
      `)
      .run(id, displayName, timestamp, timestamp);
  }

  createTask(residentId: string): TaskRecord {
    const taskId = randomUUID();
    const conversationId = randomUUID();
    const timestamp = now();

    this.db
      .prepare("INSERT INTO conversations(id, task_id, created_at, updated_at) VALUES (?, ?, ?, ?)")
      .run(conversationId, taskId, timestamp, timestamp);

    this.db
      .prepare(`
        INSERT INTO tasks(
          id, title, resident_id, state, resume_enabled, revision, control_epoch,
          conversation_id, handled_instruction_seq, wake_seq, handled_wake_seq,
          created_at, updated_at
        ) VALUES (?, ?, ?, 'Paused', 0, 1, 0, ?, 0, 0, 0, ?, ?)
      `)
      .run(taskId, "New Task", residentId, conversationId, timestamp, timestamp);

    return this.getTaskRequired(taskId);
  }

  getTask(id: string): TaskRecord | null {
    const row = this.db.prepare("SELECT * FROM tasks WHERE id = ?").get(id) as Row | undefined;
    return row ? taskFromRow(row) : null;
  }

  private getTaskRequired(id: string): TaskRecord {
    const task = this.getTask(id);
    if (!task) throw new Error(`task not found: ${id}`);
    return task;
  }

  listTasks(): TaskRecord[] {
    return (this.db.prepare("SELECT * FROM tasks ORDER BY created_at").all() as Row[]).map(taskFromRow);
  }

  listConversations(): Row[] {
    return this.db.prepare("SELECT * FROM conversations ORDER BY created_at").all() as Row[];
  }

  addMasterMessage(taskId: string, content: string): { message_id: string; seq: number; task: TaskRecord } {
    const task = this.getTaskRequired(taskId);
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task cannot receive instructions");

    const seqRow = this.db
      .prepare("SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM messages WHERE conversation_id = ?")
      .get(task.conversation_id) as Row;
    const seq = Number(seqRow.next_seq);
    const messageId = randomUUID();
    const timestamp = now();

    this.db
      .prepare("INSERT INTO messages(id, conversation_id, seq, sender, content, created_at) VALUES (?, ?, ?, 'master', ?, ?)")
      .run(messageId, task.conversation_id, seq, content, timestamp);

    if (task.initial_message_id === null) {
      this.db
        .prepare(`
          UPDATE tasks
          SET objective = ?, initial_message_id = ?, state = 'Running',
              revision = revision + 1, control_epoch = control_epoch + 1,
              wake_seq = wake_seq + 1, started_at = COALESCE(started_at, ?), updated_at = ?
          WHERE id = ?
        `)
        .run(content, messageId, timestamp, timestamp, taskId);
    } else {
      this.db
        .prepare(`
          UPDATE tasks
          SET revision = revision + 1, wake_seq = wake_seq + 1, updated_at = ?
          WHERE id = ?
        `)
        .run(timestamp, taskId);
    }
    this.db.prepare("UPDATE conversations SET updated_at = ? WHERE id = ?").run(timestamp, task.conversation_id);

    return { message_id: messageId, seq, task: this.getTaskRequired(taskId) };
  }

  updateDraftResident(taskId: string, residentId: string): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (task.initial_message_id !== null) throw new Error("started task resident is immutable");
    this.ensureResident(residentId, residentId);
    this.db
      .prepare(`
        UPDATE tasks
        SET resident_id=?, revision=revision+1, updated_at=?
        WHERE id=?
      `)
      .run(residentId, now(), taskId);
    return this.getTaskRequired(taskId);
  }

  setTaskResume(taskId: string, enabled: boolean): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task resume setting is immutable");
    this.db
      .prepare("UPDATE tasks SET resume_enabled = ?, revision = revision + 1, updated_at = ? WHERE id = ?")
      .run(enabled ? 1 : 0, now(), taskId);
    return this.getTaskRequired(taskId);
  }

  pauseTask(taskId: string): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task cannot be paused");
    if (task.state !== "Paused") {
      this.db
        .prepare(`
          UPDATE tasks
          SET state = 'Paused', revision = revision + 1, control_epoch = control_epoch + 1, updated_at = ?
          WHERE id = ?
        `)
        .run(now(), taskId);
    }
    return this.getTaskRequired(taskId);
  }

  resumeTask(taskId: string): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (task.initial_message_id === null) throw new Error("task has no initial instruction");
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task cannot be resumed");
    if (task.state !== "Running") {
      this.db
        .prepare(`
          UPDATE tasks
          SET state = 'Running', revision = revision + 1, control_epoch = control_epoch + 1,
              wake_seq = wake_seq + 1, updated_at = ?
          WHERE id = ?
        `)
        .run(now(), taskId);
    }
    return this.getTaskRequired(taskId);
  }

  cancelTask(taskId: string): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (TERMINAL_TASK_STATES.has(task.state)) return task;
    const timestamp = now();
    this.db
      .prepare(`
        UPDATE tasks
        SET state = 'Cancelled', revision = revision + 1, control_epoch = control_epoch + 1,
            ended_at = ?, updated_at = ?
        WHERE id = ?
      `)
      .run(timestamp, timestamp, taskId);
    return this.getTaskRequired(taskId);
  }

  createRun(input: CreateRunInput): RunRecord {
    const task = this.getTaskRequired(input.task_id);
    if (task.state !== "Running") throw new Error("task is not running");
    if (task.control_epoch !== input.control_epoch) throw new Error("stale control epoch");
    const dispatchEpoch = input.dispatch_epoch ?? input.control_epoch;
    if (dispatchEpoch !== task.control_epoch) throw new Error("stale dispatch epoch");

    if (input.parent_run_id) {
      const parent = this.getRun(input.parent_run_id);
      if (!parent || parent.task_id !== input.task_id || parent.kind !== "response" || parent.state !== "Running") {
        throw new Error("invalid parent response run");
      }
    }

    const id = randomUUID();
    const timestamp = now();
    const inputJson = JSON.stringify(input.input);
    this.db
      .prepare(`
        INSERT INTO runs(
          id, task_id, capability_id, operation, kind, parent_run_id, state,
          control_epoch, dispatch_epoch, input_json, input_fingerprint, workspace_scope,
          effects, cleanup_state, failure_resolution, retry_of, delivery_id, delivery_state, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?, ?, ?, 'none', 'clear', 'not_required', ?, ?, ?, ?)
      `)
      .run(
        id,
        input.task_id,
        input.capability_id,
        input.operation,
        input.kind,
        input.parent_run_id ?? null,
        input.control_epoch,
        dispatchEpoch,
        inputJson,
        fingerprint(input.input),
        input.workspace_scope ?? null,
        input.retry_of ?? null,
        input.delivery_id ?? null,
        input.delivery_id ? "unsent" : null,
        timestamp,
      );
    return this.getRunRequired(id);
  }

  getRun(id: string): RunRecord | null {
    const row = this.db.prepare("SELECT * FROM runs WHERE id = ?").get(id) as Row | undefined;
    return row ? runFromRow(row) : null;
  }

  private getRunRequired(id: string): RunRecord {
    const run = this.getRun(id);
    if (!run) throw new Error(`run not found: ${id}`);
    return run;
  }

  markRunRunning(id: string): RunRecord {
    const run = this.getRunRequired(id);
    if (run.state !== "Pending") throw new Error("run is not pending");
    const task = this.getTaskRequired(run.task_id);
    if (task.state !== "Running") throw new Error("task is not running");
    if (run.dispatch_epoch === null || run.dispatch_epoch !== task.control_epoch) {
      throw new Error("stale dispatch epoch");
    }
    this.db
      .prepare("UPDATE runs SET state='Running', started_at=? WHERE id=?")
      .run(now(), id);
    return this.getRunRequired(id);
  }

  recordRunResult(
    id: string,
    result: { state: Extract<RunState, "Completed" | "Failed" | "Cancelled">; effects: RunEffects; result?: unknown; error?: unknown },
  ): RunRecord {
    const run = this.getRunRequired(id);
    const timestamp = now();
    const resultJson = result.result === undefined ? null : JSON.stringify(result.result);
    const errorJson = result.error === undefined ? null : JSON.stringify(result.error);

    if (TERMINAL_RUN_STATES.has(run.state)) {
      this.db
        .prepare(`
          UPDATE runs
          SET supplemental_result_json=?, effects=?, cleanup_state='clear'
          WHERE id=?
        `)
        .run(resultJson, result.effects, id);
      return this.getRunRequired(id);
    }

    this.db
      .prepare(`
        UPDATE runs
        SET state=?, result_json=?, error_json=?, effects=?, cleanup_state='clear',
            failure_resolution=?, resolution_note=NULL, ended_at=?
        WHERE id=?
      `)
      .run(
        result.state,
        resultJson,
        errorJson,
        result.effects,
        result.state === "Failed" ? "unresolved" : "not_required",
        timestamp,
        id,
      );
    return this.getRunRequired(id);
  }

  recoverAfterRestart(): void {
    const timestamp = now();
    this.transaction(() => {
      this.db
        .prepare(`
          UPDATE tasks
          SET state='Paused', revision=revision+1, control_epoch=control_epoch+1, updated_at=?
          WHERE state='Running'
        `)
        .run(timestamp);

      this.db
        .prepare(`
          UPDATE runs
          SET state='Interrupted',
              effects=CASE WHEN effects='none' THEN 'unknown' ELSE effects END,
              cleanup_state='unknown',
              failure_resolution='unresolved',
              resolution_note=NULL,
              ended_at=?
          WHERE state='Running'
        `)
        .run(timestamp);
    });
  }

  createMasterRequest(input: {
    task_id: string;
    run_id?: string;
    kind: "approval" | "input";
    prompt: string;
    proposal?: unknown;
  }): { id: string; state: "Pending" } {
    const task = this.getTaskRequired(input.task_id);
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task cannot create a request");
    if (input.run_id) {
      const run = this.getRunRequired(input.run_id);
      if (run.task_id !== input.task_id) throw new Error("request run belongs to another task");
    }

    const id = randomUUID();
    const timestamp = now();
    const proposalJson = input.proposal === undefined ? null : JSON.stringify(input.proposal);
    this.db
      .prepare(`
        INSERT INTO master_requests(
          id, task_id, run_id, kind, state, revision, prompt,
          proposal_json, proposal_fingerprint, created_at
        ) VALUES (?, ?, ?, ?, 'Pending', 1, ?, ?, ?, ?)
      `)
      .run(
        id,
        input.task_id,
        input.run_id ?? null,
        input.kind,
        input.prompt,
        proposalJson,
        input.proposal === undefined ? null : fingerprint(input.proposal),
        timestamp,
      );
    return { id, state: "Pending" };
  }

  resolveMasterRequest(requestId: string, answer: unknown): Row {
    const request = this.db.prepare("SELECT * FROM master_requests WHERE id=?").get(requestId) as Row | undefined;
    if (!request) throw new Error(`request not found: ${requestId}`);
    if (String(request.state) !== "Pending") throw new Error("request is not pending");

    const timestamp = now();
    this.db
      .prepare(`
        UPDATE master_requests
        SET state='Resolved', revision=revision+1, answer_json=?, resolved_at=?
        WHERE id=?
      `)
      .run(JSON.stringify(answer), timestamp, requestId);
    return this.db.prepare("SELECT * FROM master_requests WHERE id=?").get(requestId) as Row;
  }

  completeTask(taskId: string, resultSummary: string): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (task.state !== "Running") throw new Error("only Running task can complete");

    const activeRuns = Number(
      (this.db
        .prepare("SELECT COUNT(*) AS count FROM runs WHERE task_id=? AND state IN ('Pending','Running')")
        .get(taskId) as Row).count,
    );
    if (activeRuns > 0) throw new Error("task has unfinished runs");

    const pendingRequests = Number(
      (this.db
        .prepare("SELECT COUNT(*) AS count FROM master_requests WHERE task_id=? AND state='Pending'")
        .get(taskId) as Row).count,
    );
    if (pendingRequests > 0) throw new Error("task has pending Master Requests");

    const uncertainRuns = Number(
      (this.db
        .prepare(`
          SELECT COUNT(*) AS count
          FROM runs
          WHERE task_id=? AND (effects='unknown' OR cleanup_state!='clear')
        `)
        .get(taskId) as Row).count,
    );
    if (uncertainRuns > 0) throw new Error("task has unresolved side effects");

    const unresolvedFailures = Number(
      (this.db
        .prepare(`
          SELECT COUNT(*) AS count
          FROM runs
          WHERE task_id=? AND state IN ('Failed','Interrupted') AND failure_resolution='unresolved'
        `)
        .get(taskId) as Row).count,
    );
    if (unresolvedFailures > 0) throw new Error("task has unresolved failed runs");

    const timestamp = now();
    this.db
      .prepare(`
        UPDATE tasks
        SET state='Completed', revision=revision+1, control_epoch=control_epoch+1,
            result_summary=?, ended_at=?, updated_at=?
        WHERE id=?
      `)
      .run(resultSummary, timestamp, timestamp, taskId);
    return this.getTaskRequired(taskId);
  }

  resolveRunFailure(runId: string, resolution: "recovered" | "not_needed", note: string): RunRecord {
    const run = this.getRunRequired(runId);
    if (run.state !== "Failed" && run.state !== "Interrupted") {
      throw new Error("only failed or interrupted run can be resolved");
    }
    if (run.failure_resolution !== "unresolved") {
      throw new Error("run failure is already resolved");
    }
    if (!note.trim()) throw new Error("resolution note is required");

    this.db
      .prepare(`
        UPDATE runs
        SET failure_resolution=?, resolution_note=?
        WHERE id=?
      `)
      .run(resolution, note, runId);
    return this.getRunRequired(runId);
  }

  getCommandReceipt(actor: string, commandId: string): { input_fingerprint: string; result: Record<string, unknown> } | null {
    const row = this.db
      .prepare("SELECT input_fingerprint, result_json FROM command_receipts WHERE actor=? AND command_id=?")
      .get(actor, commandId) as Row | undefined;
    if (!row) return null;
    return {
      input_fingerprint: String(row.input_fingerprint),
      result: JSON.parse(String(row.result_json)) as Record<string, unknown>,
    };
  }

  saveCommandReceipt(actor: string, commandId: string, inputFingerprint: string, result: Record<string, unknown>): void {
    this.db
      .prepare(`
        INSERT INTO command_receipts(actor, command_id, input_fingerprint, result_json, created_at)
        VALUES (?, ?, ?, ?, ?)
      `)
      .run(actor, commandId, inputFingerprint, JSON.stringify(result), now());
  }

  snapshot(): Record<string, unknown> {
    const tasks = this.listTasks();
    const requests = this.db
      .prepare("SELECT * FROM master_requests WHERE state='Pending' ORDER BY created_at")
      .all() as Row[];
    const runs = this.db
      .prepare("SELECT * FROM runs ORDER BY created_at")
      .all() as Row[];
    const messages = this.db
      .prepare("SELECT * FROM messages ORDER BY conversation_id, seq")
      .all() as Row[];
    const residents = this.db
      .prepare("SELECT id, display_name, created_at, updated_at FROM residents ORDER BY created_at")
      .all() as Row[];
    return {
      tasks,
      pending_requests: requests,
      runs,
      messages,
      residents,
    };
  }
}
