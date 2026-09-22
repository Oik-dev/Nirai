import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { CapabilityRegistry, type Capability } from "../src/hub/capability.js";
import { TaskEngine } from "../src/hub/engine.js";
import { HubService } from "../src/hub/service.js";
import { HubStore } from "../src/hub/store.js";
import { fingerprint } from "../src/shared/stable.js";

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

function createStoredRun(
  f: Fixture,
  input: Parameters<HubStore["createRun"]>[0],
  sideEffects: Parameters<HubStore["createRun"]>[1] = "possible",
) {
  return f.store.createRun(input, sideEffects);
}

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
    target: typeof payload.task_id === "string" ? payload.task_id : null,
    ...(expectedRevision === undefined ? {} : { expected_revision: expectedRevision }),
    payload,
  };
}

function taskCommand(
  f: Fixture,
  commandId: string,
  type: string,
  payload: Record<string, unknown> & { task_id: string },
) {
  const task = f.store.getTask(payload.task_id);
  assert.ok(task);
  return command(commandId, type, payload, task.revision);
}

function createTask(f: Fixture, commandId: string): string {
  const created = f.service.handleMasterCommand(
    command(commandId, "CreateTask", { resident_id: "holo" }),
  );
  return String(created.task_id);
}

function startTask(f: Fixture, taskId: string, commandId: string, content = "Implement a small change"): void {
  f.service.handleMasterCommand(
    taskCommand(f, commandId, "SendConversationMessage", {
      task_id: taskId,
      sender: "master",
      content,
    }),
  );
}

function makeCompletable(f: Fixture, taskId: string): void {
  f.store.refineTaskDefinition(taskId, "Ready Task", [{ id: "answer", text: "Requested work is explained.", required: true, verification_kind: null }]);
  const task = f.store.getTask(taskId)!;
  const response = createStoredRun(f, {
    task_id: taskId,
    capability_id: "holo",
    operation: "respond",
    kind: "response",
    control_epoch: task.control_epoch,
    input: {},
  });
  f.store.markRunRunning(response.id);
  f.store.recordRunResult(response.id, {
    state: "Completed",
    effects: "none",
    result: { disposition: "continue", completion: [{ criterion_id: "answer", artifact_ref: task.initial_message_id!, fingerprint: fingerprint(task.objective) }] },
  });
  const current = f.store.getTask(taskId)!;
  f.store.acknowledgeTaskContext(taskId, 1, current.wake_seq);
}

test("duplicate Master command is idempotent and an expired new command is rejected", () => {
  const f = fixture();
  try {
    const envelope = command("cmd-create-1", "CreateTask", { resident_id: "holo" });
    const first = f.service.handleMasterCommand(envelope);
    const second = f.service.handleMasterCommand(envelope);
    assert.deepEqual(second, first);
    assert.deepEqual(f.service.getMasterCommandReceipt("cmd-create-1"), first);
    assert.equal(f.store.listTasks().length, 1);

    const stale = new Date(Date.now() - 6 * 60 * 1000).toISOString();
    assert.throws(
      () => f.service.handleMasterCommand(command("expired-create", "CreateTask", { resident_id: "holo" }, undefined, stale)),
      /expired/,
    );

    const replay = { ...envelope, issued_at: stale };
    assert.deepEqual(f.service.handleMasterCommand(replay), first);
  } finally {
    f.close();
  }
});

test("Master content mutations require revision, unknown Residents are rejected, and draft Resume is rejected", () => {
  const f = fixture();
  try {
    assert.throws(
      () =>
        f.service.handleMasterCommand(
          command("cmd-unknown-resident", "CreateTask", { resident_id: "unknown" }),
        ),
      /resident not found/,
    );

    const taskId = createTask(f, "cmd-create-revision");
    assert.throws(
      () =>
        f.service.handleMasterCommand(
          command("cmd-no-revision", "SendConversationMessage", {
            task_id: taskId,
            sender: "master",
            content: "start",
          }),
        ),
      /expected_revision/,
    );
    assert.throws(
      () =>
        f.service.handleMasterCommand(
          taskCommand(f, "cmd-draft-resume", "SetTaskResume", {
            task_id: taskId,
            enabled: true,
          }),
        ),
      /no initial instruction/,
    );
  } finally {
    f.close();
  }
});

test("Pause/Resume and Resume ON/OFF are independent", () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-2");
    startTask(f, taskId, "cmd-start-2");
    assert.equal(f.store.getTask(taskId)?.title, "Implement a small change");
    assert.equal(f.store.getTask(taskId)?.wake_seq, 0);

    f.service.handleMasterCommand(
      taskCommand(f, "cmd-resume-on-2", "SetTaskResume", { task_id: taskId, enabled: true }),
    );
    f.service.handleMasterCommand(command("cmd-pause-2", "PauseTask", { task_id: taskId }));

    let task = f.store.getTask(taskId)!;
    assert.equal(task.state, "Paused");
    assert.equal(task.resume_enabled, true);
    const pausedWake = task.wake_seq;

    f.service.handleMasterCommand(
      taskCommand(f, "cmd-resume-task-2", "ResumeTask", { task_id: taskId }),
    );
    task = f.store.getTask(taskId)!;
    assert.equal(task.state, "Running");
    assert.equal(task.resume_enabled, true);
    assert.equal(task.wake_seq, pausedWake + 1);
  } finally {
    f.close();
  }
});

test("restart recovery pauses Tasks, preserves Resume, cancels pending response and interrupts running work", () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-3");
    startTask(f, taskId, "cmd-start-3", "Keep working");
    f.service.handleMasterCommand(
      taskCommand(f, "cmd-resume-on-3", "SetTaskResume", { task_id: taskId, enabled: true }),
    );

    const task = f.store.getTask(taskId)!;
    const pendingResponse = createStoredRun(f, {
      task_id: taskId,
      capability_id: "holo",
      operation: "respond",
      kind: "response",
      control_epoch: task.control_epoch,
      input: {},
    });
    f.store.markRunRunning(pendingResponse.id);
    const action = createStoredRun(f, {
      task_id: taskId,
      capability_id: "fake",
      operation: "work",
      kind: "action",
      parent_run_id: pendingResponse.id,
      control_epoch: task.control_epoch,
      input: { value: 1 },
    });
    f.store.markRunRunning(action.id);
    const containedRead = createStoredRun(f, {
      task_id: taskId,
      capability_id: "fake",
      operation: "read",
      kind: "action",
      parent_run_id: pendingResponse.id,
      control_epoch: task.control_epoch,
      input: { path: "safe.txt" },
    }, "none");
    f.store.markRunRunning(containedRead.id);
    const pendingAction = createStoredRun(f, {
      task_id: taskId,
      capability_id: "fake",
      operation: "later",
      kind: "action",
      parent_run_id: pendingResponse.id,
      control_epoch: task.control_epoch,
      input: { value: 2 },
    });

    f.store.recoverAfterRestart();

    assert.equal(f.store.getTask(taskId)?.state, "Paused");
    assert.equal(f.store.getTask(taskId)?.resume_enabled, true);
    assert.equal(f.store.getRun(pendingResponse.id)?.state, "Interrupted");
    assert.equal(f.store.getRun(action.id)?.state, "Interrupted");
    assert.equal(f.store.getRun(action.id)?.effects, "unknown");
    assert.equal(f.store.getRun(containedRead.id)?.state, "Interrupted");
    assert.equal(f.store.getRun(containedRead.id)?.effects, "none");
    assert.equal(f.store.getRun(containedRead.id)?.cleanup_state, "clear");
    assert.equal(f.store.getRun(pendingAction.id)?.state, "Cancelled");

    f.store.recordRunResult(action.id, {
      state: "Completed",
      effects: "applied",
      result: { late: true },
    });
    assert.equal(f.store.getRun(action.id)?.state, "Interrupted");
    assert.equal(f.store.getRun(action.id)?.effects, "unknown");
    assert.equal(f.store.getRun(action.id)?.cleanup_state, "unknown");
  } finally {
    f.close();
  }
});

test("Pause accepts a late Run result without reviving the Task", () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-4");
    startTask(f, taskId, "cmd-start-4", "Run a command");
    const task = f.store.getTask(taskId)!;
    const run = createStoredRun(f, {
      task_id: taskId,
      capability_id: "fake",
      operation: "work",
      kind: "action",
      control_epoch: task.control_epoch,
      input: { value: 2 },
    });
    f.store.markRunRunning(run.id);
    const pending = createStoredRun(f, {
      task_id: taskId,
      capability_id: "fake",
      operation: "later",
      kind: "action",
      control_epoch: task.control_epoch,
      input: { value: 3 },
    });

    f.service.handleMasterCommand(command("cmd-pause-4", "PauseTask", { task_id: taskId }));
    assert.equal(f.store.getRun(pending.id)?.state, "Cancelled");
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

test("Cancel closes pending Runs and Requests without pretending a running Run stopped", () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-cancel");
    startTask(f, taskId, "cmd-start-cancel");
    const task = f.store.getTask(taskId)!;
    const pending = createStoredRun(f, {
      task_id: taskId,
      capability_id: "fake",
      operation: "pending",
      kind: "action",
      control_epoch: task.control_epoch,
      input: {},
    });
    const running = createStoredRun(f, {
      task_id: taskId,
      capability_id: "fake",
      operation: "running",
      kind: "action",
      control_epoch: task.control_epoch,
      input: {},
    });
    f.store.markRunRunning(running.id);
    const request = f.store.createMasterRequest({
      task_id: taskId,
      run_id: running.id,
      kind: "input",
      prompt: "answer?",
    });

    f.service.handleMasterCommand(command("cmd-cancel", "CancelTask", { task_id: taskId }));
    const snapshot = f.store.snapshot() as { pending_requests: unknown[] };

    assert.equal(f.store.getTask(taskId)?.state, "Cancelled");
    assert.equal(f.store.getRun(pending.id)?.state, "Cancelled");
    assert.equal(f.store.getRun(running.id)?.state, "Running");
    assert.equal(snapshot.pending_requests.length, 0);
    assert.throws(
      () => f.store.resolveMasterRequest(request.id, { text: "late answer" }, 2),
      /not pending/,
    );
  } finally {
    f.close();
  }
});

test("Task completion rejects unhandled work and succeeds only after response context is handled", () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-complete");
    startTask(f, taskId, "cmd-start-complete", "Finish this correctly");

    assert.throws(
      () =>
        f.service.handleMasterCommand(
          taskCommand(f, "cmd-premature-complete", "CompleteTask", {
            task_id: taskId,
            result_summary: "done",
          }),
        ),
      /completion criteria|unhandled|completed response/,
    );

    makeCompletable(f, taskId);
    f.service.handleMasterCommand(
      taskCommand(f, "cmd-late-instruction", "SendConversationMessage", {
        task_id: taskId,
        sender: "master",
        content: "Also verify the final state.",
      }),
    );
    assert.throws(
      () =>
        f.service.handleMasterCommand(
          taskCommand(f, "cmd-complete-before-late-instruction", "CompleteTask", {
            task_id: taskId,
            result_summary: "done",
          }),
        ),
      /unhandled Master instructions/,
    );
    const afterLateInstruction = f.store.getTask(taskId)!;
    f.store.acknowledgeTaskContext(taskId, 2, afterLateInstruction.wake_seq);

    assert.throws(
      () =>
        f.service.handleMasterCommand(
          taskCommand(f, "cmd-empty-complete", "CompleteTask", {
            task_id: taskId,
            result_summary: "   ",
          }),
        ),
      /result summary is required/,
    );
    const completed = f.service.handleMasterCommand(
      taskCommand(f, "cmd-complete", "CompleteTask", {
        task_id: taskId,
        result_summary: "done",
      }),
    );
    assert.equal((completed.task as { state: string }).state, "Completed");
  } finally {
    f.close();
  }
});

test("Master Request resolution requires its revision and wakes only a Running Task", () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-request");
    startTask(f, taskId, "cmd-start-request");
    const request = f.store.createMasterRequest({
      task_id: taskId,
      kind: "input",
      prompt: "Choose one",
    });

    assert.throws(
      () =>
        f.service.handleMasterCommand(
          command("cmd-resolve-no-revision", "ResolveMasterRequest", {
            request_id: request.id,
            answer: { text: "A" },
          }),
        ),
      /expected_revision/,
    );

    const before = f.store.getTask(taskId)!.wake_seq;
    f.service.handleMasterCommand(
      command(
        "cmd-resolve-running",
        "ResolveMasterRequest",
        { request_id: request.id, answer: { text: "A" } },
        1,
      ),
    );
    assert.equal(f.store.getTask(taskId)!.wake_seq, before + 1);

    const request2 = f.store.createMasterRequest({
      task_id: taskId,
      kind: "input",
      prompt: "Choose again",
    });
    f.service.handleMasterCommand(command("cmd-pause-request", "PauseTask", { task_id: taskId }));
    const pausedWake = f.store.getTask(taskId)!.wake_seq;
    f.service.handleMasterCommand(
      command(
        "cmd-resolve-paused",
        "ResolveMasterRequest",
        { request_id: request2.id, answer: { text: "B" } },
        1,
      ),
    );
    assert.equal(f.store.getTask(taskId)!.wake_seq, pausedWake);
  } finally {
    f.close();
  }
});

test("ordinary Task Chat does not resolve a pending Master Request", () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-chat-boundary");
    startTask(f, taskId, "cmd-start-chat-boundary", "Initial instruction");
    f.store.createMasterRequest({
      task_id: taskId,
      kind: "input",
      prompt: "Choose one",
    });

    f.service.handleMasterCommand(
      taskCommand(f, "cmd-normal-chat-boundary", "SendConversationMessage", {
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

test("Failure resolution cannot substitute text for Adapter effects and cleanup evidence", () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-resolution");
    startTask(f, taskId, "cmd-start-resolution");
    makeCompletable(f, taskId);

    const task = f.store.getTask(taskId)!;
    const failedRun = createStoredRun(f, {
      task_id: taskId,
      capability_id: "fake",
      operation: "uncertain",
      kind: "action",
      control_epoch: task.control_epoch,
      input: {},
    });
    f.store.markRunRunning(failedRun.id);
    f.store.recordRunResult(failedRun.id, {
      state: "Failed",
      effects: "unknown",
      cleanup_state: "unknown",
      error: { message: "lost contact" },
    });

    assert.throws(() => f.store.completeTask(taskId, "done"), /unresolved side effects/);
    assert.throws(() => f.store.resolveRunFailure(taskId, failedRun.id, "not_needed",
      "AI claims nothing changed"), /must be reconciled by the Adapter/);
    // A late Adapter observation confirms effects and actual cleanup separately.
    f.store.recordRunResult(failedRun.id, {
      state: "Cancelled", effects: "none", cleanup_state: "clear",
      result: { target_unchanged: true, process_exited: true },
    });
    assert.equal(f.store.getRun(failedRun.id)?.state, "Failed");
    assert.throws(() => f.store.completeTask(taskId, "done"), /unresolved failed runs/);
    const resolved = f.store.resolveRunFailure(
      taskId,
      failedRun.id,
      "not_needed",
      "Adapter confirmed no effects; the cancelled operation is no longer needed.",
    );
    assert.equal(resolved.failure_resolution, "not_needed");
    assert.equal(resolved.effects, "none");
    assert.equal(resolved.cleanup_state, "clear");

    const completed = f.store.completeTask(taskId, "done");
    assert.equal(completed.state, "Completed");
  } finally {
    f.close();
  }
});

test("Capability Engine preserves declared side-effect boundaries on unexpected adapter errors", async () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-engine");
    startTask(f, taskId, "cmd-start-engine");
    const task = f.store.getTask(taskId)!;

    const registry = new CapabilityRegistry();
    const ok: Capability = {
      id: "ok",
      operations: new Map([["work", { side_effects: "none" as const }]]),
      availability: () => ({ state: "ready" }),
      invoke: async (_operation, input) => ({
        state: "Completed",
        effects: "none",
        result: { echoed: input },
      }),
    };
    const fail: Capability = {
      id: "fail",
      operations: new Map([["work", { side_effects: "possible" as const }]]),
      availability: () => ({ state: "ready" }),
      invoke: async () => {
        throw new Error("boom");
      },
    };
    const readFail: Capability = {
      id: "read-fail",
      operations: new Map([["read", { side_effects: "none" as const }]]),
      availability: () => ({ state: "ready" }),
      invoke: async () => {
        throw new Error("read boom");
      },
    };
    registry.register(ok);
    registry.register(fail);
    registry.register(readFail);
    const engine = new TaskEngine(f.store, registry);

    const goodRun = engine.createRun({
      task_id: taskId,
      capability_id: "ok",
      operation: "work",
      kind: "action",
      control_epoch: task.control_epoch,
      input: { value: 7 },
    });
    await engine.dispatchRun(goodRun.id);
    assert.equal(f.store.getRun(goodRun.id)?.state, "Completed");

    const badRun = engine.createRun({
      task_id: taskId,
      capability_id: "fail",
      operation: "work",
      kind: "action",
      control_epoch: task.control_epoch,
      input: {},
      side_effects: "none",
    } as Parameters<TaskEngine["createRun"]>[0] & { side_effects: "none" });
    assert.equal(f.store.getRun(badRun.id)?.side_effects, "possible");
    await assert.rejects(() => engine.dispatchRun(badRun.id), /boom/);
    const failed = f.store.getRun(badRun.id)!;
    assert.equal(failed.state, "Failed");
    assert.equal(failed.effects, "unknown");
    assert.equal(failed.cleanup_state, "unknown");
    assert.equal(failed.failure_resolution, "unresolved");

    const readRun = engine.createRun({
      task_id: taskId,
      capability_id: "read-fail",
      operation: "read",
      kind: "action",
      control_epoch: task.control_epoch,
      input: {},
    });
    await assert.rejects(() => engine.dispatchRun(readRun.id), /read boom/);
    const readFailure = f.store.getRun(readRun.id)!;
    assert.equal(readFailure.state, "Failed");
    assert.equal(readFailure.effects, "none");
    assert.equal(readFailure.cleanup_state, "clear");
  } finally {
    f.close();
  }
});

test("dispatch validation closes impossible Runs but keeps temporarily unavailable Runs pending", async () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-dispatch-validation");
    startTask(f, taskId, "cmd-start-dispatch-validation");
    const task = f.store.getTask(taskId)!;
    const registry = new CapabilityRegistry();
    registry.register({
      id: "limited",
      operations: new Map([["work", { side_effects: "none" as const }]]),
      availability: () => ({ state: "blocked", reason: "not ready yet" }),
      invoke: async () => ({ state: "Completed", effects: "none" }),
    });
    const engine = new TaskEngine(f.store, registry);

    const missing = createStoredRun(f, {
      task_id: taskId,
      capability_id: "missing",
      operation: "work",
      kind: "action",
      control_epoch: task.control_epoch,
      input: {},
    });
    await assert.rejects(() => engine.dispatchRun(missing.id), /capability not found/);
    assert.equal(f.store.getRun(missing.id)?.state, "Failed");

    const unsupported = createStoredRun(f, {
      task_id: taskId,
      capability_id: "limited",
      operation: "unknown",
      kind: "action",
      control_epoch: task.control_epoch,
      input: {},
    });
    await assert.rejects(() => engine.dispatchRun(unsupported.id), /unsupported capability operation/);
    assert.equal(f.store.getRun(unsupported.id)?.state, "Failed");

    const mismatched = createStoredRun(f, {
      task_id: taskId,
      capability_id: "limited",
      operation: "work",
      kind: "action",
      control_epoch: task.control_epoch,
      input: {},
    });
    await assert.rejects(() => engine.dispatchRun(mismatched.id), /side-effect policy/);
    assert.equal(f.store.getRun(mismatched.id)?.state, "Failed");

    const blocked = engine.createRun({
      task_id: taskId,
      capability_id: "limited",
      operation: "work",
      kind: "action",
      control_epoch: task.control_epoch,
      input: {},
    });
    await assert.rejects(() => engine.dispatchRun(blocked.id), /capability is blocked/);
    assert.equal(f.store.getRun(blocked.id)?.state, "Pending");
  } finally {
    f.close();
  }
});

test("stale control epochs cannot dispatch new work", () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "cmd-create-stale");
    startTask(f, taskId, "cmd-start-stale");
    const oldEpoch = f.store.getTask(taskId)!.control_epoch;
    const oldParent = createStoredRun(f, {
      task_id: taskId, capability_id: "fake", operation: "respond", kind: "response",
      control_epoch: oldEpoch, input: {},
    });
    f.store.markRunRunning(oldParent.id);
    f.service.handleMasterCommand(command("cmd-pause-stale", "PauseTask", { task_id: taskId }));
    f.service.handleMasterCommand(
      taskCommand(f, "cmd-resume-stale", "ResumeTask", { task_id: taskId }),
    );

    assert.throws(
      () =>
        createStoredRun(f, {
          task_id: taskId,
          capability_id: "fake",
          operation: "work",
          kind: "action",
          control_epoch: oldEpoch,
          input: {},
        }),
      /stale control epoch/,
    );
    assert.throws(() => createStoredRun(f, {
      task_id: taskId, capability_id: "fake", operation: "work", kind: "action",
      parent_run_id: oldParent.id, control_epoch: f.store.getTask(taskId)!.control_epoch, input: {},
    }), /invalid parent response run/);
  } finally {
    f.close();
  }
});

test("Approval gates only its fixed Run; rejection and Pause never leave executable proposals", () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "approval-create");
    startTask(f, taskId, "approval-start");
    const makeRun = () => createStoredRun(f, {
      task_id: taskId, capability_id: "fake", operation: "write", kind: "action",
      control_epoch: f.store.getTask(taskId)!.control_epoch, input: { path: "target" },
    });
    const rejected = makeRun();
    const unrelated = makeRun();
    const request = f.store.createMasterRequest({ task_id: taskId, run_id: rejected.id,
      kind: "approval", prompt: "Apply this change?" });
    assert.throws(() => f.store.markRunRunning(rejected.id), /requires Master approval/);
    assert.throws(() => f.service.handleMasterCommand(command("invalid-answer", "ResolveMasterRequest",
      { request_id: request.id, answer: { text: "yes" } }, 1)), /approved boolean/);
    const reject = command("reject-answer", "ResolveMasterRequest",
      { request_id: request.id, answer: { approved: false } }, 1);
    const answer = f.service.handleMasterCommand(reject);
    assert.equal(JSON.stringify(f.service.handleMasterCommand(reject)), JSON.stringify(answer));
    assert.equal(f.store.getRun(rejected.id)?.state, "Cancelled");
    assert.equal(f.store.getRun(unrelated.id)?.state, "Pending");
    assert.throws(() => f.store.markRunRunning(rejected.id), /not pending/);

    const approved = makeRun();
    const approval = f.store.createMasterRequest({ task_id: taskId, run_id: approved.id,
      kind: "approval", prompt: "Apply?" });
    f.service.handleMasterCommand(command("approve-answer", "ResolveMasterRequest",
      { request_id: approval.id, answer: { approved: true } }, 1));
    f.store.markRunRunning(approved.id);
    const pendingApproval = f.store.createMasterRequest({ task_id: taskId, run_id: unrelated.id,
      kind: "approval", prompt: "Apply later?" });
    f.service.handleMasterCommand(command("approval-pause", "PauseTask", { task_id: taskId }));
    assert.equal(f.store.getRun(approved.id)?.state, "Running");
    assert.equal(f.store.getRun(unrelated.id)?.state, "Cancelled");
    assert.throws(() => f.store.resolveMasterRequest(pendingApproval.id, { approved: true }, 2), /not pending/);
  } finally { f.close(); }
});

test("Engine preserves late Adapter observations and blocks retries of unknown execution", async () => {
  const f = fixture();
  try {
    const taskId = createTask(f, "late-create");
    startTask(f, taskId, "late-start");
    const registry = new CapabilityRegistry();
    let finish!: (value: Awaited<ReturnType<Capability["invoke"]>>) => void;
    registry.register({ id: "late", operations: new Map([["work", { side_effects: "possible" }]]),
      availability: () => ({ state: "ready" }),
      invoke: () => new Promise((resolve) => { finish = resolve; }),
    });
    const engine = new TaskEngine(f.store, registry);
    const runInput = { task_id: taskId, capability_id: "late", operation: "work",
      kind: "action" as const, control_epoch: f.store.getTask(taskId)!.control_epoch, input: {} };
    const run = engine.createRun(runInput);
    const executing = engine.dispatchRun(run.id);
    f.store.recordRunResult(run.id, { state: "Cancelled", effects: "unknown", cleanup_state: "unknown" });
    assert.throws(() => engine.createRun({ ...runInput, retry_of: run.id }), /reconciled effects/);
    finish({ state: "Completed", effects: "partial", cleanup_state: "clear", result: { changed: "one file" } });
    await executing;
    const saved = f.store.getRun(run.id)!;
    assert.equal(saved.state, "Cancelled");
    assert.equal(saved.effects, "partial");
    assert.equal(saved.cleanup_state, "clear");
    assert.equal(JSON.parse(saved.supplemental_result_json!)[0].result.changed, "one file");
  } finally { f.close(); }
});
