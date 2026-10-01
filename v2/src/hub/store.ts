import { randomUUID } from "node:crypto";
import type { HubCommandEnvelope } from "../shared/types.js";
import { conversationId, type ConversationBinding, type HoloChatBinding } from "../shared/holo.js";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, isAbsolute, join } from "node:path";
import { backup as sqliteBackup, DatabaseSync } from "node:sqlite";

import { fingerprint } from "../shared/stable.js";
import { DEFAULT_SETTINGS, type HubSettings } from "../shared/settings.js";
import { HubError } from "../shared/errors.js";
import { validatePersonaPath } from "./persona.js";
import type { ArtifactReference, CompletionCriterion, CompletionEvidence, VerificationResult } from "../shared/types.js";
import type {
  CreateRunInput,
  ChatContext,
  ChatMessageRecord,
  ChatResponseRecord,
  ResidentChatContext,
  ConversationRecord,
  HoloTurnRecord,
  RunEffects,
  RunRecord,
  RunSideEffects,
  RunState,
  ResidentConfiguration,
  ResidentRecord,
  TaskRecord,
  TaskState,
} from "../shared/types.js";

type Row = Record<string, unknown>;

export interface HoloChatPromptMemory {
  conversation_id: string;
  seen_message_ids: string[];
  persona_fingerprint: string | null;
}

export const HUB_SCHEMA_VERSION = 17;
const TERMINAL_TASK_STATES = new Set<TaskState>(["Completed", "Failed", "Cancelled"]);
const TERMINAL_RUN_STATES = new Set<RunState>(["Completed", "Failed", "Cancelled", "Interrupted"]);

function now(): string {
  return new Date().toISOString();
}

function bool(value: unknown): boolean {
  return Number(value) !== 0;
}

function chatMessageFromRow(row: Row): ChatMessageRecord {
  return {
    id: String(row.id), conversation_id: String(row.conversation_id), seq: Number(row.seq),
    sender: String(row.sender), content: String(row.content), created_at: String(row.created_at),
    audience: JSON.parse(String(row.audience_json)),
    reply_to_message_id: row.reply_to_message_id === null ? null : String(row.reply_to_message_id),
  };
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
    turn_id: row.turn_id === null || row.turn_id === undefined ? null : String(row.turn_id),
    capability_id: String(row.capability_id),
    operation: String(row.operation),
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
    resources_json: String(row.resources_json),
    settings_json: String(row.settings_json),
    stop_requested_at: row.stop_requested_at === null ? null : String(row.stop_requested_at),
    created_at: String(row.created_at),
    started_at: row.started_at === null ? null : String(row.started_at),
    ended_at: row.ended_at === null ? null : String(row.ended_at),
  };
}

function turnFromRow(row: Row): HoloTurnRecord {
  return {
    id: String(row.id),
    task_id: String(row.task_id),
    control_epoch: Number(row.control_epoch),
    instruction_seq: Number(row.instruction_seq),
    await_master: bool(row.await_master),
    completion_summary: row.completion_summary === null || row.completion_summary === undefined ? null : String(row.completion_summary),
    settings_json: String(row.settings_json),
    created_at: String(row.created_at),
    ended_at: row.ended_at === null ? null : String(row.ended_at),
    end_reason: row.end_reason === null ? null : String(row.end_reason),
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
    let version = Number((this.db.prepare("PRAGMA user_version").get() as Row).user_version ?? 0);
    if (version > HUB_SCHEMA_VERSION) {
      throw new Error(`unsupported hub schema version: ${version}`);
    }
    if (version === HUB_SCHEMA_VERSION) return;

    if (version === 0) {
      this.db.exec(`
        CREATE TABLE residents (
          id TEXT PRIMARY KEY,
          display_name TEXT NOT NULL,
          role TEXT,
          persona_path TEXT,
          capability_id TEXT,
          model TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE conversations (
          id TEXT PRIMARY KEY,
          task_id TEXT UNIQUE,
          kind TEXT NOT NULL DEFAULT 'task' CHECK(kind IN ('task','say','whisper')),
          resident_id TEXT REFERENCES residents(id),
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
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          started_at TEXT,
          ended_at TEXT,
          result_summary TEXT
        );

        CREATE TABLE holo_turns (
          id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL REFERENCES tasks(id),
          control_epoch INTEGER NOT NULL,
          instruction_seq INTEGER NOT NULL,
          await_master INTEGER NOT NULL DEFAULT 0 CHECK(await_master IN (0,1)),
          completion_summary TEXT,
          settings_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          ended_at TEXT,
          end_reason TEXT
        );

        CREATE UNIQUE INDEX holo_turns_one_active
          ON holo_turns(task_id)
          WHERE ended_at IS NULL;

        CREATE TABLE messages (
          id TEXT PRIMARY KEY,
          conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          seq INTEGER NOT NULL,
          sender TEXT NOT NULL,
          content TEXT NOT NULL,
          created_at TEXT NOT NULL,
          run_id TEXT REFERENCES runs(id),
          turn_id TEXT REFERENCES holo_turns(id),
          request_id TEXT REFERENCES master_requests(id),
          audience_json TEXT NOT NULL DEFAULT '[]',
          reply_to_message_id TEXT REFERENCES messages(id),
          UNIQUE(conversation_id, seq)
        );

        CREATE TABLE runs (
          id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL REFERENCES tasks(id),
          capability_id TEXT NOT NULL,
          operation TEXT NOT NULL,
          turn_id TEXT REFERENCES holo_turns(id),
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
          resources_json TEXT NOT NULL DEFAULT '[]',
          settings_json TEXT NOT NULL DEFAULT '{}',
          stop_requested_at TEXT,
          created_at TEXT NOT NULL,
          started_at TEXT,
          ended_at TEXT
        );

        CREATE TABLE master_requests (
          id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL REFERENCES tasks(id),
          run_id TEXT REFERENCES runs(id),
          turn_id TEXT REFERENCES holo_turns(id),
          kind TEXT NOT NULL CHECK(kind IN ('approval','input')),
          state TEXT NOT NULL CHECK(state IN ('Pending','Resolved','Cancelled')),
          revision INTEGER NOT NULL,
          prompt TEXT NOT NULL,
          proposal_json TEXT,
          proposal_fingerprint TEXT,
          answer_json TEXT,
          created_at TEXT NOT NULL,
          resolved_at TEXT,
          answered_by TEXT
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
          updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX bindings_unique_conversation ON provider_bindings(provider, external_conversation_id)
          WHERE external_conversation_id IS NOT NULL;

        CREATE TABLE artifact_references (
          task_id TEXT NOT NULL REFERENCES tasks(id),
          run_id TEXT NOT NULL REFERENCES runs(id),
          ref TEXT NOT NULL,
          fingerprint TEXT NOT NULL,
          ownership TEXT NOT NULL CHECK(ownership IN ('temporary','project','shared','recovery')),
          observed_at TEXT NOT NULL,
          PRIMARY KEY(task_id, run_id, ref)
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

        PRAGMA user_version = 17;
      `);
      this.initializeChatSchema();
      return;
    }

    if (version === 13) {
      this.db.exec(`ALTER TABLE holo_turns ADD COLUMN instruction_seq INTEGER NOT NULL DEFAULT 0;`);
      version = 14;
    }
    if (version === 14) {
      this.db.exec(`ALTER TABLE holo_turns ADD COLUMN await_master INTEGER NOT NULL DEFAULT 0 CHECK(await_master IN (0,1));`);
      this.db.prepare(`
        UPDATE holo_turns SET await_master=1
        WHERE end_reason='assistant' AND id IN (
          SELECT turn_id FROM master_requests
          WHERE kind='input' AND state='Pending' AND turn_id IS NOT NULL
        )
      `).run();
      const answeredInputs = this.db.prepare(`
        SELECT id,answer_json FROM master_requests
        WHERE kind='input' AND state='Resolved' AND answer_json IS NOT NULL
      `).all() as Row[];
      for (const request of answeredInputs) {
        let text: string | null = null;
        try {
          const answer = JSON.parse(String(request.answer_json)) as Row;
          if (typeof answer.text === "string" && answer.text.trim()) text = answer.text.trim();
        } catch {}
        if (text) {
          this.db.prepare("UPDATE messages SET sender='master',content=?,request_id=NULL WHERE request_id=?")
            .run(text, String(request.id));
        }
      }
      this.db.prepare(`UPDATE messages SET request_id=NULL WHERE request_id IN (
        SELECT id FROM master_requests WHERE kind='input'
      )`).run();
      this.db.prepare("DELETE FROM master_requests WHERE kind='input'").run();
      this.db.exec("PRAGMA user_version = 15;");
      version = 15;
    }
    if (version === 15) {
      this.db.exec("ALTER TABLE holo_turns ADD COLUMN completion_summary TEXT;");
      this.db.exec("PRAGMA user_version = 16;");
      version = 16;
    }
    if (version === 16) {
      this.db.exec(`
        ALTER TABLE residents ADD COLUMN role TEXT;
        ALTER TABLE residents ADD COLUMN persona_path TEXT;
        ALTER TABLE residents ADD COLUMN capability_id TEXT;
        ALTER TABLE residents ADD COLUMN model TEXT;
        ALTER TABLE conversations ADD COLUMN kind TEXT NOT NULL DEFAULT 'task' CHECK(kind IN ('task','say','whisper'));
        ALTER TABLE conversations ADD COLUMN resident_id TEXT REFERENCES residents(id);
        ALTER TABLE messages ADD COLUMN audience_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE messages ADD COLUMN reply_to_message_id TEXT REFERENCES messages(id);
        PRAGMA user_version = 17;
      `);
      this.initializeChatSchema();
      return;
    }
    throw new Error(`unsupported hub schema migration path: ${version}`);
  }

  private initializeChatSchema(): void {
    this.db.exec(`
      CREATE UNIQUE INDEX conversations_one_say ON conversations(kind) WHERE kind='say';
      CREATE UNIQUE INDEX conversations_one_whisper ON conversations(resident_id) WHERE kind='whisper';
      CREATE UNIQUE INDEX messages_one_chat_reply ON messages(reply_to_message_id,sender) WHERE reply_to_message_id IS NOT NULL;
      CREATE TABLE chat_responses (
        message_id TEXT NOT NULL REFERENCES messages(id),
        resident_id TEXT NOT NULL REFERENCES residents(id),
        state TEXT NOT NULL CHECK(state IN ('pending','running','completed','failed','interrupted')),
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(message_id,resident_id)
      );
    `);
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

  listResidents(): ResidentRecord[] {
    return this.db.prepare("SELECT * FROM residents ORDER BY created_at,rowid")
      .all() as unknown as ResidentRecord[];
  }

  getResident(id: string): ResidentRecord | null {
    return (this.db.prepare("SELECT * FROM residents WHERE id=?").get(id) as unknown as ResidentRecord) ?? null;
  }

  private validateResidentConfiguration(input: Partial<ResidentConfiguration>): void {
    const limits: Record<string, number> = { display_name: 120, role: 1000, persona_path: 4096, capability_id: 128, model: 128 };
    for (const [key, value] of Object.entries(input)) {
      if (!Object.hasOwn(limits, key)) throw new HubError("invalid", `invalid Resident field: ${key}`);
      if (value === null && key !== "display_name") continue;
      if (typeof value !== "string" || !value.trim() || value.length > limits[key]! || value.includes("\0")) {
        throw new HubError("invalid", `invalid ${key}`);
      }
      if (key === "persona_path") validatePersonaPath(value);
    }
  }

  createResident(id: string, configuration: ResidentConfiguration): ResidentRecord {
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(id) || ["master", "control"].includes(id)) {
      throw new HubError("invalid", "invalid Resident ID");
    }
    this.validateResidentConfiguration(configuration);
    if (!configuration.display_name) throw new HubError("invalid", "missing display_name");
    if (this.residentExists(id)) throw new HubError("invalid", "Resident ID already exists");
    const timestamp = now();
    this.db.prepare(`INSERT INTO residents(id,display_name,role,persona_path,capability_id,model,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?)`).run(id, configuration.display_name, configuration.role ?? null,
      configuration.persona_path ?? null, configuration.capability_id ?? null, configuration.model ?? null, timestamp, timestamp);
    return this.getResident(id)!;
  }

  updateResident(id: string, configuration: Partial<ResidentConfiguration>): ResidentRecord {
    const existing = this.getResident(id);
    if (!existing) throw new HubError("invalid", "Resident not found");
    this.validateResidentConfiguration(configuration);
    if (Object.keys(configuration).length === 0) throw new HubError("invalid", "Resident update is empty");
    const updated = { ...existing, ...configuration };
    this.db.prepare(`UPDATE residents SET display_name=?,role=?,persona_path=?,capability_id=?,model=?,updated_at=? WHERE id=?`)
      .run(updated.display_name, updated.role, updated.persona_path, updated.capability_id, updated.model, now(), id);
    return this.getResident(id)!;
  }

  private requireChatResident(id: string): ResidentRecord {
    const resident = this.getResident(id);
    if (!resident || ["master", "control"].includes(id)) {
      throw new HubError("invalid", "Resident is unavailable for normal conversation");
    }
    return resident;
  }

  listConversations(): ConversationRecord[] {
    return this.db.prepare("SELECT * FROM conversations ORDER BY created_at,rowid").all() as unknown as ConversationRecord[];
  }

  private requireChatConversation(id: string, residentId?: string): ConversationRecord {
    const conversation = this.db.prepare("SELECT * FROM conversations WHERE id=?").get(id) as unknown as ConversationRecord | undefined;
    if (!conversation || conversation.kind === "task" || conversation.task_id !== null) {
      throw new HubError("invalid", "normal Conversation not found");
    }
    if (residentId) {
      this.requireChatResident(residentId);
      if (conversation.kind === "whisper" && conversation.resident_id !== residentId) {
        throw new HubError("unauthorized", "Resident cannot read another Whisper");
      }
    }
    return conversation;
  }

  addChatMasterMessage(channel: "say" | "whisper", residentId: string | undefined, content: string): {
    conversation_id: string; message_id: string; seq: number; message: ChatMessageRecord; conversation: ConversationRecord; audience: string[];
  } {
    return this.transaction(() => {
      if (!content.trim() || content.length > 32 * 1024) throw new HubError("invalid", "invalid normal conversation message");
      if (channel !== "say" && channel !== "whisper") throw new HubError("invalid", "invalid channel");
      if (channel === "say" && residentId !== undefined) throw new HubError("invalid", "Say has no single Resident target");
      const audience = channel === "whisper"
        ? [this.requireChatResident(residentId ?? "").id]
        : this.listResidents().filter(resident => !["master", "control"].includes(resident.id)).map(resident => resident.id);
      if (audience.length === 0) throw new HubError("unavailable", "no Resident is available for Say");
      let conversation = this.db.prepare(channel === "say"
        ? "SELECT * FROM conversations WHERE kind='say'"
        : "SELECT * FROM conversations WHERE kind='whisper' AND resident_id=?")
        .get(...(channel === "say" ? [] : [residentId!])) as unknown as ConversationRecord | undefined;
      if (!conversation) {
        const timestamp = now();
        const id = randomUUID();
        this.db.prepare("INSERT INTO conversations(id,kind,resident_id,created_at,updated_at) VALUES (?,?,?,?,?)")
          .run(id, channel, channel === "whisper" ? residentId! : null, timestamp, timestamp);
        conversation = this.requireChatConversation(id);
      }
      const message = this.insertChatMessage(conversation.id, "master", content, audience, null);
      const statement = this.db.prepare(`INSERT INTO chat_responses(message_id,resident_id,state,created_at,updated_at)
        VALUES (?,?,'pending',?,?)`);
      for (const id of audience) statement.run(message.id, id, message.created_at, message.created_at);
      return { conversation_id: conversation.id, message_id: message.id, seq: message.seq, message,
        conversation: this.requireChatConversation(conversation.id), audience };
    });
  }

  private insertChatMessage(conversationId: string, sender: string, content: string, audience: string[], replyTo: string | null): ChatMessageRecord {
    const id = randomUUID();
    const timestamp = now();
    const seq = Number((this.db.prepare("SELECT COALESCE(MAX(seq),0)+1 AS seq FROM messages WHERE conversation_id=?").get(conversationId) as Row).seq);
    this.db.prepare(`INSERT INTO messages(id,conversation_id,seq,sender,content,audience_json,reply_to_message_id,created_at)
      VALUES (?,?,?,?,?,?,?,?)`).run(id, conversationId, seq, sender, content, JSON.stringify(audience), replyTo, timestamp);
    this.db.prepare("UPDATE conversations SET updated_at=? WHERE id=?").run(timestamp, conversationId);
    return { id, conversation_id: conversationId, seq, sender, content, audience, reply_to_message_id: replyTo, created_at: timestamp };
  }

  getChatContext(conversationId: string, residentId: string, maxMessages = 40, beforeOrAtSeq?: number): ChatContext {
    if (!Number.isSafeInteger(maxMessages) || maxMessages < 1 || maxMessages > 100) throw new HubError("invalid", "invalid max_messages");
    if (beforeOrAtSeq !== undefined && (!Number.isSafeInteger(beforeOrAtSeq) || beforeOrAtSeq < 1)) {
      throw new HubError("invalid", "invalid Chat context sequence");
    }
    const conversation = this.requireChatConversation(conversationId, residentId);
    const ceiling = beforeOrAtSeq ?? Number.MAX_SAFE_INTEGER;
    const rows = this.db.prepare(`SELECT * FROM messages WHERE conversation_id=?
      AND (seq<=? OR EXISTS (
        SELECT 1 FROM messages AS original WHERE original.id=messages.reply_to_message_id
          AND original.conversation_id=messages.conversation_id AND original.sender='master' AND original.seq<=?
      ))
      AND EXISTS (SELECT 1 FROM json_each(messages.audience_json) WHERE value=?) ORDER BY seq DESC LIMIT ?`)
      .all(conversationId, ceiling, ceiling, residentId, maxMessages) as Row[];
    return { conversation, resident: this.requireChatResident(residentId), messages: rows.reverse().map(chatMessageFromRow) };
  }

  /** Only this person's received normal speech, across channels, at the current input horizon. */
  getResidentChatContext(messageId: string, residentId: string, maxMessages = 40): ResidentChatContext {
    if (!Number.isSafeInteger(maxMessages) || maxMessages < 1 || maxMessages > 100) throw new HubError("invalid", "invalid max_messages");
    const input = this.db.prepare("SELECT rowid AS horizon,* FROM messages WHERE id=? AND sender='master'")
      .get(messageId) as Row | undefined;
    if (!input || !(JSON.parse(String(input.audience_json)) as string[]).includes(residentId)) {
      throw new HubError("unauthorized", "Resident did not receive this message");
    }
    const conversation = this.requireChatConversation(String(input.conversation_id), residentId);
    const rows = this.db.prepare(`SELECT messages.*,conversations.kind AS channel FROM messages
      JOIN conversations ON conversations.id=messages.conversation_id
      WHERE conversations.kind IN ('say','whisper') AND conversations.task_id IS NULL
      AND (conversations.kind='say' OR conversations.resident_id=?)
      AND EXISTS (SELECT 1 FROM json_each(messages.audience_json) WHERE value=?)
      AND (messages.rowid<=? OR EXISTS (
        SELECT 1 FROM messages AS original WHERE original.id=messages.reply_to_message_id
          AND original.sender='master' AND original.rowid<=?
      )) ORDER BY messages.rowid DESC LIMIT ?`)
      .all(residentId, residentId, Number(input.horizon), Number(input.horizon), maxMessages) as Row[];
    return { conversation, resident: this.requireChatResident(residentId),
      messages: rows.reverse().map(row => ({ ...chatMessageFromRow(row), channel: String(row.channel) as "say" | "whisper" })) };
  }

  getHoloChatBinding(): HoloChatBinding {
    const url = this.getMetadata("holo_chat_url");
    return { external_conversation_id: url ? conversationId(url) : null, external_url: url };
  }

  getHoloChatPromptMemory(): HoloChatPromptMemory | null {
    try {
      const memory = JSON.parse(this.getMetadata("holo_chat_prompt_memory") ?? "null") as HoloChatPromptMemory | null;
      if (!memory || typeof memory.conversation_id !== "string" || !memory.conversation_id
        || memory.conversation_id !== this.getHoloChatBinding().external_conversation_id
        || !Array.isArray(memory.seen_message_ids) || memory.seen_message_ids.length > 100
        || memory.seen_message_ids.some(id => typeof id !== "string" || !id || id.length > 128)
        || (memory.persona_fingerprint !== null && !/^[a-f0-9]{64}$/.test(memory.persona_fingerprint))) return null;
      return memory;
    } catch { return null; }
  }

  confirmHoloChatConversation(messageId: string, url: string,
    promptMemory?: Omit<HoloChatPromptMemory, "conversation_id">): HoloChatBinding {
    return this.transaction(() => {
      const response = this.getChatResponse(messageId, "holo");
      if (!response || response.state !== "running") throw new HubError("stale", "Holo Chat response is no longer running");
      const externalId = conversationId(url);
      if (!externalId) throw new HubError("invalid", "invalid ChatGPT Conversation");
      const binding = this.getHoloChatBinding();
      if (binding.external_conversation_id && binding.external_conversation_id !== externalId) {
        throw new HubError("conflict", "Holo normal conversation changed during delivery");
      }
      if (this.db.prepare("SELECT 1 FROM provider_bindings WHERE provider='chatgpt' AND external_conversation_id=?").get(externalId)) {
        throw new HubError("conflict", "Holo normal conversation belongs to a Task");
      }
      this.db.prepare(`INSERT INTO metadata(key,value,updated_at) VALUES ('holo_chat_url',?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at`).run(url, now());
      if (promptMemory) {
        this.db.prepare(`INSERT INTO metadata(key,value,updated_at) VALUES ('holo_chat_prompt_memory',?,?)
          ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at`)
          .run(JSON.stringify({ ...promptMemory, conversation_id: externalId }), now());
      }
      return this.getHoloChatBinding();
    });
  }

  addChatAssistantMessage(conversationId: string, residentId: string, replyToMessageId: string, content: string): ChatMessageRecord {
    return this.transaction(() => {
      this.requireChatConversation(conversationId, residentId);
      const input = this.db.prepare("SELECT * FROM messages WHERE id=? AND conversation_id=? AND sender='master'")
        .get(replyToMessageId, conversationId) as Row | undefined;
      if (!input || !(JSON.parse(String(input.audience_json)) as string[]).includes(residentId)) {
        throw new HubError("unauthorized", "Resident cannot reply to this message");
      }
      const existing = this.db.prepare("SELECT * FROM messages WHERE reply_to_message_id=? AND sender=?")
        .get(replyToMessageId, residentId) as Row | undefined;
      if (existing) {
        if (existing.content !== content) throw new HubError("invalid", "Chat reply conflict");
        return chatMessageFromRow(existing);
      }
      if (!content.trim() || content.length > 128 * 1024) throw new HubError("invalid", "invalid Chat reply");
      const response = this.getChatResponse(replyToMessageId, residentId);
      if (!response || response.state !== "running") throw new HubError("stale", "Chat response is no longer running");
      const message = this.insertChatMessage(conversationId, residentId, content, JSON.parse(String(input.audience_json)), replyToMessageId);
      this.db.prepare("UPDATE chat_responses SET state='completed',error=NULL,updated_at=? WHERE message_id=? AND resident_id=?")
        .run(message.created_at, replyToMessageId, residentId);
      return message;
    });
  }

  getChatResponse(messageId: string, residentId: string): ChatResponseRecord | null {
    return (this.db.prepare("SELECT * FROM chat_responses WHERE message_id=? AND resident_id=?")
      .get(messageId, residentId) as unknown as ChatResponseRecord) ?? null;
  }

  getChatMessage(id: string): ChatMessageRecord | null {
    const row = this.db.prepare("SELECT messages.* FROM messages JOIN conversations ON conversations.id=messages.conversation_id WHERE messages.id=? AND conversations.kind IN ('say','whisper')")
      .get(id) as Row | undefined;
    return row ? chatMessageFromRow(row) : null;
  }

  claimNextChatResponse(): ChatResponseRecord | null {
    return this.transaction(() => {
      const next = this.db.prepare("SELECT * FROM chat_responses WHERE state='pending' ORDER BY created_at,rowid LIMIT 1")
        .get() as unknown as ChatResponseRecord | undefined;
      if (!next) return null;
      this.db.prepare("UPDATE chat_responses SET state='running',updated_at=? WHERE message_id=? AND resident_id=?")
        .run(now(), next.message_id, next.resident_id);
      return this.getChatResponse(next.message_id, next.resident_id);
    });
  }

  completeChatResponse(messageId: string, residentId: string, content: string): ChatMessageRecord {
    const input = this.db.prepare("SELECT conversation_id FROM messages WHERE id=?").get(messageId) as Row | undefined;
    if (!input) throw new HubError("invalid", "Chat message not found");
    return this.addChatAssistantMessage(String(input.conversation_id), residentId, messageId, content);
  }

  failChatResponse(messageId: string, residentId: string, error: string): void {
    this.transaction(() => {
      const response = this.getChatResponse(messageId, residentId);
      if (!response || response.state !== "running") return;
      this.db.prepare("UPDATE chat_responses SET state='failed',error=?,updated_at=? WHERE message_id=? AND resident_id=?")
        .run(error.slice(0, 2000), now(), messageId, residentId);
    });
  }

  recoverChatResponses(): void {
    this.transaction(() => {
      this.db.prepare("UPDATE chat_responses SET state='interrupted',error=?,updated_at=? WHERE state IN ('pending','running')")
        .run("会話の応答が中断しました。自動で再送しません。", now());
    });
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
          id, title, resident_id, workspace_scope, state, resume_enabled, revision, control_epoch,
          conversation_id, handled_instruction_seq, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'Paused', 0, 1, 0, ?, 0, ?, ?)
      `)
      .run(taskId, "New Task", residentId, this.getSettings().value.workspace_scope, conversationId, timestamp, timestamp);

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

  private hasPendingHoloCompletion(taskId: string): boolean {
    return Boolean(this.db.prepare(
      "SELECT 1 FROM holo_turns WHERE task_id=? AND ended_at IS NULL AND completion_summary IS NOT NULL",
    ).get(taskId));
  }

  getMessage(id: string): { conversation_id: string; sender: string; content: string } | null {
    return this.db.prepare("SELECT conversation_id,sender,content FROM messages WHERE id=?").get(id) as
      { conversation_id: string; sender: string; content: string } | undefined ?? null;
  }

  private insertTaskMessage(task: TaskRecord, sender: string, content: string, requestId: string | null = null): {
    id: string; seq: number; timestamp: string;
  } {
    const seq = Number((this.db.prepare("SELECT COALESCE(MAX(seq),0)+1 AS seq FROM messages WHERE conversation_id=?")
      .get(task.conversation_id) as Row).seq);
    const id = randomUUID();
    const timestamp = now();
    this.db.prepare("INSERT INTO messages(id,conversation_id,seq,sender,content,created_at,request_id) VALUES (?,?,?,?,?,?,?)")
      .run(id, task.conversation_id, seq, sender, content, timestamp, requestId);
    this.db.prepare("UPDATE conversations SET updated_at=? WHERE id=?").run(timestamp, task.conversation_id);
    return { id, seq, timestamp };
  }

  addMasterMessage(taskId: string, content: string): { message_id: string; seq: number; task: TaskRecord } {
    if (!content.trim()) throw new Error("invalid empty message");
    const task = this.getTaskRequired(taskId);
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task cannot receive instructions");
    if (this.hasPendingHoloCompletion(taskId)) throw new Error("task is awaiting final assistant reply");

    const inserted = this.insertTaskMessage(task, "master", content);
    const messageId = inserted.id;
    const seq = inserted.seq;
    const timestamp = inserted.timestamp;

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
    return { message_id: messageId, seq, task: this.getTaskRequired(taskId) };
  }

  private updateDraftResident(taskId: string, residentId: string): TaskRecord {
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
    if (this.hasPendingHoloCompletion(taskId)) throw new Error("task is awaiting final assistant reply");
    if (changes.resident_id !== undefined) this.updateDraftResident(taskId, String(changes.resident_id));
    if (changes.title !== undefined || changes.completion_criteria !== undefined) this.refineTaskDefinition(taskId,
      String(changes.title ?? task.title), (changes.completion_criteria ?? task.completion_criteria) as CompletionCriterion[]);
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
          SET state='Cancelled', effects='none', cleanup_state='clear', ended_at=?
          WHERE task_id=? AND state='Pending'
        `)
        .run(timestamp, taskId);
      this.cancelObsoleteApprovals();
      this.endActiveHoloTurn(taskId, "Task paused");
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
      const timestamp = now();
      this.db
        .prepare(`
          UPDATE tasks
          SET state = 'Running', revision = revision + 1, control_epoch = control_epoch + 1,
              updated_at = ?
          WHERE id = ?
        `)
        .run(timestamp, taskId);
      if (this.latestInstructionSeq(task) <= task.handled_instruction_seq) {
        this.insertTaskMessage(task, "control", "Master resumed the Task.");
      }
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
        SET state='Cancelled', effects='none', cleanup_state='clear', ended_at=?
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
    this.endActiveHoloTurn(taskId, "Task cancelled");
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
    if (input.turn_id) {
      const turn = this.assertHoloTurn(input.turn_id);
      if (turn.task_id !== task.id || turn.control_epoch !== task.control_epoch) throw new Error("invalid Holo Turn");
    }

    const id = randomUUID();
    const timestamp = now();
    const inputJson = JSON.stringify(input.input);
    this.db
      .prepare(`
        INSERT INTO runs(
          id, task_id, capability_id, operation, turn_id, state,
          control_epoch, side_effects, input_json, input_fingerprint, workspace_scope,
          effects, cleanup_state, created_at
        ) VALUES (?, ?, ?, ?, ?, 'Pending', ?, ?, ?, ?, ?, 'none', 'clear', ?)
      `)
      .run(
        id,
        input.task_id,
        input.capability_id,
        input.operation,
        input.turn_id ?? null,
        input.control_epoch,
        sideEffects,
        inputJson,
        fingerprint(input.input),
        task.workspace_scope,
        timestamp,
      );
    this.db.prepare("UPDATE runs SET resources_json=?, settings_json=? WHERE id=?")
      .run(JSON.stringify(input.resources ?? []), JSON.stringify(this.getSettings().value), id);
    return this.getRunRequired(id);
  }

  getRun(id: string): RunRecord | null {
    const row = this.db.prepare("SELECT * FROM runs WHERE id=?").get(id) as Row | undefined;
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
    return this.transaction(() => { const run = this.saveRunResult(id, result); this.cancelObsoleteApprovals(); return run; });
  }

  private saveRunResult(id: string, result: Parameters<HubStore["recordRunResult"]>[1]): RunRecord {
    const run = this.getRunRequired(id);
    const timestamp = now();
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
      const nextCleanup = result.cleanup_state ?? (result.effects === "unknown" ? "unknown" : run.cleanup_state);
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
        SET state=?, result_json=?, error_json=?, effects=?, cleanup_state=?, ended_at=?
        WHERE id=?
      `)
      .run(
        result.state,
        resultJson,
        errorJson,
        result.effects,
        cleanupState,
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
          SET state='Cancelled', effects='none', cleanup_state='clear', ended_at=?
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
              ended_at=?
          WHERE state='Running'
        `)
        .run(timestamp);
      this.db.prepare("UPDATE holo_turns SET ended_at=?,end_reason='Hub restarted',completion_summary=NULL WHERE ended_at IS NULL").run(timestamp);
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
    turn_id?: string;
    kind: "approval";
    prompt: string;
    proposal?: unknown;
  }): { id: string; state: "Pending" } {
    const task = this.getTaskRequired(input.task_id);
    if (TERMINAL_TASK_STATES.has(task.state)) throw new Error("terminal task cannot create a request");
    if (!input.prompt.trim()) throw new Error("request prompt is required");

    if (input.turn_id) {
      const turn = this.assertHoloTurn(input.turn_id, task.id);
      if (turn.control_epoch !== task.control_epoch) throw new Error("stale Holo Turn");
    }

    let proposal = input.proposal;
    if (input.run_id) {
      const run = this.getRunRequired(input.run_id);
      if (run.task_id !== input.task_id) throw new Error("request run belongs to another task");
      if (input.kind === "approval") {
        if (run.state !== "Pending" || run.control_epoch !== task.control_epoch) {
          throw new Error("approval requires a current pending action Run");
        }
        proposal = this.approvalProposal(run);
        if (input.proposal !== undefined && fingerprint(input.proposal) !== fingerprint(proposal)) {
          throw new Error("approval proposal must match Run");
        }
      }
    }
    if (!input.run_id) throw new Error("approval requires a Run");

    const id = randomUUID();
    const timestamp = now();
    const proposalJson = proposal === undefined ? null : JSON.stringify(proposal);
    this.db
      .prepare(`
        INSERT INTO master_requests(
          id, task_id, run_id, turn_id, kind, state, revision, prompt,
          proposal_json, proposal_fingerprint, created_at
        ) VALUES (?, ?, ?, ?, ?, 'Pending', 1, ?, ?, ?, ?)
      `)
      .run(
        id,
        input.task_id,
        input.run_id ?? null,
        input.turn_id ?? null,
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
    if (request.kind !== "approval") throw new Error("unsupported legacy Master Request");
    if (typeof fields.approved !== "boolean") throw new Error("approval answer requires approved boolean");
    const timestamp = now();
    this.db
      .prepare(`
        UPDATE master_requests
        SET state='Resolved', revision=revision+1, answer_json=?, resolved_at=?, answered_by='master'
        WHERE id=?
      `)
      .run(JSON.stringify(answer), timestamp, requestId);
    if (fields.approved === false) {
      this.db.prepare(`
        UPDATE runs SET state='Cancelled', effects='none', cleanup_state='clear',
          ended_at=? WHERE id=? AND state='Pending'
      `).run(timestamp, String(request.run_id));
      this.cancelObsoleteApprovals();
    }
    this.insertTaskMessage(task, "control", `Approval resolved: ${requestId}`, requestId);
    this.db
      .prepare("UPDATE tasks SET revision=revision+1,updated_at=? WHERE id=?")
      .run(timestamp, task.id);
    return this.db.prepare("SELECT * FROM master_requests WHERE id=?").get(requestId) as Row;
  }

  private initializeSettings(): void {
    this.db.prepare("INSERT INTO settings(key,value_json,revision,updated_at) VALUES ('runtime',?,1,?) ON CONFLICT(key) DO NOTHING")
      .run(JSON.stringify(DEFAULT_SETTINGS), now());
  }

  getSettings(): { value: HubSettings; revision: number } {
    const row = this.db.prepare("SELECT * FROM settings WHERE key='runtime'").get() as Row;
    const stored = JSON.parse(String(row.value_json)) as Record<string, unknown>;
    const value = Object.fromEntries(Object.entries(DEFAULT_SETTINGS)
      .map(([key, initial]) => [key, Object.hasOwn(stored, key) ? stored[key] : initial])) as HubSettings;
    return { value, revision: Number(row.revision) };
  }

  updateSettings(value: Record<string, unknown>, revision: number): ReturnType<HubStore["getSettings"]> {
    const current = this.getSettings();
    if (current.revision !== revision) throw new HubError("stale", "stale settings revision", current);
    for (const [key, setting] of Object.entries(value)) {
      if (!Object.hasOwn(DEFAULT_SETTINGS, key)) throw new Error(`invalid setting: ${key}`);
      if (key === "holo_app_name") {
        if (setting !== null && (typeof setting !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(setting))) throw new Error("invalid Holo app name");
      } else if (key === "resident_avatars") {
        if (!setting || typeof setting !== "object" || Array.isArray(setting)) throw new Error("invalid resident avatars");
        const entries = Object.entries(setting);
        if (entries.length > 100 || entries.some(([id, path]) => !this.residentExists(id)
          || typeof path !== "string" || path.length > 4096 || path.includes("\0")
          || !isAbsolute(path) || path.startsWith("\\\\") || !path.toLowerCase().endsWith(".vrm"))) {
          throw new Error("invalid resident avatar path");
        }
      } else if (key === "workspace_scope") {
        if (setting !== null && (typeof setting !== "string" || !isAbsolute(setting) || setting.startsWith("\\\\"))) throw new Error("invalid workspace_scope");
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
    return (taskId
      ? this.db.prepare("SELECT * FROM runs WHERE task_id=? ORDER BY rowid").all(taskId)
      : this.db.prepare("SELECT * FROM runs ORDER BY rowid").all()
    ).map(row => runFromRow(row as Row));
  }

  listHoloTurns(taskId?: string): HoloTurnRecord[] {
    return (taskId
      ? this.db.prepare("SELECT * FROM holo_turns WHERE task_id=? ORDER BY rowid").all(taskId)
      : this.db.prepare("SELECT * FROM holo_turns ORDER BY rowid").all()
    ).map(row => turnFromRow(row as Row));
  }

  getHoloTurn(id: string): HoloTurnRecord | null {
    const row = this.db.prepare("SELECT * FROM holo_turns WHERE id=?").get(id) as Row | undefined;
    return row ? turnFromRow(row) : null;
  }

  private getHoloTurnRequired(id: string): HoloTurnRecord {
    const turn = this.getHoloTurn(id);
    if (!turn) throw new Error(`Holo Turn not found: ${id}`);
    return turn;
  }

  private latestInstructionSeq(task: TaskRecord): number {
    return Number((this.db.prepare("SELECT COALESCE(MAX(seq),0) AS seq FROM messages WHERE conversation_id=? AND sender IN ('master','control')")
      .get(task.conversation_id) as Row).seq);
  }

  private assignedInstructionSeq(task: TaskRecord): number {
    // A failed Turn still used this execution permission. Only an explicit new
    // control epoch, a new instruction, or Resume ON may authorize another Turn.
    return Number((this.db.prepare("SELECT COALESCE(MAX(instruction_seq),0) AS seq FROM holo_turns WHERE task_id=? AND control_epoch=?")
      .get(task.id, task.control_epoch) as Row).seq);
  }

  resourcesAvailable(resources: string[], exceptRunId?: string): boolean {
    return !this.listRuns().some(run => {
      if (run.id === exceptRunId || !(JSON.parse(run.resources_json) as string[]).some(resource => resources.includes(resource))) return false;
      return run.state === "Running" || run.effects === "unknown" || run.cleanup_state !== "clear";
    });
  }

  needsHoloTurn(taskId: string): boolean {
    const task = this.getTaskRequired(taskId);
    if (task.resident_id !== "holo" || task.state !== "Running" || !task.initial_message_id) return false;
    if (this.db.prepare("SELECT 1 FROM holo_turns WHERE task_id=? AND ended_at IS NULL").get(taskId)) return false;

    const runs = this.listRuns(taskId);
    if (runs.some(run => run.state === "Pending" || run.state === "Running"
      || run.effects === "unknown" || run.cleanup_state !== "clear")) return false;
    if (this.db.prepare("SELECT 1 FROM master_requests WHERE task_id=? AND state='Pending' AND kind='approval'").get(taskId)) return false;

    const latestInstructionSeq = this.latestInstructionSeq(task);
    const latestTurn = this.db.prepare(`
      SELECT instruction_seq,end_reason
      FROM holo_turns
      WHERE task_id=? AND ended_at IS NOT NULL
      ORDER BY created_at DESC LIMIT 1
    `).get(taskId) as Row | undefined;
    if (latestTurn?.end_reason === "master_stop"
      && latestInstructionSeq <= Number(latestTurn.instruction_seq)) return false;

    if (latestInstructionSeq > Math.max(task.handled_instruction_seq, this.assignedInstructionSeq(task))) return true;
    if (this.db.prepare(`
      SELECT 1 FROM holo_turns
      WHERE task_id=? AND await_master=1 AND end_reason='assistant' AND instruction_seq>=?
      ORDER BY created_at DESC LIMIT 1
    `).get(taskId, latestInstructionSeq)) return false;
    return task.resume_enabled;
  }

  reserveHoloTurn(taskId: string): HoloTurnRecord | null {
    return this.transaction(() => {
      if (!this.needsHoloTurn(taskId)) return null;
      const task = this.getTaskRequired(taskId);
      const id = randomUUID();
      const timestamp = now();
      this.db.prepare(`
        INSERT INTO holo_turns(id,task_id,control_epoch,instruction_seq,await_master,settings_json,created_at)
        VALUES (?,?,?,?,0,?,?)
      `).run(id, task.id, task.control_epoch, this.latestInstructionSeq(task), JSON.stringify(this.getSettings().value), timestamp);
      return this.getHoloTurnRequired(id);
    });
  }

  assertHoloTurn(turnId: string, taskId?: string): HoloTurnRecord {
    const turn = this.getHoloTurnRequired(turnId);
    const task = this.getTaskRequired(turn.task_id);
    if (taskId !== undefined && taskId !== task.id) throw new HubError("unauthorized", "Holo Turn belongs to another Task");
    if (turn.ended_at !== null || task.state !== "Running" || turn.control_epoch !== task.control_epoch) {
      throw new HubError("stale", "stale Holo Turn");
    }
    if (turn.completion_summary !== null) {
      throw new HubError("stale", "Holo Turn is awaiting its final assistant reply");
    }
    const settings = JSON.parse(turn.settings_json) as HubSettings;
    if (Date.now() - Date.parse(turn.created_at) > settings.holo_turn_timeout_ms) throw new HubError("stale", "Holo Turn expired");
    return turn;
  }

  awaitMasterReply(turnId: string): HoloTurnRecord {
    const turn = this.assertHoloTurn(turnId);
    this.db.prepare("UPDATE holo_turns SET await_master=1 WHERE id=?").run(turn.id);
    return this.getHoloTurnRequired(turn.id);
  }

  getHoloInput(turnId: string): string {
    const turn = this.getHoloTurnRequired(turnId);
    const task = this.getTaskRequired(turn.task_id);
    if (turn.instruction_seq <= task.handled_instruction_seq) return "Continue the current Task.";

    const message = this.db.prepare(`
      SELECT sender,content,request_id
      FROM messages
      WHERE conversation_id=? AND seq=?
    `).get(task.conversation_id, turn.instruction_seq) as Row | undefined;
    if (!message) return "Continue the current Task.";
    if (String(message.sender) !== "control" || message.request_id === null) return String(message.content);

    const request = this.db.prepare("SELECT kind,answer_json FROM master_requests WHERE id=?")
      .get(String(message.request_id)) as Row | undefined;
    if (!request?.answer_json) return String(message.content);
    const answer = JSON.parse(String(request.answer_json)) as Row;
    return answer.approved === true
      ? "Master approved the requested operation."
      : "Master rejected the requested operation.";
  }

  private contextRunReference(run: RunRecord): Record<string, unknown> {
    let summary: string | null = null;
    let error: string | null = null;
    try {
      const value = JSON.parse(run.result_json ?? "{}").value;
      if (typeof value?.summary === "string") summary = value.summary.slice(0, 4096);
    } catch {}
    try {
      const parsed = JSON.parse(run.error_json ?? "{}");
      if (typeof parsed?.message === "string") error = parsed.message.slice(0, 2048);
    } catch {}
    return {
      id: run.id,
      capability_id: run.capability_id,
      operation: run.operation,
      state: run.state,
      effects: run.effects,
      cleanup_state: run.cleanup_state,
      has_result: run.result_json !== null || run.supplemental_result_json !== null,
      summary,
      error,
    };
  }

  getRunResultForTurn(turnId: string, targetRunId: string, maxBytes = 64 * 1024): Record<string, unknown> {
    const turn = this.assertHoloTurn(turnId);
    const target = this.getRunRequired(targetRunId);
    if (target.task_id !== turn.task_id) throw new HubError("unauthorized", "Run belongs to another Task");
    if (!Number.isSafeInteger(maxBytes) || maxBytes < 1024 || maxBytes > 256 * 1024) throw new HubError("invalid", "invalid max_bytes");

    const bounded = (value: string | null): Record<string, unknown> | null => {
      if (value === null) return null;
      const bytes = Buffer.from(value);
      if (bytes.length <= maxBytes) {
        try { return { truncated: false, value: JSON.parse(value) }; }
        catch { return { truncated: false, text: value }; }
      }
      return { truncated: true, bytes: bytes.length, excerpt: bytes.subarray(0, maxBytes).toString("utf8") };
    };
    const artifacts = this.db.prepare("SELECT ref,fingerprint,ownership,observed_at FROM artifact_references WHERE task_id=? AND run_id=? ORDER BY observed_at")
      .all(turn.task_id, targetRunId) as Row[];
    return {
      run: this.contextRunReference(target),
      result: bounded(target.result_json),
      supplemental_result: bounded(target.supplemental_result_json),
      artifacts,
    };
  }

  confirmHoloConversation(turnId: string, url: string): ConversationBinding {
    return this.transaction(() => {
      const turn = this.getHoloTurnRequired(turnId);
      const externalId = conversationId(url);
      if (!externalId) throw new HubError("invalid", "invalid ChatGPT Conversation");
      return this.bindConversation(turn.task_id, "chatgpt", externalId, url);
    });
  }

  syncHoloTurn(turnId: string, content: string, complete: boolean): { turn: HoloTurnRecord; message_id: string } {
    if (!content.trim()) throw new Error("invalid empty Holo message");
    return this.transaction(() => {
      const turn = this.getHoloTurnRequired(turnId);
      if (turn.ended_at !== null) throw new HubError("stale", "Holo Turn already ended");
      const task = this.getTaskRequired(turn.task_id);
      const timestamp = now();
      const rows = this.db.prepare("SELECT id FROM messages WHERE turn_id=? ORDER BY rowid").all(turn.id) as Row[];
      if (rows.length > 1) throw new Error("Holo Turn has multiple Chat messages");

      let messageId: string;
      if (rows.length === 1) {
        messageId = String(rows[0]!.id);
        this.db.prepare("UPDATE messages SET content=? WHERE id=?").run(content, messageId);
      } else {
        messageId = randomUUID();
        const seq = Number((this.db.prepare("SELECT COALESCE(MAX(seq),0)+1 AS seq FROM messages WHERE conversation_id=?")
          .get(task.conversation_id) as Row).seq);
        this.db.prepare("INSERT INTO messages(id,conversation_id,seq,sender,content,created_at,turn_id) VALUES (?,?,?,?,?,?,?)")
          .run(messageId, task.conversation_id, seq, task.resident_id, content, timestamp, turn.id);
      }

      this.db.prepare("UPDATE conversations SET updated_at=? WHERE id=?").run(timestamp, task.conversation_id);
      this.db.prepare("UPDATE tasks SET updated_at=? WHERE id=?").run(timestamp, task.id);

      if (complete) {
        this.db.prepare("UPDATE tasks SET handled_instruction_seq=MAX(handled_instruction_seq,?),updated_at=? WHERE id=?")
          .run(turn.instruction_seq, timestamp, task.id);
        this.db.prepare("UPDATE holo_turns SET ended_at=?,end_reason='assistant' WHERE id=? AND ended_at IS NULL")
          .run(timestamp, turn.id);
        if (turn.completion_summary !== null) {
          this.applyTaskCompletion(task.id, turn.completion_summary, timestamp);
        }
      }

      return { turn: this.getHoloTurnRequired(turn.id), message_id: messageId };
    });
  }

  endHoloTurn(turnId: string, reason: string): HoloTurnRecord {
    const turn = this.getHoloTurnRequired(turnId);
    if (turn.ended_at !== null) return turn;
    this.db.prepare("UPDATE holo_turns SET ended_at=?,end_reason=?,completion_summary=NULL WHERE id=? AND ended_at IS NULL")
      .run(now(), reason.slice(0, 512), turnId);
    return this.getHoloTurnRequired(turnId);
  }

  endActiveHoloTurn(taskId: string, reason: string): void {
    this.db.prepare("UPDATE holo_turns SET ended_at=?,end_reason=?,completion_summary=NULL WHERE task_id=? AND ended_at IS NULL")
      .run(now(), reason.slice(0, 512), taskId);
  }

  requestRunStops(taskId: string): void {
    const timestamp = now();
    // Runs declared side-effect free carry no cleanup obligation. Pausing invalidates
    // them immediately; late observations remain historical and cannot regain authority.
    this.db.prepare(`UPDATE runs
      SET state='Interrupted',effects='none',cleanup_state='clear',
          error_json=COALESCE(error_json,?),ended_at=?
      WHERE task_id=? AND state='Running' AND side_effects='none'`)
      .run(JSON.stringify({ message: "Task execution stopped" }), timestamp, taskId);
    this.db.prepare(`UPDATE runs
      SET stop_requested_at=COALESCE(stop_requested_at,?)
      WHERE task_id=? AND state='Running' AND side_effects='possible'`)
      .run(timestamp, taskId);
  }

  ensureRunApproval(runId: string, prompt: string): void {
    this.transaction(() => {
      const run = this.getRunRequired(runId);
      if (!this.db.prepare("SELECT 1 FROM master_requests WHERE run_id=? AND kind='approval'").get(runId)) {
        this.createMasterRequest({ task_id: run.task_id, run_id: runId, kind: "approval", prompt });
      }
    });
  }

  requestActionStop(runId: string): RunRecord {
    return this.transaction(() => {
      const run = this.getRunRequired(runId);
      if (run.state === "Pending") {
        this.db.prepare("UPDATE runs SET state='Cancelled',effects='none',cleanup_state='clear',ended_at=? WHERE id=?").run(now(), runId);
        this.cancelObsoleteApprovals();
      } else if (run.state === "Running") {
        this.db.prepare("UPDATE runs SET stop_requested_at=COALESCE(stop_requested_at,?) WHERE id=?").run(now(), runId);
      }
      return this.getRunRequired(runId);
    });
  }

  interruptRun(runId: string, reason: string): void {
    const run = this.getRunRequired(runId);
    if (run.state !== "Running") return;
    this.db.prepare(`UPDATE runs SET state='Interrupted',effects=?,cleanup_state=?,
      error_json=?,stop_requested_at=COALESCE(stop_requested_at,?),ended_at=? WHERE id=?`)
      .run(run.side_effects === "none" ? "none" : "unknown", run.side_effects === "none" ? "clear" : "unknown",
        JSON.stringify({ message: reason }), now(), now(), runId);
  }

  // Conversation identity is a routing hint only. It never grants Task authority.
  bindConversation(taskId: string, provider: string, conversationId: string, url: string | null = null): ConversationBinding {
    this.getTaskRequired(taskId);
    if (!provider.trim() || !conversationId.trim()) throw new HubError("invalid", "invalid conversation binding");
    if (provider === "chatgpt" && this.getHoloChatBinding().external_conversation_id === conversationId) {
      throw new HubError("conflict", "Holo normal conversation cannot be used for a Task");
    }
    this.db.prepare(`UPDATE provider_bindings SET external_conversation_id=NULL,external_url=NULL,updated_at=?
      WHERE provider=? AND external_conversation_id=? AND task_id<>?`).run(now(), provider, conversationId, taskId);
    this.db.prepare(`INSERT INTO provider_bindings(task_id,provider,external_conversation_id,external_url,updated_at)
      VALUES (?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET provider=excluded.provider,
      external_conversation_id=excluded.external_conversation_id,external_url=excluded.external_url,updated_at=excluded.updated_at`)
      .run(taskId, provider, conversationId, url, now());
    return this.getConversationBinding(taskId)!;
  }

  getConversationBinding(taskId: string): ConversationBinding | null {
    return (this.db.prepare("SELECT task_id,provider,external_conversation_id,external_url FROM provider_bindings WHERE task_id=?")
      .get(taskId) as unknown as ConversationBinding) ?? null;
  }

  prepareHoloConversation(turnId: string): ConversationBinding {
    return this.transaction(() => {
      const turn = this.assertHoloTurn(turnId);
      this.db.prepare(`INSERT INTO provider_bindings(task_id,provider,external_conversation_id,external_url,updated_at)
        VALUES (?,'chatgpt',NULL,NULL,?) ON CONFLICT(task_id) DO NOTHING`).run(turn.task_id, now());
      const binding = this.getConversationBinding(turn.task_id)!;
      if (binding.provider !== "chatgpt") throw new HubError("conflict", "Task is bound to another provider");
      return binding;
    });
  }

  authenticateTurn(turnId: string, envelope: HubCommandEnvelope): string {
    if (!turnId || !envelope || typeof envelope.command_id !== "string") {
      throw new HubError("unauthorized", "Holo Turn authentication required");
    }
    const turn = this.getHoloTurn(turnId);
    if (!turn) throw new HubError("unauthorized", "Holo Turn is invalid or expired");

    if (this.getCommandReceipt(`turn:${turnId}`, envelope.command_id)) return turnId;

    this.assertHoloTurn(turnId);
    return turnId;
  }

  private refineTaskDefinition(taskId: string, title: string, completionCriteria: CompletionCriterion[]): TaskRecord {
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
    this.db
      .prepare(`
        UPDATE tasks
        SET title=?, completion_criteria=?, revision=revision+1, updated_at=?
        WHERE id=?
      `)
      .run(title.trim(), JSON.stringify(completionCriteria), now(), taskId);
    return this.getTaskRequired(taskId);
  }

  stageTaskCompletion(
    taskId: string,
    resultSummary: string,
    turnId: string,
    evidence: CompletionEvidence[] = [],
  ): HoloTurnRecord {
    return this.transaction(() => {
      const turn = this.assertHoloTurn(turnId, taskId);
      if (turn.await_master) throw new Error("Holo Turn already awaits Master reply");
      const summary = this.validateTaskCompletion(taskId, resultSummary, evidence, false, turnId);
      this.db.prepare("UPDATE holo_turns SET completion_summary=? WHERE id=?")
        .run(summary, turn.id);
      return this.getHoloTurnRequired(turn.id);
    });
  }

  confirmTaskCompletion(taskId: string, resultSummary: string, evidence: CompletionEvidence[] = []): TaskRecord {
    return this.transaction(() => {
      const summary = this.validateTaskCompletion(taskId, resultSummary, evidence, true);
      return this.applyTaskCompletion(taskId, summary, now());
    });
  }

  private validateTaskCompletion(
    taskId: string,
    resultSummary: string,
    evidence: CompletionEvidence[],
    masterConfirmed: boolean,
    turnId?: string,
  ): string {
    const summary = resultSummary.trim();
    if (!summary) throw new Error("result summary is required");
    const task = this.getTaskRequired(taskId);
    if (masterConfirmed) {
      if (!["Running", "Paused"].includes(task.state)) throw new Error("only active task can complete");
    } else if (task.state !== "Running") {
      throw new Error("only Running task can complete");
    }
    if (!task.initial_message_id) throw new Error("task has no initial instruction");

    const maxInstructionSeq = this.latestInstructionSeq(task);
    if (task.handled_instruction_seq < maxInstructionSeq) {
      if (masterConfirmed || !turnId) throw new Error("task has unhandled Master instructions");
      const completingTurn = this.getHoloTurnRequired(turnId);
      if (completingTurn.task_id !== task.id || completingTurn.instruction_seq < maxInstructionSeq) {
        throw new Error("task has unhandled Master instructions");
      }
    }

    const activeTurn = this.db.prepare("SELECT id FROM holo_turns WHERE task_id=? AND ended_at IS NULL").get(taskId) as Row | undefined;
    if (activeTurn && (masterConfirmed || String(activeTurn.id) !== turnId)) throw new Error("task has an active Holo Turn");

    const activeRuns = this.listRuns(taskId).filter(run => run.state === "Pending" || run.state === "Running").length;
    if (activeRuns > 0) throw new Error("task has unfinished runs");

    const pendingRequests = Number(
      (this.db.prepare("SELECT COUNT(*) AS count FROM master_requests WHERE task_id=? AND state='Pending' AND kind='approval'")
        .get(taskId) as Row).count,
    );
    if (pendingRequests > 0) throw new Error("task has pending Approvals");

    const unsettledRuns = this.listRuns(taskId)
      .filter(run => run.effects === "unknown" || run.cleanup_state !== "clear").length;
    if (unsettledRuns > 0) throw new Error("task has unresolved side effects or cleanup");

    if (task.completion_criteria.length) this.validateCompletionEvidence(task, evidence);
    return summary;
  }

  private applyTaskCompletion(taskId: string, summary: string, timestamp: string): TaskRecord {
    this.db.prepare(`
      UPDATE tasks
      SET state='Completed', revision=revision+1, control_epoch=control_epoch+1,
          result_summary=?, ended_at=?, updated_at=?
      WHERE id=? AND state IN ('Running','Paused')
    `).run(summary, timestamp, timestamp, taskId);
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
        if (!verification || verification.task_id !== task.id || verification.state !== "Completed"
          || verification.effects === "unknown" || verification.cleanup_state !== "clear" || !observed?.passed
          || observed.kind !== criterion.verification_kind || observed.artifact_ref !== claim.artifact_ref || observed.fingerprint !== claim.fingerprint) {
          throw new Error(`required verification is not satisfied: ${criterion.id}`);
        }
      }
    }
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
      .prepare("SELECT * FROM master_requests WHERE state='Pending' AND kind='approval' ORDER BY created_at")
      .all() as Row[];
    const runs = this.listRuns();
    const holoTurns = this.listHoloTurns();
    const messages = (this.db
      .prepare("SELECT * FROM messages ORDER BY conversation_id, seq")
      .all() as Row[]).map(row => {
        const { audience_json, ...message } = row;
        return { ...message, audience: JSON.parse(String(audience_json)) };
      });
    const residents = this.listResidents();
    return {
      revision: Number(this.getMetadata("snapshot_revision") ?? 0),
      tasks,
      pending_requests: requests,
      runs,
      holo_turns: holoTurns,
      messages,
      residents,
      conversations: this.listConversations(),
      chat_responses: this.db.prepare("SELECT * FROM chat_responses ORDER BY created_at,rowid").all(),
      settings: this.getSettings(),
      artifacts: this.db.prepare("SELECT * FROM artifact_references ORDER BY observed_at").all(),
      provider_bindings: this.db.prepare("SELECT task_id,provider,external_conversation_id,external_url FROM provider_bindings").all(),
    };
  }
}
