import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { CapabilityRegistry } from "../src/hub/capability.js";
import { HubRuntime } from "../src/hub/runtime.js";
import type { HubCommandEnvelope } from "../src/shared/types.js";

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function until(predicate: () => unknown): Promise<void> {
  for (let i = 0; i < 300; i++) {
    if (predicate()) return;
    await delay(10);
  }
  throw new Error("saved state did not reach the expected boundary");
}

const envelope = (type: string, payload: Record<string, unknown> = {}): HubCommandEnvelope => ({
  protocol_version: 1,
  command_id: randomUUID(),
  issued_at: new Date().toISOString(),
  target: null,
  type,
  payload,
});

async function setup(registry = new CapabilityRegistry()) {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-flow-"));
  const runtime = await HubRuntime.start(root, registry);
  const task = runtime.store.createTask("holo");
  runtime.store.addMasterMessage(task.id, "work");
  const turn = runtime.store.reserveHoloTurn(task.id)!;
  runtime.service.handleTurnCommand(turn.id, envelope("GetTaskContext"));
  const call = (type: string, payload: Record<string, unknown> = {}) =>
    runtime.service.handleTurnCommand(turn.id, envelope(type, payload));
  return {
    root,
    runtime,
    taskId: task.id,
    turn,
    call,
    async close() {
      await runtime.close();
      rmSync(root, { recursive: true, force: true });
    },
  };
}

test("one Holo Turn can use multiple Action Runs without response bookkeeping", async () => {
  const registry = new CapabilityRegistry();
  registry.register({
    id: "fixture",
    operations: new Map([["read", { side_effects: "none" }]]),
    availability: () => ({ state: "ready" }),
    invoke: async (_operation, input) => ({
      state: "Completed",
      effects: "none",
      cleanup_state: "clear",
      result: { value: input },
    }),
  });

  const f = await setup(registry);
  try {
    const first = String(f.call("InvokeCapability", {
      capability_id: "fixture",
      operation: "read",
      input: { n: 1 },
    }).run_id);
    const second = String(f.call("InvokeCapability", {
      capability_id: "fixture",
      operation: "read",
      input: { n: 2 },
    }).run_id);

    await until(() => f.runtime.store.getRun(first)?.state === "Completed"
      && f.runtime.store.getRun(second)?.state === "Completed");

    assert.equal(f.runtime.store.getRun(first)?.turn_id, f.turn.id);
    assert.equal(f.runtime.store.getRun(second)?.turn_id, f.turn.id);
    assert.equal(f.runtime.store.listRuns(f.taskId).length, 2);
    assert.equal(f.runtime.store.listHoloTurns(f.taskId).length, 1);
  } finally {
    await f.close();
  }
});

test("RequestMasterInput blocks continuation until the answer is handed to a new Turn", async () => {
  const f = await setup();
  try {
    const requestId = String(f.call("RequestMasterInput", { prompt: "Choose" }).request_id);
    f.runtime.store.finishHoloTurn(f.turn.id, "Masterの回答待ちです。");

    const request = (f.runtime.store.snapshot().pending_requests as Array<{ id: string; revision: number }>)
      .find(item => item.id === requestId)!;
    assert.ok(request);

    f.runtime.service.handleMasterCommand({
      protocol_version: 1,
      command_id: randomUUID(),
      issued_at: new Date().toISOString(),
      type: "ResolveMasterRequest",
      target: request.id,
      expected_revision: request.revision,
      payload: { request_id: request.id, answer: { text: "go" } },
    });

    const next = f.runtime.store.reserveHoloTurn(f.taskId);
    assert.ok(next);
    const context = f.runtime.store.getTaskContext(next.id);
    const messages = context.messages as Array<{ sender: string; request_id?: string | null }>;
    assert.ok(messages.some(message => message.sender === "control" && message.request_id === requestId));
  } finally {
    await f.close();
  }
});

test("CompleteTask is the only Holo completion boundary and closes future authority", async () => {
  const f = await setup();
  try {
    const result = f.call("CompleteTask", { result_summary: "done" });
    assert.equal((result.task as { state: string }).state, "Completed");
    assert.equal(f.runtime.store.getTask(f.taskId)?.state, "Completed");

    assert.throws(
      () => f.runtime.service.handleTurnCommand(f.turn.id, envelope("GetTaskContext")),
      /stale/,
    );

    f.runtime.store.finishHoloTurn(f.turn.id, "完了しました。");
    const messages = f.runtime.store.snapshot().messages as Array<{ content: string }>;
    assert.equal(messages.at(-1)?.content, "完了しました。");
  } finally {
    await f.close();
  }
});

test("settled Action failure is history and does not require a recovery state", async () => {
  let fail = true;
  const registry = new CapabilityRegistry();
  registry.register({
    id: "fixture",
    operations: new Map([["work", { side_effects: "none" }]]),
    availability: () => ({ state: "ready" }),
    invoke: async () => fail
      ? { state: "Failed", effects: "none", cleanup_state: "clear", error: { message: "fixture" } }
      : { state: "Completed", effects: "none", cleanup_state: "clear", result: { summary: "ok" } },
  });

  const f = await setup(registry);
  try {
    const first = String(f.call("InvokeCapability", {
      capability_id: "fixture", operation: "work", input: {},
    }).run_id);
    await until(() => f.runtime.store.getRun(first)?.state === "Failed");

    fail = false;
    const second = String(f.call("InvokeCapability", {
      capability_id: "fixture", operation: "work", input: {},
    }).run_id);
    await until(() => f.runtime.store.getRun(second)?.state === "Completed");

    assert.equal(f.runtime.store.getRun(first)?.state, "Failed");
    assert.equal(f.runtime.store.getRun(second)?.state, "Completed");
    assert.equal(f.runtime.store.getTask(f.taskId)?.state, "Running");
  } finally {
    await f.close();
  }
});
