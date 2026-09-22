import assert from "node:assert/strict";
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
    downgradeProbe.exec(`
      ALTER TABLE runs ADD COLUMN dispatch_epoch INTEGER;
      ALTER TABLE runs DROP COLUMN side_effects;
      DROP TABLE metadata;
      DROP TABLE artifact_references;
      ALTER TABLE runs DROP COLUMN context_instruction_seq;
      ALTER TABLE runs DROP COLUMN context_wake_seq;
      ALTER TABLE runs DROP COLUMN resources_json;
      ALTER TABLE runs DROP COLUMN settings_json;
      ALTER TABLE runs DROP COLUMN stop_requested_at;
      ALTER TABLE messages DROP COLUMN run_id;
      ALTER TABLE messages DROP COLUMN request_id;
      ALTER TABLE master_requests DROP COLUMN answered_by;
      PRAGMA user_version = 1;
    `);
    downgradeProbe.close();

    const migrated = await HubStore.open(dbPath, "0.2.0-test");
    try {
      assert.equal(migrated.getTask(String(created.task_id))?.resident_id, "holo");
      assert.equal(migrated.getMetadata("schema_version"), "5");
      assert.equal(migrated.getMetadata("app_version"), "0.2.0-test");
    } finally {
      migrated.close();
    }

    const schemaProbe = new DatabaseSync(dbPath);
    try {
      const runColumns = schemaProbe.prepare("PRAGMA table_info(runs)").all() as Array<{ name: string }>;
      assert.equal(runColumns.some((column) => column.name === "dispatch_epoch"), false);
      assert.equal(runColumns.some((column) => column.name === "side_effects"), true);
    } finally {
      schemaProbe.close();
    }

    const migrationBackups = readdirSync(join(root, "backups")).filter((name) =>
      name.startsWith("hub-pre-migration-v1-"),
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
      assert.equal(version, 1);
      assert.equal(taskCount, 1);
    } finally {
      backupProbe.close();
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
