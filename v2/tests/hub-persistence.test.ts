import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { HubService } from "../src/hub/service.js";
import { HubStore } from "../src/hub/store.js";

test("Hub state survives SQLite close and reopen", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-persist-"));
  const dbPath = join(root, "hub.sqlite3");

  try {
    const firstStore = new HubStore(dbPath);
    const firstService = new HubService(firstStore);
    const created = firstService.handleMasterCommand({
      protocol_version: 1,
      command_id: "persist-create",
      issued_at: new Date().toISOString(),
      type: "CreateTask",
      target: null,
      payload: { resident_id: "holo" },
    });
    firstStore.close();

    const secondStore = new HubStore(dbPath);
    try {
      const task = secondStore.getTask(String(created.task_id));
      assert.equal(task?.state, "Paused");
      assert.equal(task?.resident_id, "holo");
      assert.equal(secondStore.listTasks().length, 1);
    } finally {
      secondStore.close();
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
