import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { backup as sqliteBackup, DatabaseSync } from "node:sqlite";

import { fingerprint } from "../shared/stable.js";
import { DEFAULT_SETTINGS, type HubSettings } from "../shared/settings.js";
import { HubError } from "../shared/errors.js";
import type { ArtifactReference, CompletionCriterion, CompletionEvidence, VerificationResult } from "../shared/types.js";
import type {
  CreateRunInput,
  RunEffects,
  RunRecord,
  RunSideEffects,
  RunState,
  TaskRecord,
  TaskState,
} from "../shared/types.js";

type Row = Record<string, unknown>;

export const HUB_SCHEMA_VERSION = 5;
const TERMINAL_TASK_STATES = new Set<TaskState>(["Completed", "Failed", "Cancelled"]);
const TERMINAL_RUN_STATES = new Set<RunState>(["Completed", "Failed", "Cancelled", "Interrupted"]);

function now(): string {
  return new Date().toISOString();
}

function bool(value: unknown): boolean {
  return Number(value) !== 0;
}

function provisionalTitle(content: string): string {
  const normalized = content.replace(/\s+/g, " ").trim();
  if (normalized.length <= 48) return normalized;
  return `${normalized.slice(0, 47)}…`;
}

function taskFromRow(row: Row): TaskRecord {
  return {
    id: String(row.id),
    title: String(row.title),
    resident_id: String(row.resident_id),
    objective: row.objective === null ? null : String(row.objective),
    initial_message_id: row.initial_message_id === null ? null : String(row.initial_message_id),
    workspace_scope: row.workspace_scope === null ? null : String(row.workspace_scope),
    completion_criteria: row.completion_criteria === null ? [] : JSON.parse(String(row.completion_criteria)),
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
    side_effects: String(row.side_effects) as RunRecord["side_effects"],
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
    context_instruction_seq: Number(row.context_instruction_seq),
    context_wake_seq: Number(row.context_wake_seq),
    resources_json: String(row.resources_json),
    settings_json: String(row.settings_json),
    stop_requested_at: row.stop_requested_at === null ? null : String(row.stop_requested_at),
  };
}

export class HubStore {
  private readonly db: DatabaseSync;
  private transactionDepth = 0;

  static async open(path: string, appVersion = "dev"): Promise<HubStore> {
    mkdirSync(dirname(path), { recursive: true });

    if (existsSync(path)) {
      const probe = new DatabaseSync(path);
      try {
        const version = Number((probe.prepare("PRAGMA user_version").get() as Row).user_version ?? 0);
        if (version > HUB_SCHEMA_VERSION) {
          throw new Error(`unsupported hub schema version: ${version}`);
        }
        if (version > 0 && version < HUB_SCHEMA_VERSION) {
          const backupDir = join(dirname(path), "backups");
          mkdirSync(backupDir, { recursive: true });
          const backupPath = join(backupDir, `hub-pre-migration-v${version}-${Date.now()}.sqlite3`);
          await sqliteBackup(probe, backupPath);
        }
      } finally {
        probe.close();
      }
    }

    return new HubStore(path, appVersion);
  }

  constructor(path: string, private readonly appVersion = "dev") {
    mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    try {
      const version = Number((this.db.prepare("PRAGMA user_version").get() as Row).user_version);
      if (version > HUB_SCHEMA_VERSION) throw new Error(`unsupported hub schema version: ${version}`);
      const integrity = this.db.prepare("PRAGMA quick_check").all() as Row[];
      if (integrity.some(row => row.quick_check !== "ok")) throw new Error("Hub database integrity check failed");
      if (version === 0 && this.db.prepare("SELECT 1 FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'").get()) {
        throw new Error("unversioned nonempty Hub database cannot be initialized");
      }
      this.db.exec("PRAGMA foreign_keys = ON");
      this.db.exec("PRAGMA journal_mode = WAL");
      this.db.exec("PRAGMA synchronous = FULL");
      this.transaction(() => this.migrate());
      this.initializeSettings();
      this.recordMetadata();
    } catch (error) {
      this.db.close();
      throw error;
    }
  }

  private migrate(): void {
    const version = Number((this.db.prepare("PRAGMA user_version").get() as Row).user_version ?? 0);
    if (version > HUB_SCHEMA_VERSION) {
      throw new Error(`unsupported hub schema version: ${version}`);
    }
    if (version === HUB_SCHEMA_VERSION) return;

    if (version === 0) {
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
          side_effects TEXT NOT NULL CHECK(side_effects IN ('none','possible')),
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

        CREATE TABLE metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        PRAGMA user_version = 4;
      `);
      });
      this.migrate();
      return;
    }

    if (version === 1) {
      this.transaction(() => {
        this.db.exec(`
          CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
          );
          ALTER TABLE runs DROP COLUMN dispatch_epoch;
          ALTER TABLE runs ADD COLUMN side_effects TEXT NOT NULL DEFAULT 'possible' CHECK(side_effects IN ('none','possible'));
          PRAGMA user_version = 4;
        `);
      });
      this.migrate();
      return;
    }

    if (version === 2) {
      this.transaction(() => {
        this.db.exec(`
          ALTER TABLE runs DROP COLUMN dispatch_epoch;
          ALTER TABLE runs ADD COLUMN side_effects TEXT NOT NULL DEFAULT 'possible' CHECK(side_effects IN ('none','possible'));
          PRAGMA user_version = 4;
        `);
      });
      this.migrate();
      return;
    }

    if (version === 3) {
      this.transaction(() => {
        this.db.exec(`
          ALTER TABLE runs ADD COLUMN side_effects TEXT NOT NULL DEFAULT 'possible' CHECK(side_effects IN ('none','possible'));
          PRAGMA user_version = 4;
        `);
      });
      this.migrate();
      return;
    }

    if (version === 4) {
      this.transaction(() => {
        this.db.exec(`
          ALTER TABLE runs ADD COLUMN context_instruction_seq INTEGER NOT NULL DEFAULT 0;
          ALTER TABLE runs ADD COLUMN context_wake_seq INTEGER NOT NULL DEFAULT 0;
          ALTER TABLE runs ADD COLUMN resources_json TEXT NOT NULL DEFAULT '[]';
          ALTER TABLE runs ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}';
          ALTER TABLE runs ADD COLUMN stop_requested_at TEXT;
          ALTER TABLE messages ADD COLUMN run_id TEXT REFERENCES runs(id);
          ALTER TABLE messages ADD COLUMN request_id TEXT REFERENCES master_requests(id);
          ALTER TABLE master_requests ADD COLUMN answered_by TEXT;
          CREATE TABLE artifact_references (
            task_id TEXT NOT NULL REFERENCES tasks(id),
            run_id TEXT NOT NULL REFERENCES runs(id),
            ref TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            ownership TEXT NOT NULL CHECK(ownership IN ('temporary','project','shared','recovery')),
            observed_at TEXT NOT NULL,
            PRIMARY KEY(task_id, run_id, ref)
          );
          PRAGMA user_version = 5;
        `);
        // Old free text is a requirement to verify, never evidence of completion.
        for (const row of this.db.prepare("SELECT id, completion_criteria FROM tasks WHERE completion_criteria IS NOT NULL").all() as Row[]) {
          this.db.prepare("UPDATE tasks SET completion_criteria=? WHERE id=?").run(JSON.stringify([
            { id: randomUUID(), text: String(row.completion_criteria), required: true, verification_kind: null },
          ]), String(row.id));
        }
      });
      return;
    }
    throw new Error(`unsupported hub schema migration path: ${version}`);
  }

  private recordMetadata(): void {
    const timestamp = now();
    const statement = this.db.prepare(`
      INSERT INTO metadata(key, value, updated_at)
      VALUES (?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
    `);
    statement.run("schema_version", String(HUB_SCHEMA_VERSION), timestamp);
    statement.run("app_version", this.appVersion, timestamp);
  }

  getMetadata(key: string): string | null {
    const row = this.db.prepare("SELECT value FROM metadata WHERE key=?").get(key) as Row | undefined;
    return row ? String(row.value) : null;
  }

  transaction<T>(fn: () => T): T {
    if (this.transactionDepth > 0) return fn();
    this.db.exec("BEGIN IMMEDIATE");
    this.transactionDepth += 1;
    try {
      const result = fn();
      if (result instanceof Promise) throw new Error("external IO cannot run in a Store transaction");
      this.db.prepare(`INSERT INTO metadata(key,value,updated_at) VALUES ('snapshot_revision','1',?)
        ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1,updated_at=excluded.updated_at`).run(now());
      this.db.exec("COMMIT");
      return result;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    } finally {
      this.transactionDepth -= 1;
    }
  }

  async backupTo(destinationPath: string): Promise<void> {
    mkdirSync(dirname(destinationPath), { recursive: true });
    await sqliteBackup(this.db, destinationPath);
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

  residentExists(id: string): boolean {
    return Boolean(this.db.prepare("SELECT 1 AS ok FROM residents WHERE id=?").get(id));
  }

  createTask(residentId: string): TaskRecord {
    if (!this.residentExists(residentId)) throw new Error(`resident not found: ${residentId}`);
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
    this.db.prepare("UPDATE tasks SET workspace_scope=? WHERE id=?").run(this.getSettings().value.workspace_scope, taskId);

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
    if (!content.trim()) throw new Error("invalid empty message");
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
          SET title = ?, objective = ?, initial_message_id = ?, state = 'Running',
              revision = revision + 1, control_epoch = control_epoch + 1,
              started_at = COALESCE(started_at, ?), updated_at = ?
          WHERE id = ?
        `)
        .run(provisionalTitle(content), content, messageId, timestamp, timestamp, taskId);
    } else {
      this.db
        .prepare(`
          UPDATE tasks
          SET revision = revision + 1, updated_at = ?
          WHERE id = ?
        `)
        .run(timestamp, taskId);
    }
    this.db.prepare("UPDATE conversations SET updated_at = ? WHERE id = ?").run(timestamp, task.conversation_id);

    return { message_id: messageId, seq, task: this.getTaskRequired(taskId) };
  }

  updateDraftResident(taskId: string, residentId: string): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task definition is immutable");
    if (task.initial_message_id !== null) throw new Error("started task resident is immutable");
    if (!this.residentExists(residentId)) throw new Error(`resident not found: ${residentId}`);
    this.db
      .prepare(`
        UPDATE tasks
        SET resident_id=?, revision=revision+1, updated_at=?
        WHERE id=?
      `)
      .run(residentId, now(), taskId);
    return this.getTaskRequired(taskId);
  }

  updateTaskDefinition(taskId: string, changes: Record<string, unknown>): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task definition is immutable");
    if (changes.resident_id !== undefined) this.updateDraftResident(taskId, String(changes.resident_id));
    if (changes.title !== undefined || changes.completion_criteria !== undefined) this.refineTaskDefinition(taskId,
      String(changes.title ?? task.title), (changes.completion_criteria ?? task.completion_criteria) as CompletionCriterion[], true);
    if (changes.workspace_scope !== undefined || changes.objective !== undefined) {
      if (this.listRuns(taskId).some(run => ["Pending", "Running"].includes(run.state) || run.effects === "unknown" || run.cleanup_state !== "clear")) throw new Error("Task scope cannot change with unfinished work");
      const scope = changes.workspace_scope === undefined ? task.workspace_scope : changes.workspace_scope;
      const objective = changes.objective ?? task.objective;
      if (scope !== null && typeof scope !== "string" || objective !== null && typeof objective !== "string") throw new Error("invalid Task definition");
      this.db.prepare("UPDATE tasks SET workspace_scope=?,objective=?,revision=revision+1,control_epoch=control_epoch+1,updated_at=? WHERE id=?")
        .run(scope as string | null, objective as string | null, now(), taskId);
    }
    return this.getTaskRequired(taskId);
  }

  setTaskResume(taskId: string, enabled: boolean): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (task.initial_message_id === null) throw new Error("task has no initial instruction");
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
      const timestamp = now();
      this.db
        .prepare(`
          UPDATE tasks
          SET state = 'Paused', revision = revision + 1, control_epoch = control_epoch + 1, updated_at = ?
          WHERE id = ?
        `)
        .run(timestamp, taskId);
      this.db
        .prepare(`
          UPDATE runs
          SET state='Cancelled', effects='none', cleanup_state='clear',
              failure_resolution='not_required', ended_at=?
          WHERE task_id=? AND state='Pending'
        `)
        .run(timestamp, taskId);
      this.cancelObsoleteApprovals();
      this.requestRunStops(taskId);
    }
    return this.getTaskRequired(taskId);
  }

  private cancelObsoleteApprovals(): void {
    this.db.prepare(`
      UPDATE master_requests SET state='Cancelled', revision=revision+1, resolved_at=?
      WHERE state='Pending' AND kind='approval'
        AND run_id IN (SELECT id FROM runs WHERE state='Cancelled')
    `).run(now());
  }

  prepareShutdown(): void {
    this.transaction(() => {
      for (const task of this.listTasks()) {
        if (!TERMINAL_TASK_STATES.has(task.state)) this.pauseTask(task.id);
      }
    });
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
    this.db
      .prepare(`
        UPDATE runs
        SET state='Cancelled', effects='none', cleanup_state='clear',
            failure_resolution='not_required', ended_at=?
        WHERE task_id=? AND state='Pending'
      `)
      .run(timestamp, taskId);
    this.db
      .prepare(`
        UPDATE master_requests
        SET state='Cancelled', revision=revision+1, resolved_at=?
        WHERE task_id=? AND state='Pending'
      `)
      .run(timestamp, taskId);
    this.requestRunStops(taskId);
    return this.getTaskRequired(taskId);
  }

  createRun(input: CreateRunInput, sideEffects: RunSideEffects): RunRecord {
    const task = this.getTaskRequired(input.task_id);
    if (task.state !== "Running") throw new Error("task is not running");
    if (task.control_epoch !== input.control_epoch) throw new Error("stale control epoch");
    if (input.workspace_scope !== undefined && input.workspace_scope !== task.workspace_scope) {
      throw new Error("Run workspace must match the Task scope");
    }
    if (input.parent_run_id) {
      const parent = this.getRun(input.parent_run_id);
      if (!parent || parent.task_id !== input.task_id || parent.kind !== "response" || parent.state !== "Running"
        || parent.control_epoch !== task.control_epoch || input.kind !== "action") {
        throw new Error("invalid parent response run");
      }
    }
    if (input.retry_of) {
      const previous = this.getRunRequired(input.retry_of);
      if (previous.task_id !== task.id || !TERMINAL_RUN_STATES.has(previous.state)
        || previous.effects === "unknown" || previous.cleanup_state !== "clear"
        || previous.delivery_state === "started" || previous.delivery_state === "unknown") {
        throw new Error("retry requires a terminal Run with reconciled effects and delivery in the same Task");
      }
    }

    const id = randomUUID();
    const timestamp = now();
    const inputJson = JSON.stringify(input.input);
    this.db
      .prepare(`
        INSERT INTO runs(
          id, task_id, capability_id, operation, kind, parent_run_id, state,
          control_epoch, side_effects, input_json, input_fingerprint, workspace_scope,
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
        sideEffects,
        inputJson,
        fingerprint(input.input),
        task.workspace_scope,
        input.retry_of ?? null,
        input.delivery_id ?? null,
        input.delivery_id ? "unsent" : null,
        timestamp,
      );
    this.db.prepare("UPDATE runs SET resources_json=?, settings_json=? WHERE id=?")
      .run(JSON.stringify(input.resources ?? []), JSON.stringify(this.getSettings().value), id);
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
    if (run.control_epoch !== task.control_epoch) throw new Error("stale control epoch");
    if (!this.resourcesAvailable(JSON.parse(run.resources_json), id)) throw new HubError("blocked", "Run resources are still in use");
    const approvals = this.db.prepare("SELECT * FROM master_requests WHERE run_id=? AND kind='approval'").all(id) as Row[];
    for (const approval of approvals) {
      if (approval.state !== "Resolved" || JSON.parse(String(approval.answer_json)).approved !== true) {
        throw new Error("run requires Master approval");
      }
      if (approval.proposal_fingerprint !== fingerprint(this.approvalProposal(run))) {
        throw new Error("approval proposal no longer matches Run");
      }
    }
    this.db
      .prepare("UPDATE runs SET state='Running', started_at=? WHERE id=?")
      .run(now(), id);
    return this.getRunRequired(id);
  }

  recordRunResult(
    id: string,
    result: {
      state: Extract<RunState, "Completed" | "Failed" | "Cancelled">;
      effects: RunEffects;
      result?: unknown;
      error?: unknown;
      cleanup_state?: "clear" | "pending" | "unknown";
      artifacts?: ArtifactReference[];
      verification?: VerificationResult;
    },
  ): RunRecord {
    return this.transaction(() => this.saveRunResult(id, result));
  }

  private saveRunResult(id: string, result: Parameters<HubStore["recordRunResult"]>[1]): RunRecord {
    const run = this.getRunRequired(id);
    const timestamp = now();
    if (result.artifacts?.length && run.kind !== "action") throw new Error("artifact observations require an action Adapter");
    for (const artifact of result.artifacts ?? []) {
      if (!artifact.ref || !artifact.fingerprint || !["temporary", "project", "shared", "recovery"].includes(artifact.ownership)) {
        throw new Error("invalid artifact ownership or fingerprint");
      }
      this.db.prepare(`INSERT INTO artifact_references(task_id, run_id, ref, fingerprint, ownership, observed_at)
        VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(task_id, run_id, ref) DO UPDATE SET
        fingerprint=excluded.fingerprint, ownership=excluded.ownership, observed_at=excluded.observed_at`)
        .run(run.task_id, id, artifact.ref, artifact.fingerprint, artifact.ownership, timestamp);
    }
    const resultJson = JSON.stringify({ value: result.result ?? null, verification: result.verification ?? null });
    const errorJson = result.error === undefined ? null : JSON.stringify(result.error);
    const cleanupState = result.cleanup_state ?? (result.effects === "unknown" ? "unknown" : "clear");

    if (TERMINAL_RUN_STATES.has(run.state)) {
      // Only registered Adapter results reach this method. Preserve the terminal
      // status and keep the complete observation, including cleanup and effects.
      const observations = run.supplemental_result_json
        ? JSON.parse(run.supplemental_result_json) as unknown : [];
      const history = Array.isArray(observations) ? observations : [observations];
      history.push({ ...result, observed_at: timestamp });
      const reconciled = result.effects !== "unknown" && result.cleanup_state === "clear";
      const nextEffects = result.effects === "unknown" ? "unknown" : reconciled ? result.effects : run.effects;
      const nextCleanup = result.effects === "unknown" ? "unknown"
        : result.cleanup_state === "pending" || result.cleanup_state === "unknown" ? result.cleanup_state
        : reconciled ? "clear" : run.cleanup_state;
      this.db
        .prepare(`
          UPDATE runs
          SET supplemental_result_json=?,
              effects=?, cleanup_state=?
          WHERE id=?
        `)
        .run(JSON.stringify(history), nextEffects, nextCleanup, id);
      return this.getRunRequired(id);
    }

    this.db
      .prepare(`
        UPDATE runs
        SET state=?, result_json=?, error_json=?, effects=?, cleanup_state=?,
            failure_resolution=?, resolution_note=NULL, ended_at=?
        WHERE id=?
      `)
      .run(
        result.state,
        resultJson,
        errorJson,
        result.effects,
        cleanupState,
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
          SET resume_enabled=0, revision=revision+1, updated_at=?
          WHERE initial_message_id IS NULL AND resume_enabled=1
        `)
        .run(timestamp);

      this.db
        .prepare(`
          UPDATE runs
          SET state='Cancelled', effects='none', cleanup_state='clear',
              failure_resolution='not_required', ended_at=?
          WHERE state='Pending'
        `)
        .run(timestamp);

      this.db
        .prepare(`
          UPDATE tasks
          SET state='Paused', revision=revision+1, control_epoch=control_epoch+1, updated_at=?
          WHERE state='Running'
        `)
        .run(timestamp);

      this.db
        .prepare(`
          UPDATE master_requests
          SET state='Cancelled', revision=revision+1, resolved_at=?
          WHERE state='Pending'
            AND task_id IN (SELECT id FROM tasks WHERE state IN ('Completed','Failed','Cancelled'))
        `)
        .run(timestamp);

      this.db
        .prepare(`
          UPDATE runs
          SET state='Interrupted',
              effects=CASE WHEN side_effects='none' THEN 'none' WHEN effects='none' THEN 'unknown' ELSE effects END,
              cleanup_state=CASE WHEN side_effects='none' THEN 'clear' ELSE 'unknown' END,
              failure_resolution='unresolved',
              resolution_note=NULL,
              ended_at=?
          WHERE state='Running'
        `)
        .run(timestamp);
      this.cancelObsoleteApprovals();
    });
  }

  private approvalProposal(run: RunRecord): unknown {
    return {
      capability_id: run.capability_id,
      operation: run.operation,
      input: JSON.parse(run.input_json) as unknown,
      workspace_scope: run.workspace_scope,
    };
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
    if (!input.prompt.trim()) throw new Error("request prompt is required");
    let proposal = input.proposal;
    if (input.run_id) {
      const run = this.getRunRequired(input.run_id);
      if (run.task_id !== input.task_id) throw new Error("request run belongs to another task");
      if (input.kind === "approval") {
        if (run.kind !== "action" || run.state !== "Pending" || run.control_epoch !== task.control_epoch) {
          throw new Error("approval requires a current pending action Run");
        }
        proposal = this.approvalProposal(run);
        if (input.proposal !== undefined && fingerprint(input.proposal) !== fingerprint(proposal)) {
          throw new Error("approval proposal must match Run");
        }
      }
    }
    if (input.kind === "approval" && !input.run_id) throw new Error("approval requires a Run");

    const id = randomUUID();
    const timestamp = now();
    const proposalJson = proposal === undefined ? null : JSON.stringify(proposal);
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
        proposal === undefined ? null : fingerprint(proposal),
        timestamp,
      );
    return { id, state: "Pending" };
  }

  resolveMasterRequest(requestId: string, answer: unknown, expectedRevision: number): Row {
    const request = this.db.prepare("SELECT * FROM master_requests WHERE id=?").get(requestId) as Row | undefined;
    if (!request) throw new Error(`request not found: ${requestId}`);
    if (Number(request.revision) !== expectedRevision) {
      throw new Error(`stale request revision: expected ${expectedRevision}, current ${request.revision}`);
    }
    if (String(request.state) !== "Pending") throw new Error("request is not pending");

    const task = this.getTaskRequired(String(request.task_id));
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task cannot resolve a request");
    if (!answer || typeof answer !== "object" || Array.isArray(answer)) throw new Error("invalid request answer");
    const fields = answer as Row;
    if (request.kind === "approval") {
      if (typeof fields.approved !== "boolean") throw new Error("approval answer requires approved boolean");
    } else if (typeof fields.text !== "string" || !fields.text.trim()) {
      throw new Error("input answer requires non-empty text");
    }
    const timestamp = now();
    this.db
      .prepare(`
        UPDATE master_requests
        SET state='Resolved', revision=revision+1, answer_json=?, resolved_at=?, answered_by='master'
        WHERE id=?
      `)
      .run(JSON.stringify(answer), timestamp, requestId);
    if (request.kind === "approval" && fields.approved === false) {
      this.db.prepare(`
        UPDATE runs SET state='Cancelled', effects='none', cleanup_state='clear',
          failure_resolution='not_required', ended_at=? WHERE id=? AND state='Pending'
      `).run(timestamp, String(request.run_id));
      this.cancelObsoleteApprovals();
    }
    this.db
      .prepare(`
        UPDATE tasks
        SET revision=revision+1,
            wake_seq=wake_seq + CASE WHEN state='Running' THEN 1 ELSE 0 END,
            updated_at=?
        WHERE id=?
      `)
      .run(timestamp, task.id);
    return this.db.prepare("SELECT * FROM master_requests WHERE id=?").get(requestId) as Row;
  }

  private initializeSettings(): void {
    this.db.prepare("INSERT INTO settings(key,value_json,revision,updated_at) VALUES ('runtime',?,1,?) ON CONFLICT(key) DO NOTHING")
      .run(JSON.stringify(DEFAULT_SETTINGS), now());
  }

  getSettings(): { value: HubSettings; revision: number } {
    const row = this.db.prepare("SELECT * FROM settings WHERE key='runtime'").get() as Row;
    return { value: JSON.parse(String(row.value_json)), revision: Number(row.revision) };
  }

  updateSettings(value: Record<string, unknown>, revision: number): ReturnType<HubStore["getSettings"]> {
    const current = this.getSettings();
    if (current.revision !== revision) throw new HubError("stale", "stale settings revision", current);
    for (const [key, setting] of Object.entries(value)) {
      if (!Object.hasOwn(DEFAULT_SETTINGS, key)) throw new Error(`invalid setting: ${key}`);
      if (key === "workspace_scope") {
        if (setting !== null && (typeof setting !== "string" || !setting.trim())) throw new Error("invalid workspace_scope");
      } else if (key === "communication_retry_ms") {
        if (!Array.isArray(setting) || setting.length !== 2 || setting.some(n => !Number.isSafeInteger(n) || n < 1 || n > 60_000)) throw new Error("invalid retry delays");
      } else if (!Number.isSafeInteger(setting) || Number(setting) < 1 || Number(setting) > Number(DEFAULT_SETTINGS[key as keyof HubSettings])) {
        throw new Error(`invalid setting limit: ${key}`);
      }
    }
    const next = { ...current.value, ...value };
    if (next.command_timeout_ms > next.command_max_timeout_ms) throw new Error("invalid command timeout");
    this.db.prepare("UPDATE settings SET value_json=?,revision=revision+1,updated_at=? WHERE key='runtime'")
      .run(JSON.stringify(next), now());
    return this.getSettings();
  }

  listRuns(taskId?: string): RunRecord[] {
    return (taskId ? this.db.prepare("SELECT * FROM runs WHERE task_id=? ORDER BY rowid").all(taskId)
      : this.db.prepare("SELECT * FROM runs ORDER BY rowid").all()).map(row => runFromRow(row as Row));
  }

  private latestInstructionSeq(task: TaskRecord): number {
    return Number((this.db.prepare("SELECT COALESCE(MAX(seq),0) AS seq FROM messages WHERE conversation_id=? AND sender='master'")
      .get(task.conversation_id) as Row).seq);
  }

  resourcesAvailable(resources: string[], exceptRunId?: string): boolean {
    return !this.listRuns().some(run => run.id !== exceptRunId
      && (run.state === "Running" || run.effects === "unknown" || run.cleanup_state !== "clear"
        || run.delivery_state === "started" || run.delivery_state === "unknown")
      && (JSON.parse(run.resources_json) as string[]).some(resource => resources.includes(resource)));
  }

  needsResponse(taskId: string): boolean {
    const task = this.getTaskRequired(taskId);
    if (task.state !== "Running" || !task.initial_message_id) return false;
    const runs = this.listRuns(taskId);
    if (runs.some(run => run.state === "Pending" || run.state === "Running" || run.effects === "unknown"
      || run.cleanup_state !== "clear" || run.delivery_state === "started" || run.delivery_state === "unknown")) return false;
    if (this.db.prepare("SELECT 1 FROM master_requests WHERE task_id=? AND state='Pending'").get(taskId)) return false;
    if (this.latestInstructionSeq(task) > task.handled_instruction_seq || task.wake_seq > task.handled_wake_seq) return true;
    const last = runs.filter(run => run.kind === "response").at(-1);
    return task.resume_enabled && last?.state === "Completed"
      && ["continue", "wait"].includes(JSON.parse(last.result_json ?? "{}").value?.disposition);
  }

  reserveResponse(input: CreateRunInput, sideEffects: RunSideEffects): RunRecord | null {
    return this.transaction(() => {
      if (!this.needsResponse(input.task_id) || !this.resourcesAvailable(input.resources ?? [])) return null;
      const run = this.createRun(input, sideEffects);
      this.markRunRunning(run.id);
      this.getTaskContext(run.id);
      const delivered = this.getRunRequired(run.id);
      this.acknowledgeTaskContext(input.task_id, delivered.context_instruction_seq, delivered.context_wake_seq);
      return delivered;
    });
  }

  assertResponse(runId: string, taskId?: string): RunRecord {
    const run = this.getRunRequired(runId);
    const task = this.getTaskRequired(run.task_id);
    if (taskId !== undefined && taskId !== task.id) throw new HubError("unauthorized", "response belongs to another Task");
    if (run.kind !== "response" || run.state !== "Running" || task.state !== "Running"
      || run.control_epoch !== task.control_epoch || run.stop_requested_at) throw new HubError("stale", "stale response permission");
    const settings = JSON.parse(run.settings_json) as HubSettings;
    if (Date.now() - Date.parse(run.started_at!) > settings.response_deadline_ms) throw new HubError("stale", "response permission expired");
    return run;
  }

  getTaskContext(runId: string): Record<string, unknown> {
    const run = this.assertResponse(runId);
    const task = this.getTaskRequired(run.task_id);
    const instructionSeq = this.latestInstructionSeq(task);
    this.db.prepare("UPDATE runs SET context_instruction_seq=?,context_wake_seq=? WHERE id=?").run(instructionSeq, task.wake_seq, runId);
    return {
      task, instruction_seq: instructionSeq, wake_seq: task.wake_seq,
      // Failed responses retain their original instructions in this source history.
      messages: this.db.prepare("SELECT * FROM messages WHERE conversation_id=? ORDER BY seq").all(task.conversation_id),
      requests: this.db.prepare("SELECT * FROM master_requests WHERE task_id=? ORDER BY created_at").all(task.id),
      runs: this.listRuns(task.id),
      artifacts: this.db.prepare("SELECT * FROM artifact_references WHERE task_id=? ORDER BY observed_at").all(task.id),
    };
  }

  acknowledgeResponseContext(runId: string, instructionSeq: number, wakeSeq: number): void {
    const run = this.assertResponse(runId);
    if (instructionSeq > run.context_instruction_seq || wakeSeq > run.context_wake_seq) throw new HubError("stale", "response has not received these instructions");
    this.acknowledgeTaskContext(run.task_id, instructionSeq, wakeSeq);
  }

  addResponseMessage(runId: string, content: string): string {
    if (!content.trim()) throw new Error("invalid empty message");
    const run = this.assertResponse(runId);
    return this.addResultMessage(run.task_id, content, run.id);
  }

  private addResultMessage(taskId: string, content: string, runId: string | null): string {
    const task = this.getTaskRequired(taskId);
    const id = randomUUID();
    const seq = Number((this.db.prepare("SELECT COALESCE(MAX(seq),0)+1 AS seq FROM messages WHERE conversation_id=?")
      .get(task.conversation_id) as Row).seq);
    this.db.prepare("INSERT INTO messages(id,conversation_id,seq,sender,content,created_at,run_id) VALUES (?,?,?,?,?,?,?)")
      .run(id, task.conversation_id, seq, task.resident_id, content, now(), runId);
    return id;
  }

  requestRunStops(taskId: string): void {
    this.db.prepare("UPDATE runs SET stop_requested_at=COALESCE(stop_requested_at,?) WHERE task_id=? AND state='Running'").run(now(), taskId);
  }

  interruptRun(runId: string, reason: string): void {
    const run = this.getRunRequired(runId);
    if (run.state !== "Running") return;
    this.db.prepare(`UPDATE runs SET state='Interrupted',effects=?,cleanup_state=?,failure_resolution='unresolved',
      error_json=?,stop_requested_at=COALESCE(stop_requested_at,?),ended_at=? WHERE id=?`)
      .run(run.side_effects === "none" ? "none" : "unknown", run.side_effects === "none" ? "clear" : "unknown",
        JSON.stringify({ message: reason }), now(), now(), runId);
  }

  recordDelivery(runId: string, state: "started" | "acknowledged" | "unknown"): void {
    const run = this.getRunRequired(runId);
    if (!run.delivery_id) throw new Error("Run has no delivery identity");
    if (state === "started") {
      this.assertResponse(runId);
      if (run.delivery_state !== "unsent") throw new Error("delivery cannot be sent again");
    } else if (run.delivery_state !== "started" && run.delivery_state !== "unknown" && run.delivery_state !== state) {
      throw new Error("invalid delivery observation");
    }
    this.db.prepare("UPDATE runs SET delivery_state=? WHERE id=?").run(state, runId);
  }

  finishResponse(runId: string, payload: {
    disposition: "continue" | "wait" | "fail"; summary: string; instruction_seq: number; wake_seq: number;
    wait_for?: string[]; completion?: CompletionEvidence[];
  }): RunRecord {
    return this.transaction(() => {
      const run = this.assertResponse(runId);
      if (payload.disposition === "wait") {
        if (!payload.wait_for?.length) throw new Error("wait requires a pending Run or Request");
        for (const id of payload.wait_for) {
          const child = this.getRun(id);
          const request = this.db.prepare("SELECT * FROM master_requests WHERE id=?").get(id) as Row | undefined;
          if (!(child?.parent_run_id === run.id && ["Pending", "Running"].includes(child.state))
            && !(request?.task_id === run.task_id && request.state === "Pending")) throw new Error("invalid response wait target");
        }
      }
      this.acknowledgeResponseContext(run.id, payload.instruction_seq, payload.wake_seq);
      const result = this.recordRunResult(run.id, { state: "Completed", effects: "none", cleanup_state: "clear", result: payload });
      this.addResultMessage(run.task_id, payload.summary, run.id);
      if (payload.disposition === "fail") {
        this.cancelTask(run.task_id);
        this.db.prepare("UPDATE tasks SET state='Failed',result_summary=? WHERE id=?").run(payload.summary, run.task_id);
      }
      return result;
    });
  }

  refineTaskDefinition(taskId: string, title: string, completionCriteria: CompletionCriterion[], master = false): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task definition is immutable");
    if (!title.trim() || !Array.isArray(completionCriteria) || completionCriteria.length === 0) throw new Error("title and completion criteria are required");
    const ids = new Set<string>();
    for (const criterion of completionCriteria) {
      if (!criterion.id || !criterion.text?.trim() || typeof criterion.required !== "boolean"
        || !(criterion.verification_kind === null || typeof criterion.verification_kind === "string" && criterion.verification_kind.trim())) throw new Error("invalid completion criterion");
      if (ids.has(criterion.id)) throw new Error("duplicate completion criterion id");
      ids.add(criterion.id);
    }
    if (!master) for (const prior of task.completion_criteria) {
      const next = completionCriteria.find(item => item.id === prior.id);
      if (!next || (prior.required && !next.required) || next.verification_kind !== prior.verification_kind
        || !next.text.includes(prior.text)) throw new Error("required completion criteria cannot be removed or weakened");
    }
    this.db
      .prepare(`
        UPDATE tasks
        SET title=?, completion_criteria=?, revision=revision+1, updated_at=?
        WHERE id=?
      `)
      .run(title.trim(), JSON.stringify(completionCriteria), now(), taskId);
    return this.getTaskRequired(taskId);
  }

  acknowledgeTaskContext(taskId: string, instructionSeq: number, wakeSeq: number): TaskRecord {
    const task = this.getTaskRequired(taskId);
    if (!Number.isSafeInteger(instructionSeq) || !Number.isSafeInteger(wakeSeq)) {
      throw new Error("handled sequences must be integers");
    }
    const maxInstructionSeq = Number(
      (this.db
        .prepare("SELECT COALESCE(MAX(seq), 0) AS max_seq FROM messages WHERE conversation_id=? AND sender='master'")
        .get(task.conversation_id) as Row).max_seq,
    );
    if (instructionSeq < task.handled_instruction_seq || instructionSeq > maxInstructionSeq) {
      throw new Error("invalid handled instruction sequence");
    }
    if (wakeSeq < task.handled_wake_seq || wakeSeq > task.wake_seq) {
      throw new Error("invalid handled wake sequence");
    }
    this.db
      .prepare(`
        UPDATE tasks
        SET handled_instruction_seq=?, handled_wake_seq=?, revision=revision+1, updated_at=?
        WHERE id=?
      `)
      .run(instructionSeq, wakeSeq, now(), taskId);
    return this.getTaskRequired(taskId);
  }

  completeTask(taskId: string, resultSummary: string, evidence?: CompletionEvidence[], response?: {
    run_id: string; instruction_seq: number; wake_seq: number;
  }): TaskRecord {
    return this.transaction(() => {
      if (response) {
        this.assertResponse(response.run_id, taskId);
        this.acknowledgeResponseContext(response.run_id, response.instruction_seq, response.wake_seq);
      }
      const claims = evidence ?? this.latestCompletionEvidence(taskId);
      return this.finalizeTask(taskId, resultSummary, claims, response?.run_id);
    });
  }

  latestCompletionEvidence(taskId: string): CompletionEvidence[] {
    const last = this.listRuns(taskId).filter(run => run.kind === "response" && run.state === "Completed").at(-1);
    return JSON.parse(last?.result_json ?? "{}").value?.completion ?? [];
  }

  private finalizeTask(taskId: string, resultSummary: string, evidence: CompletionEvidence[], responseId?: string): TaskRecord {
    const summary = resultSummary.trim();
    if (!summary) throw new Error("result summary is required");
    const task = this.getTaskRequired(taskId);
    if (task.state !== "Running") throw new Error("only Running task can complete");
    if (!task.initial_message_id) throw new Error("task has no initial instruction");
    if (!task.completion_criteria.length) throw new Error("task has no completion criteria");

    const maxInstructionSeq = Number(
      (this.db
        .prepare("SELECT COALESCE(MAX(seq), 0) AS max_seq FROM messages WHERE conversation_id=? AND sender='master'")
        .get(task.conversation_id) as Row).max_seq,
    );
    if (task.handled_instruction_seq < maxInstructionSeq || task.handled_wake_seq < task.wake_seq) {
      throw new Error("task has unhandled Master instructions");
    }

    const completedResponses = Number(
      (this.db
        .prepare("SELECT COUNT(*) AS count FROM runs WHERE task_id=? AND kind='response' AND state='Completed'")
        .get(taskId) as Row).count,
    );
    if (completedResponses === 0 && !responseId) throw new Error("task has no completed response");

    const activeRuns = Number(
      (this.db
        .prepare("SELECT COUNT(*) AS count FROM runs WHERE task_id=? AND state IN ('Pending','Running') AND id!=?")
        .get(taskId, responseId ?? "") as Row).count,
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
          WHERE task_id=? AND (effects='unknown' OR cleanup_state!='clear'
            OR delivery_state IN ('started','unknown'))
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

    this.validateCompletionEvidence(task, evidence);
    if (responseId) this.recordRunResult(responseId, { state: "Completed", effects: "none", cleanup_state: "clear",
      result: { disposition: "complete", summary, completion: evidence } });
    this.addResultMessage(taskId, summary, responseId ?? null);

    const timestamp = now();
    this.db
      .prepare(`
        UPDATE tasks
        SET state='Completed', revision=revision+1, control_epoch=control_epoch+1,
            result_summary=?, ended_at=?, updated_at=?
        WHERE id=?
      `)
      .run(summary, timestamp, timestamp, taskId);
    return this.getTaskRequired(taskId);
  }

  private validateCompletionEvidence(task: TaskRecord, evidence: CompletionEvidence[]): void {
    if (!Array.isArray(evidence)) throw new Error("invalid completion evidence");
    for (const criterion of task.completion_criteria.filter(item => item.required)) {
      const claim = evidence.find(item => item.criterion_id === criterion.id);
      if (!claim?.artifact_ref || !claim.fingerprint) throw new Error(`missing completion evidence: ${criterion.id}`);
      const artifact = this.db.prepare("SELECT * FROM artifact_references WHERE task_id=? AND ref=? ORDER BY observed_at DESC, rowid DESC LIMIT 1")
        .get(task.id, claim.artifact_ref) as Row | undefined;
      const message = this.db.prepare(`SELECT * FROM messages WHERE id=? AND conversation_id=?`).get(claim.artifact_ref, task.conversation_id) as Row | undefined;
      if (artifact ? artifact.fingerprint !== claim.fingerprint
        : !(message && !criterion.verification_kind && fingerprint(message.content) === claim.fingerprint)) {
        throw new Error(`artifact evidence does not match the current content: ${criterion.id}`);
      }
      if (criterion.verification_kind) {
        const verification = claim.verification_run_id ? this.getRun(claim.verification_run_id) : null;
        const observed = JSON.parse(verification?.result_json ?? "{}").verification as VerificationResult | undefined;
        if (!verification || verification.task_id !== task.id || verification.kind !== "action" || verification.state !== "Completed"
          || verification.effects === "unknown" || verification.cleanup_state !== "clear" || !observed?.passed
          || observed.kind !== criterion.verification_kind || observed.artifact_ref !== claim.artifact_ref || observed.fingerprint !== claim.fingerprint) {
          throw new Error(`required verification is not satisfied: ${criterion.id}`);
        }
      }
    }
  }

  resolveRunFailure(
    taskId: string,
    runId: string,
    resolution: "recovered" | "not_needed",
    evidence: string,
    recoveryRunId?: string,
  ): RunRecord {
    const run = this.getRunRequired(runId);
    if (run.task_id !== taskId) throw new Error("run belongs to another task");
    if (run.state !== "Failed" && run.state !== "Interrupted") {
      throw new Error("only failed or interrupted run can be resolved");
    }
    if (run.failure_resolution !== "unresolved") {
      throw new Error("run failure is already resolved");
    }
    if (!evidence.trim()) throw new Error("resolution evidence is required");
    const failedVerification = JSON.parse(run.result_json ?? "{}").verification as VerificationResult | undefined;
    if (resolution === "not_needed" && failedVerification && this.getTaskRequired(taskId).completion_criteria.some(
      item => item.required && item.verification_kind === failedVerification.kind)) throw new Error("required verification failure cannot be dismissed");
    if (resolution === "recovered") {
      const recovery = recoveryRunId ? this.getRun(recoveryRunId) : null;
      if (!recovery || recovery.task_id !== taskId || recovery.id === runId || recovery.state !== "Completed"
        || recovery.effects === "unknown" || recovery.cleanup_state !== "clear" || !recovery.result_json) throw new Error("recovered requires a successful recovery Run in the same Task");
      if (failedVerification) {
        const checked = JSON.parse(recovery.result_json).verification as VerificationResult | undefined;
        if (!checked?.passed || checked.kind !== failedVerification.kind || checked.artifact_ref !== failedVerification.artifact_ref) throw new Error("recovery Run does not verify the failed target");
      }
    }
    if (run.effects === "unknown" || run.cleanup_state !== "clear"
      || run.delivery_state === "started" || run.delivery_state === "unknown") {
      throw new Error("Run effects, cleanup and delivery must be reconciled by the Adapter before resolving failure");
    }

    const timestamp = now();
    this.db
      .prepare(`
        UPDATE runs
        SET failure_resolution=?, resolution_note=?
        WHERE id=?
      `)
      .run(resolution, JSON.stringify({ evidence: evidence.trim(), recovery_run_id: recoveryRunId ?? null }), runId);
    this.db
      .prepare("UPDATE tasks SET revision=revision+1, updated_at=? WHERE id=?")
      .run(timestamp, taskId);
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
      revision: Number(this.getMetadata("snapshot_revision") ?? 0),
      tasks,
      pending_requests: requests,
      runs,
      messages,
      residents,
      settings: this.getSettings(),
      artifacts: this.db.prepare("SELECT * FROM artifact_references ORDER BY observed_at").all(),
    };
  }
}
