import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import { HubService } from "../src/hub/service.js";
import { HubStore } from "../src/hub/store.js";

test("Hub backup is readable and schema migration makes a pre-migration backup", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-persist-"));
  const dbPath = join(root, "hub.sqlite3");
  const manualBackupPath = join(root, "manual", "hub-backup.sqlite3");

  try {
    const firstStore = new HubStore(dbPath, "0.1.0-test");
    firstStore.ensureResident("holo", "Holo");
    const firstService = new HubService(firstStore);
    const created = firstService.handleMasterCommand({
      protocol_version: 1,
      command_id: "persist-create",
      issued_at: new Date().toISOString(),
      type: "CreateTask",
      target: null,
      payload: { resident_id: "holo" },
    });
    await firstStore.backupTo(manualBackupPath);
    firstStore.close();

    const manualBackup = new HubStore(manualBackupPath);
    try {
      assert.equal(manualBackup.listTasks().length, 1);
      assert.equal(manualBackup.getTask(String(created.task_id))?.resident_id, "holo");
    } finally {
      manualBackup.close();
    }

    const downgradeProbe = new DatabaseSync(dbPath);
    try {
      // Recreate the released schema 13 shape, including settings keys it no longer defines.
      downgradeProbe.exec(`
        ALTER TABLE holo_turns DROP COLUMN completion_summary;
        ALTER TABLE holo_turns DROP COLUMN await_master;
        ALTER TABLE holo_turns DROP COLUMN instruction_seq;
        UPDATE settings SET value_json=json_set(value_json,'$.response_retry_limit',3) WHERE key='runtime';
        PRAGMA user_version = 13;
      `);
    } finally {
      downgradeProbe.close();
    }

    const migrated = await HubStore.open(dbPath, "0.2.0-test");
    try {
      assert.equal(migrated.getTask(String(created.task_id))?.resident_id, "holo");
      assert.equal(migrated.getMetadata("schema_version"), "16");
      assert.equal(migrated.getMetadata("app_version"), "0.2.0-test");
      assert.equal(Object.hasOwn(migrated.getSettings().value, "response_retry_limit"), false);
    } finally {
      migrated.close();
    }

    const schemaProbe = new DatabaseSync(dbPath);
    try {
      const turnColumns = schemaProbe.prepare("PRAGMA table_info(holo_turns)").all() as Array<{ name: string }>;
      assert.equal(turnColumns.some((column) => column.name === "instruction_seq"), true);
      assert.equal(turnColumns.some((column) => column.name === "await_master"), true);
      assert.equal(turnColumns.some((column) => column.name === "completion_summary"), true);
    } finally {
      schemaProbe.close();
    }

    const migrationBackups = readdirSync(join(root, "backups")).filter((name) =>
      name.startsWith("hub-pre-migration-v13-"),
    );
    assert.equal(migrationBackups.length, 1);

    const backupProbe = new DatabaseSync(join(root, "backups", migrationBackups[0]!));
    try {
      const version = Number(
        (backupProbe.prepare("PRAGMA user_version").get() as { user_version: number }).user_version,
      );
      const taskCount = Number(
        (backupProbe.prepare("SELECT COUNT(*) AS count FROM tasks").get() as { count: number }).count,
      );
      assert.equal(version, 13);
      assert.equal(taskCount, 1);
    } finally {
      backupProbe.close();
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("schema 14 input Requests migrate into Chat plus Turn handoff without a question Request", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-input-migration-"));
  const dbPath = join(root, "hub.sqlite3");

  try {
    const store = new HubStore(dbPath, "0.1.0-test");
    store.ensureResident("holo", "Holo");

    const waitingTask = store.createTask("holo");
    store.addMasterMessage(waitingTask.id, "waiting work");
    store.setTaskResume(waitingTask.id, true);
    const waitingTurn = store.reserveHoloTurn(waitingTask.id)!;
    store.syncHoloTurn(waitingTurn.id, "どちらにしますか？", true);

    const answeredTask = store.createTask("holo");
    store.addMasterMessage(answeredTask.id, "answered work");
    const answeredTurn = store.reserveHoloTurn(answeredTask.id)!;
    store.syncHoloTurn(answeredTurn.id, "方針を教えてください。", true);
    const answeredConversation = store.getTask(answeredTask.id)!.conversation_id;
    store.close();

    const probe = new DatabaseSync(dbPath);
    try {
      probe.exec(`
        ALTER TABLE holo_turns DROP COLUMN completion_summary;
        ALTER TABLE holo_turns DROP COLUMN await_master;
        PRAGMA user_version = 14;
      `);

      const pendingId = randomUUID();
      const resolvedId = randomUUID();
      const timestamp = new Date().toISOString();
      probe.prepare(`
        INSERT INTO master_requests(
          id,task_id,run_id,turn_id,kind,state,revision,prompt,
          proposal_json,proposal_fingerprint,answer_json,created_at,resolved_at,answered_by
        ) VALUES (?,?,NULL,?,'input','Pending',1,?,NULL,NULL,NULL,?,NULL,NULL)
      `).run(pendingId, waitingTask.id, waitingTurn.id, "legacy pending question", timestamp);
      probe.prepare(`
        INSERT INTO master_requests(
          id,task_id,run_id,turn_id,kind,state,revision,prompt,
          proposal_json,proposal_fingerprint,answer_json,created_at,resolved_at,answered_by
        ) VALUES (?,?,NULL,?,'input','Resolved',2,?,NULL,NULL,?,?,?,'master')
      `).run(
        resolvedId,
        answeredTask.id,
        answeredTurn.id,
        "legacy resolved question",
        JSON.stringify({ text: "legacy answer" }),
        timestamp,
        timestamp,
      );
      const seq = Number(
        (probe.prepare("SELECT COALESCE(MAX(seq),0)+1 AS seq FROM messages WHERE conversation_id=?")
          .get(answeredConversation) as { seq: number }).seq,
      );
      probe.prepare(`
        INSERT INTO messages(id,conversation_id,seq,sender,content,created_at,request_id)
        VALUES (?,?,?,'control',?,?,?)
      `).run(randomUUID(), answeredConversation, seq, `Master Request resolved: ${resolvedId}`, timestamp, resolvedId);
    } finally {
      probe.close();
    }

    const migrated = await HubStore.open(dbPath, "0.2.0-test");
    try {
      assert.equal(migrated.getHoloTurn(waitingTurn.id)?.await_master, true);
      assert.equal(migrated.needsHoloTurn(waitingTask.id), false);
      assert.equal((migrated.snapshot().pending_requests as unknown[]).length, 0);

      const messages = migrated.snapshot().messages as Array<{
        conversation_id: string;
        sender: string;
        content: string;
        request_id: string | null;
      }>;
      const converted = messages.find(message =>
        message.conversation_id === answeredConversation && message.content === "legacy answer"
      );
      assert.equal(converted?.sender, "master");
      assert.equal(converted?.request_id, null);

      const next = migrated.reserveHoloTurn(answeredTask.id);
      assert.ok(next);
      assert.equal(migrated.getHoloInput(next.id), "legacy answer");
    } finally {
      migrated.close();
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
