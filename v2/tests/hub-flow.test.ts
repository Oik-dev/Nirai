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

test("AwaitMasterReply hands the next turn to normal Task Chat", async () => {
  const f = await setup();
  try {
    f.runtime.store.setTaskResume(f.taskId, true);
    const waiting = f.call("AwaitMasterReply");
    assert.equal(waiting.awaiting_master, true);
    f.runtime.store.syncHoloTurn(f.turn.id, "AとBのどちらにしますか？", true);

    assert.equal((f.runtime.store.snapshot().pending_requests as unknown[]).length, 0);
    assert.equal(f.runtime.store.getHoloTurn(f.turn.id)?.await_master, true);
    assert.equal(f.runtime.store.reserveHoloTurn(f.taskId), null);

    f.runtime.store.addMasterMessage(f.taskId, "Aで");
    const next = f.runtime.store.reserveHoloTurn(f.taskId);
    assert.ok(next);
    assert.equal(f.runtime.store.getHoloInput(next.id), "Aで");
  } finally {
    await f.close();
  }
});

test("AwaitMasterReply does not create a phantom wait when no assistant reply is saved", async () => {
  const f = await setup();
  try {
    f.runtime.store.setTaskResume(f.taskId, true);
    f.call("AwaitMasterReply");
    f.runtime.store.endHoloTurn(f.turn.id, "Session Error");

    const next = f.runtime.store.reserveHoloTurn(f.taskId);
    assert.ok(next);
    assert.notEqual(next.id, f.turn.id);
  } finally {
    await f.close();
  }
});

test("a Master Chat reply written while Paused becomes the next raw Holo input after Resume", async () => {
  const f = await setup();
  try {
    f.call("AwaitMasterReply");
    f.runtime.store.syncHoloTurn(f.turn.id, "続行方針を教えてください。", true);
    f.runtime.store.pauseTask(f.taskId);
    f.runtime.store.addMasterMessage(f.taskId, "paused answer");

    assert.equal(f.runtime.store.reserveHoloTurn(f.taskId), null);
    f.runtime.store.resumeTask(f.taskId);
    const next = f.runtime.store.reserveHoloTurn(f.taskId);
    assert.ok(next);
    assert.equal(f.runtime.store.getHoloInput(next.id), "paused answer");
  } finally {
    await f.close();
  }
});

test("CompleteTask stages completion until the final assistant reply is saved", async () => {
  const f = await setup();
  try {
    const result = f.call("CompleteTask", { result_summary: "done" });
    assert.equal(result.completion_pending, true);
    assert.equal(result.reply_required, true);
    assert.match(String(result.instruction), /full requested answer/);
    assert.equal(f.runtime.store.getTask(f.taskId)?.state, "Running");
    assert.equal(f.runtime.store.getHoloTurn(f.turn.id)?.completion_summary, "done");

    assert.throws(
      () => f.runtime.service.handleTurnCommand(
        f.turn.id,
        envelope("AwaitMasterReply"),
      ),
      /stale|awaiting its final assistant reply/,
    );
    assert.throws(
      () => f.runtime.store.addMasterMessage(f.taskId, "late instruction"),
      /awaiting final assistant reply/,
    );

    f.runtime.store.syncHoloTurn(f.turn.id, "完了しました。", true);
    assert.equal(f.runtime.store.getTask(f.taskId)?.state, "Completed");
    const messages = f.runtime.store.snapshot().messages as Array<{ content: string }>;
    assert.equal(messages.at(-1)?.content, "完了しました。");
  } finally {
    await f.close();
  }
});

test("an interrupted final reply abandons staged completion instead of creating a Completed Task without Chat", async () => {
  const f = await setup();
  try {
    f.call("CompleteTask", { result_summary: "done" });
    f.runtime.store.endHoloTurn(f.turn.id, "Session Error");

    assert.equal(f.runtime.store.getTask(f.taskId)?.state, "Running");
    assert.equal(f.runtime.store.getHoloTurn(f.turn.id)?.completion_summary, null);
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
