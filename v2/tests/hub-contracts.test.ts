import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { CapabilityRegistry, type Capability } from "../src/hub/capability.js";
import { TaskEngine } from "../src/hub/engine.js";
import { HubService } from "../src/hub/service.js";
import { HubStore } from "../src/hub/store.js";

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-test-"));
  const store = new HubStore(join(root, "hub.sqlite3"));
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

function command(commandId: string, type: string, payload: Record<string, unknown>) {
  return {
    protocol_version: 1,
    command_id: commandId,
    issued_at: new Date().toISOString(),
    type,
    target: null,
    payload,
  };
}

test("duplicate Master command returns the same receipt without duplicating state", () => {
  const f = fixture();
  try {
    const envelope = command("cmd-create-1", "CreateTask", { resident_id: "holo" });
    const first = f.service.handleMasterCommand(envelope);
    const second = f.service.handleMasterCommand(envelope);

    assert.deepEqual(second, first);
    assert.equal(f.store.listTasks().length, 1);
    assert.equal(f.store.listConversations().length, 1);
  } finally {
    f.close();
  }
});

test("Pause/Resume and Resume ON/OFF are independent", () => {
  const f = fixture();
  try {
    const created = f.service.handleMasterCommand(
      command("cmd-create-2", "CreateTask", { resident_id: "holo" }),
    );
    const taskId = String(created.task_id);

    f.service.handleMasterCommand(
      command("cmd-start-2", "SendConversationMessage", {
        task_id: taskId,
        sender: "master",
        content: "Implement a small change",
      }),
    );
    f.service.handleMasterCommand(
      command("cmd-resume-on-2", "SetTaskResume", { task_id: taskId, enabled: true }),
    );
    f.service.handleMasterCommand(
      command("cmd-pause-2", "PauseTask", { task_id: taskId }),
    );

    let task = f.store.getTask(taskId);
    assert.equal(task?.state, "Paused");
    assert.equal(task?.resume_enabled, true);

    f.service.handleMasterCommand(
      command("cmd-resume-task-2", "ResumeTask", { task_id: taskId }),
    );
    task = f.store.getTask(taskId);
    assert.equal(task?.state, "Running");
    assert.equal(task?.resume_enabled, true);
  } finally {
    f.close();
  }
});

test("restart recovery pauses unfinished Tasks while preserving Resume", () => {
  const f = fixture();
  try {
    const created = f.service.handleMasterCommand(
      command("cmd-create-3", "CreateTask", { resident_id: "holo" }),
    );
    const taskId = String(created.task_id);

    f.service.handleMasterCommand(
      command("cmd-start-3", "SendConversationMessage", {
        task_id: taskId,
        sender: "master",
        content: "Keep working",
      }),
    );
    f.service.handleMasterCommand(
      command("cmd-resume-on-3", "SetTaskResume", { task_id: taskId, enabled: true }),
    );

    const epoch = f.store.getTask(taskId)!.control_epoch;
    const run = f.store.createRun({
      task_id: taskId,
      capability_id: "fake",
      operation: "work",
      kind: "action",
      control_epoch: epoch,
      dispatch_epoch: epoch,
      input: { value: 1 },
    });
    f.store.markRunRunning(run.id);

    f.store.recoverAfterRestart();

    const task = f.store.getTask(taskId);
    const recoveredRun = f.store.getRun(run.id);
    assert.equal(task?.state, "Paused");
    assert.equal(task?.resume_enabled, true);
    assert.equal(recoveredRun?.state, "Interrupted");
    assert.equal(recoveredRun?.effects, "unknown");
  } finally {
    f.close();
  }
});

test("late Run results are accepted after Pause without reviving the Task", () => {
  const f = fixture();
  try {
    const created = f.service.handleMasterCommand(
      command("cmd-create-4", "CreateTask", { resident_id: "holo" }),
    );
    const taskId = String(created.task_id);
    f.service.handleMasterCommand(
      command("cmd-start-4", "SendConversationMessage", {
        task_id: taskId,
        sender: "master",
        content: "Run a command",
      }),
    );

    const epoch = f.store.getTask(taskId)!.control_epoch;
    const run = f.store.createRun({
      task_id: taskId,
      capability_id: "fake",
      operation: "work",
      kind: "action",
      control_epoch: epoch,
      dispatch_epoch: epoch,
      input: { value: 2 },
    });
    f.store.markRunRunning(run.id);

    f.service.handleMasterCommand(
      command("cmd-pause-4", "PauseTask", { task_id: taskId }),
    );
    f.store.recordRunResult(run.id, {
      state: "Completed",
      effects: "applied",
      result: { ok: true },
    });

    assert.equal(f.store.getTask(taskId)?.state, "Paused");
    assert.equal(f.store.getRun(run.id)?.state, "Completed");
    assert.equal(f.store.getRun(run.id)?.effects, "applied");
  } finally {
    f.close();
  }
});

test("pending Master Request blocks completion until explicitly resolved", () => {
  const f = fixture();
  try {
    const created = f.service.handleMasterCommand(
      command("cmd-create-6", "CreateTask", { resident_id: "holo" }),
    );
    const taskId = String(created.task_id);
    f.service.handleMasterCommand(
      command("cmd-start-6", "SendConversationMessage", {
        task_id: taskId,
        sender: "master",
        content: "Need approval",
      }),
    );

    const request = f.store.createMasterRequest({
      task_id: taskId,
      kind: "approval",
      prompt: "Apply the proposed change?",
      proposal: { operation: "patch", target: "file.txt" },
    });

    assert.throws(
      () =>
        f.service.handleMasterCommand(
          command("cmd-complete-blocked-6", "CompleteTask", {
            task_id: taskId,
            result_summary: "done",
          }),
        ),
      /pending Master Requests/,
    );

    f.service.handleMasterCommand(
      command("cmd-resolve-6", "ResolveMasterRequest", {
        request_id: request.id,
        answer: { approved: true },
      }),
    );
    const completed = f.service.handleMasterCommand(
      command("cmd-complete-6", "CompleteTask", {
        task_id: taskId,
        result_summary: "done",
      }),
    );

    assert.equal((completed.task as { state: string }).state, "Completed");
  } finally {
    f.close();
  }
});

test("failed Run must be explicitly handled before Task completion", () => {
  const f = fixture();
  try {
    const created = f.service.handleMasterCommand(
      command("cmd-create-7a", "CreateTask", { resident_id: "holo" }),
    );
    const taskId = String(created.task_id);
    f.service.handleMasterCommand(
      command("cmd-start-7a", "SendConversationMessage", {
        task_id: taskId,
        sender: "master",
        content: "Try the work",
      }),
    );

    const epoch = f.store.getTask(taskId)!.control_epoch;
    const run = f.store.createRun({
      task_id: taskId,
      capability_id: "fake",
      operation: "work",
      kind: "action",
      control_epoch: epoch,
      dispatch_epoch: epoch,
      input: {},
    });
    f.store.markRunRunning(run.id);
    f.store.recordRunResult(run.id, {
      state: "Failed",
      effects: "none",
      error: { message: "expected failure" },
    });

    assert.throws(() => f.store.completeTask(taskId, "done"), /unresolved failed runs/);
    f.store.resolveRunFailure(run.id, "not_needed", "The failed attempt is not required for the objective.");
    const completed = f.store.completeTask(taskId, "done");
    assert.equal(completed.state, "Completed");
  } finally {
    f.close();
  }
});

test("ordinary Task Chat does not resolve a pending Master Request", () => {
  const f = fixture();
  try {
    const created = f.service.handleMasterCommand(
      command("cmd-create-chat-boundary", "CreateTask", { resident_id: "holo" }),
    );
    const taskId = String(created.task_id);
    f.service.handleMasterCommand(
      command("cmd-start-chat-boundary", "SendConversationMessage", {
        task_id: taskId,
        sender: "master",
        content: "Initial instruction",
      }),
    );
    f.store.createMasterRequest({
      task_id: taskId,
      kind: "input",
      prompt: "Choose one",
    });

    f.service.handleMasterCommand(
      command("cmd-normal-chat-boundary", "SendConversationMessage", {
        task_id: taskId,
        sender: "master",
        content: "This is normal chat, not the request answer.",
      }),
    );

    const current = f.store.snapshot() as { pending_requests: unknown[] };
    assert.equal(current.pending_requests.length, 1);
  } finally {
    f.close();
  }
});

test("Capability Registry and Engine execute one recorded Run through the common boundary", async () => {
  const f = fixture();
  try {
    const created = f.service.handleMasterCommand(
      command("cmd-create-7", "CreateTask", { resident_id: "holo" }),
    );
    const taskId = String(created.task_id);
    f.service.handleMasterCommand(
      command("cmd-start-7", "SendConversationMessage", {
        task_id: taskId,
        sender: "master",
        content: "Use the fake capability",
      }),
    );

    const epoch = f.store.getTask(taskId)!.control_epoch;
    const run = f.store.createRun({
      task_id: taskId,
      capability_id: "fake",
      operation: "work",
      kind: "action",
      control_epoch: epoch,
      dispatch_epoch: epoch,
      input: { value: 7 },
    });

    const fake: Capability = {
      id: "fake",
      operations: new Set(["work"]),
      availability: () => ({ state: "ready" }),
      invoke: async (_operation, input) => ({
        state: "Completed",
        effects: "none",
        result: { echoed: input },
      }),
    };
    const registry = new CapabilityRegistry();
    registry.register(fake);
    const engine = new TaskEngine(f.store, registry);
    await engine.dispatchRun(run.id);

    const finished = f.store.getRun(run.id);
    assert.equal(finished?.state, "Completed");
    assert.deepEqual(JSON.parse(finished!.result_json!), { echoed: { value: 7 } });
  } finally {
    f.close();
  }
});

test("stale control epochs cannot dispatch new work", () => {
  const f = fixture();
  try {
    const created = f.service.handleMasterCommand(
      command("cmd-create-5", "CreateTask", { resident_id: "holo" }),
    );
    const taskId = String(created.task_id);
    f.service.handleMasterCommand(
      command("cmd-start-5", "SendConversationMessage", {
        task_id: taskId,
        sender: "master",
        content: "Start",
      }),
    );

    const oldEpoch = f.store.getTask(taskId)!.control_epoch;
    f.service.handleMasterCommand(
      command("cmd-pause-5", "PauseTask", { task_id: taskId }),
    );
    f.service.handleMasterCommand(
      command("cmd-resume-task-5", "ResumeTask", { task_id: taskId }),
    );

    assert.throws(
      () =>
        f.store.createRun({
          task_id: taskId,
          capability_id: "fake",
          operation: "work",
          kind: "action",
          control_epoch: oldEpoch,
          dispatch_epoch: oldEpoch,
          input: {},
        }),
      /stale control epoch/,
    );
  } finally {
    f.close();
  }
});
