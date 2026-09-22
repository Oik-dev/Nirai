import assert from "node:assert/strict";
import { mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { HubRuntime } from "../src/hub/runtime.js";
import { HubStore } from "../src/hub/store.js";

test("same Data Root cannot be opened by two Hub runtimes", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-lock-"));
  const first = await HubRuntime.start(root);
  const alias = `${root}-alias`;

  try {
    await assert.rejects(() => HubRuntime.start(root), /already using this Data Root/);
    symlinkSync(root, alias, "junction");
    await assert.rejects(() => HubRuntime.start(alias), /already using this Data Root/);
  } finally {
    await first.close();
    rmSync(alias, { recursive: true, force: true });
    rmSync(root, { recursive: true, force: true });
  }
});

test("normal Hub shutdown persists Pause before the next startup recovery", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-shutdown-"));
  const runtime = await HubRuntime.start(root);
  const task = runtime.store.createTask("holo");
  runtime.store.addMasterMessage(task.id, "work");
  runtime.store.setTaskResume(task.id, true);
  const epoch = runtime.store.getTask(task.id)!.control_epoch;
  await runtime.close();
  const stored = new HubStore(join(root, "hub.sqlite3"));
  try {
    assert.equal(stored.getTask(task.id)?.state, "Paused");
    assert.equal(stored.getTask(task.id)?.resume_enabled, true);
    assert.ok(stored.getTask(task.id)!.control_epoch > epoch);
  } finally {
    stored.close();
    rmSync(root, { recursive: true, force: true });
  }
});
