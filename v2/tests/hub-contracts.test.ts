import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { CapabilityRegistry } from "../src/hub/capability.js";
import { TaskEngine } from "../src/hub/engine.js";
import { HubService } from "../src/hub/service.js";
import { HubStore } from "../src/hub/store.js";
import { DEFAULT_SETTINGS } from "../src/shared/settings.js";

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-test-"));
  const store = new HubStore(join(root, "hub.sqlite3"));
  store.ensureResident("holo", "Holo");
  const service = new HubService(store);
  return {
    root,
    store,
    service,
    close() {
      store.close();
      rmSync(root, { recursive: true, force: true });
    },
  };
}

type Fixture = ReturnType<typeof fixture>;

function command(
  commandId: string,
  type: string,
  payload: Record<string, unknown>,
  expectedRevision?: number,
  issuedAt = new Date().toISOString(),
) {
  return {
    protocol_version: 1,
    command_id: commandId,
    issued_at: issuedAt,
    type,
    target: typeof payload.task_id === "string"
      ? payload.task_id
      : typeof payload.request_id === "string" ? payload.request_id : null,
    ...(expectedRevision === undefined ? {} : { expected_revision: expectedRevision }),
    payload,
  };
}

function createTask(f: Fixture): string {
  return String(f.service.handleMasterCommand(
    command(randomUUID(), "CreateTask", { resident_id: "holo" }),
  ).task_id);
}

function startTask(f: Fixture, taskId: string, content = "work"): void {
  const task = f.store.getTask(taskId)!;
  f.service.handleMasterCommand(command(
    randomUUID(),
    "SendConversationMessage",
    { task_id: taskId, sender: "master", content },
    task.revision,
  ));
}

test("large Action results require bounded lookup", () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);
    const turn = f.store.reserveHoloTurn(taskId)!;
    const task = f.store.getTask(taskId)!;
    const ids: string[] = [];

    for (let i = 0; i < 30; i++) {
      const run = f.store.createRun({
        task_id: taskId,
        turn_id: turn.id,
        capability_id: "fixture",
        operation: "read",
        control_epoch: task.control_epoch,
        input: { index: i },
      }, "none");
      f.store.markRunRunning(run.id);
      f.store.recordRunResult(run.id, {
        state: "Completed",
        effects: "none",
        cleanup_state: "clear",
        result: { summary: `result ${i}`, content: "x".repeat(80 * 1024) },
      });
      ids.push(run.id);
    }

    const detail = f.store.getRunResultForTurn(turn.id, ids.at(-1)!, 4096);
    assert.equal((detail.result as { truncated: boolean }).truncated, true);
  } finally {
    f.close();
  }
});

test("Master commands are idempotent and stale or expired mutations are rejected", () => {
  const f = fixture();
  try {
    const create = command("same", "CreateTask", { resident_id: "holo" });
    const first = f.service.handleMasterCommand(create);
    assert.deepEqual(f.service.handleMasterCommand(create), first);

    assert.throws(
      () => f.service.handleMasterCommand(command(
        randomUUID(),
        "CreateTask",
        { resident_id: "holo" },
        undefined,
        new Date(0).toISOString(),
      )),
      /expired/,
    );

    const taskId = String(first.task_id);
    const task = f.store.getTask(taskId)!;
    assert.throws(
      () => f.service.handleMasterCommand(command(
        randomUUID(),
        "SendConversationMessage",
        { task_id: taskId, sender: "master", content: "x" },
        task.revision + 1,
      )),
      /stale/,
    );
  } finally {
    f.close();
  }
});

test("Pause, ResumeTask and Resume ON/OFF remain independent", () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);

    let task = f.store.getTask(taskId)!;
    f.service.handleMasterCommand(command(
      randomUUID(), "SetTaskResume", { task_id: taskId, enabled: true }, task.revision,
    ));
    assert.equal(f.store.getTask(taskId)?.resume_enabled, true);

    f.service.handleMasterCommand(command(randomUUID(), "PauseTask", { task_id: taskId }));
    task = f.store.getTask(taskId)!;
    assert.equal(task.state, "Paused");
    assert.equal(task.resume_enabled, true);

    f.service.handleMasterCommand(command(
      randomUUID(), "ResumeTask", { task_id: taskId }, task.revision,
    ));
    assert.equal(f.store.getTask(taskId)?.state, "Running");
    assert.equal(f.store.getTask(taskId)?.resume_enabled, true);
  } finally {
    f.close();
  }
});

test("restart recovery pauses Tasks, closes active Turn and reconciles only Action Runs", () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);
    f.store.setTaskResume(taskId, true);
    const turn = f.store.reserveHoloTurn(taskId)!;
    const task = f.store.getTask(taskId)!;

    const running = f.store.createRun({
      task_id: taskId,
      turn_id: turn.id,
      capability_id: "fixture",
      operation: "write",
      control_epoch: task.control_epoch,
      input: {},
    }, "possible");
    f.store.markRunRunning(running.id);

    const pending = f.store.createRun({
      task_id: taskId,
      turn_id: turn.id,
      capability_id: "fixture",
      operation: "later",
      control_epoch: task.control_epoch,
      input: {},
    }, "none");

    f.store.recoverAfterRestart();

    assert.equal(f.store.getTask(taskId)?.state, "Paused");
    assert.equal(f.store.getTask(taskId)?.resume_enabled, true);
    assert.notEqual(f.store.getHoloTurn(turn.id)?.ended_at, null);
    assert.equal(f.store.getRun(running.id)?.state, "Interrupted");
    assert.equal(f.store.getRun(running.id)?.effects, "unknown");
    assert.equal(f.store.getRun(pending.id)?.state, "Cancelled");
  } finally {
    f.close();
  }
});

test("Pause preserves late Action observations without reviving the Task", () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);
    const task = f.store.getTask(taskId)!;
    const run = f.store.createRun({
      task_id: taskId,
      capability_id: "fixture",
      operation: "write",
      control_epoch: task.control_epoch,
      input: {},
    }, "possible");
    f.store.markRunRunning(run.id);

    f.store.pauseTask(taskId);
    f.store.recordRunResult(run.id, {
      state: "Completed",
      effects: "applied",
      cleanup_state: "clear",
      result: { summary: "late result" },
    });

    assert.equal(f.store.getTask(taskId)?.state, "Paused");
    assert.equal(f.store.getRun(run.id)?.effects, "applied");
    assert.ok(f.store.getRun(run.id)?.result_json?.includes("late result")
      || f.store.getRun(run.id)?.supplemental_result_json?.includes("late result"));
  } finally {
    f.close();
  }
});

test("Cancel closes pending work and Approvals while running side effects remain observable", () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);
    const task = f.store.getTask(taskId)!;

    const running = f.store.createRun({
      task_id: taskId,
      capability_id: "fixture",
      operation: "write",
      control_epoch: task.control_epoch,
      input: {},
    }, "possible");
    f.store.markRunRunning(running.id);

    const pending = f.store.createRun({
      task_id: taskId,
      capability_id: "fixture",
      operation: "later",
      control_epoch: task.control_epoch,
      input: {},
    }, "none");
    f.store.createMasterRequest({ task_id: taskId, run_id: pending.id, kind: "approval", prompt: "Approve later?" });

    f.store.cancelTask(taskId);

    assert.equal(f.store.getTask(taskId)?.state, "Cancelled");
    assert.equal(f.store.getRun(pending.id)?.state, "Cancelled");
    assert.equal(f.store.getRun(running.id)?.state, "Running");
    assert.notEqual(f.store.getRun(running.id)?.stop_requested_at, null);
    assert.equal((f.store.snapshot().pending_requests as unknown[]).length, 0);
  } finally {
    f.close();
  }
});

test("CompleteTask requires handled input and settled Actions before staging final completion", () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);

    assert.throws(() => f.store.confirmTaskCompletion(taskId, "too early"), /unhandled Master/);

    const turn = f.store.reserveHoloTurn(taskId)!;
    const task = f.store.getTask(taskId)!;
    const run = f.store.createRun({
      task_id: taskId,
      turn_id: turn.id,
      capability_id: "fixture",
      operation: "write",
      control_epoch: task.control_epoch,
      input: {},
    }, "possible");
    f.store.markRunRunning(run.id);
    f.store.recordRunResult(run.id, {
      state: "Failed",
      effects: "unknown",
      cleanup_state: "unknown",
      error: { message: "unknown" },
    });

    assert.throws(() => f.store.stageTaskCompletion(taskId, "still early", turn.id, []), /unresolved side effects|unfinished runs/);

    f.store.recordRunResult(run.id, {
      state: "Failed",
      effects: "none",
      cleanup_state: "clear",
      result: { summary: "reconciled" },
    });
    const staged = f.store.stageTaskCompletion(taskId, "done", turn.id, []);
    assert.equal(staged.completion_summary, "done");
    assert.equal(f.store.getTask(taskId)?.state, "Running");

    f.store.syncHoloTurn(turn.id, "最終回答", true);
    assert.equal(f.store.getTask(taskId)?.state, "Completed");
  } finally {
    f.close();
  }
});

test("Approval resolution stays explicit while ordinary Chat remains conversation", () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);
    const task = f.store.getTask(taskId)!;
    const run = f.store.createRun({
      task_id: taskId,
      capability_id: "fixture",
      operation: "write",
      control_epoch: task.control_epoch,
      input: { path: "target" },
    }, "possible");
    const request = f.store.createMasterRequest({
      task_id: taskId,
      run_id: run.id,
      kind: "approval",
      prompt: "Apply?",
    });

    f.service.handleMasterCommand(command(
      randomUUID(),
      "SendConversationMessage",
      { task_id: taskId, sender: "master", content: "ordinary chat" },
      f.store.getTask(taskId)!.revision,
    ));
    assert.equal((f.store.snapshot().pending_requests as unknown[]).length, 1);

    const pending = (f.store.snapshot().pending_requests as Array<{ id: string; revision: number }>)[0]!;
    f.service.handleMasterCommand(command(
      randomUUID(),
      "ResolveMasterRequest",
      { request_id: request.id, answer: { approved: true } },
      pending.revision,
    ));
    assert.equal((f.store.snapshot().pending_requests as unknown[]).length, 0);
  } finally {
    f.close();
  }
});

test("Approval gates exactly one fixed Action Run", () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);
    const task = f.store.getTask(taskId)!;
    const run = f.store.createRun({
      task_id: taskId,
      capability_id: "fixture",
      operation: "write",
      control_epoch: task.control_epoch,
      input: { path: "target" },
    }, "possible");

    const request = f.store.createMasterRequest({
      task_id: taskId,
      run_id: run.id,
      kind: "approval",
      prompt: "Apply?",
    });
    assert.throws(() => f.store.markRunRunning(run.id), /approval/);

    f.store.resolveMasterRequest(request.id, { approved: false }, 1);
    assert.equal(f.store.getRun(run.id)?.state, "Cancelled");
  } finally {
    f.close();
  }
});

test("Capability Engine preserves side-effect uncertainty on adapter failure", async () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);
    const registry = new CapabilityRegistry();
    registry.register({
      id: "unsafe",
      operations: new Map([["work", { side_effects: "possible" }]]),
      availability: () => ({ state: "ready" }),
      invoke: async () => { throw new Error("adapter crashed"); },
    });

    const engine = new TaskEngine(f.store, registry);
    const task = f.store.getTask(taskId)!;
    const run = engine.createRun({
      task_id: taskId,
      capability_id: "unsafe",
      operation: "work",
      control_epoch: task.control_epoch,
      input: {},
    });

    await assert.rejects(() => engine.dispatchRun(run.id), /adapter crashed/);
    assert.equal(f.store.getRun(run.id)?.state, "Failed");
    assert.equal(f.store.getRun(run.id)?.effects, "unknown");
    assert.equal(f.store.getRun(run.id)?.cleanup_state, "unknown");
  } finally {
    f.close();
  }
});

test("stale control epochs cannot create new Action Runs", () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);
    const oldEpoch = f.store.getTask(taskId)!.control_epoch;

    f.store.pauseTask(taskId);
    f.store.resumeTask(taskId);

    assert.throws(() => f.store.createRun({
      task_id: taskId,
      capability_id: "fixture",
      operation: "work",
      control_epoch: oldEpoch,
      input: {},
    }, "none"), /stale control epoch/);
  } finally {
    f.close();
  }
});

test("resource ownership remains with unsettled Action observations", () => {
  const f = fixture();
  try {
    const taskId = createTask(f);
    startTask(f, taskId);
    const task = f.store.getTask(taskId)!;
    const first = f.store.createRun({
      task_id: taskId,
      capability_id: "fixture",
      operation: "write",
      control_epoch: task.control_epoch,
      input: {},
      resources: ["workspace"],
    }, "possible");
    f.store.markRunRunning(first.id);
    f.store.recordRunResult(first.id, {
      state: "Failed",
      effects: "unknown",
      cleanup_state: "unknown",
      error: { message: "lost" },
    });

    assert.equal(f.store.resourcesAvailable(["workspace"]), false);

    f.store.recordRunResult(first.id, {
      state: "Failed",
      effects: "partial",
      cleanup_state: "clear",
      result: { summary: "reconciled" },
    });
    assert.equal(f.store.resourcesAvailable(["workspace"]), true);
  } finally {
    f.close();
  }
});
