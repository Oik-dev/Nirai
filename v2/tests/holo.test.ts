import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { controlCommand } from "../src/bridge/client.js";
import { HubRuntime } from "../src/hub/runtime.js";
import type { HoloDispatch, HoloObservation } from "../src/shared/holo.js";
import type { HubCommandEnvelope } from "../src/shared/types.js";

const ready: HoloObservation = {
  state: "ready",
  reason: "fixture",
  url: "https://chatgpt.com/",
  conversation_id: null,
  busy: false,
  draft: false,
};

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function until(predicate: () => unknown): Promise<void> {
  for (let i = 0; i < 300; i++) {
    if (predicate()) return;
    await delay(10);
  }
  throw new Error("Holo state did not settle");
}

const envelope = (type: string, payload: Record<string, unknown> = {}): HubCommandEnvelope => ({
  protocol_version: 1,
  command_id: randomUUID(),
  issued_at: new Date().toISOString(),
  target: null,
  type,
  payload,
});

async function setup() {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-holo-"));
  const runtime = await HubRuntime.start(root);
  const sent: HoloDispatch[] = [];

  runtime.holo.send = message => {
    if (message.type === "holo:dispatch") sent.push(message.dispatch);
  };
  runtime.store.updateSettings({ holo_app_name: "nirai-v2" }, 1);
  runtime.holo.observe(ready);

  const start = (content = "Do the work") => {
    const task = runtime.store.createTask("holo");
    runtime.store.addMasterMessage(task.id, content);
    runtime.engine.schedule();
    return task.id;
  };
  const call = (dispatch: HoloDispatch, type: string, payload: Record<string, unknown> = {}) =>
    controlCommand(join(root, "control", "connection.json"), dispatch.turn_id, envelope(type, payload));

  return {
    root,
    runtime,
    sent,
    start,
    call,
    async close() {
      await runtime.close();
      rmSync(root, { recursive: true, force: true });
    },
  };
}

test("Holo Turn is the authority boundary and prompt contains only the Nirai context contract", async () => {
  const f = await setup();
  try {
    const taskId = f.start("Read the task and continue");
    await until(() => f.sent.length === 1);
    const dispatch = f.sent[0]!;

    assert.equal(dispatch.task_id, taskId);
    assert.match(dispatch.prompt, /^@nirai-v2\nturn_id=/);
    assert.match(dispatch.prompt, /GetTaskContext/);
    assert.equal(dispatch.prompt.includes("WORLD_RULES"), false);
    assert.equal(dispatch.prompt.includes("TASK_CONTEXT"), false);
    assert.ok(dispatch.prompt.length < 220);

    const context = await f.call(dispatch, "GetTaskContext");
    assert.match(String(context.world_rules), /機能美/);
    assert.equal((context.task as { objective: string }).objective, "Read the task and continue");
    assert.deepEqual(
      (context.messages as Array<{ sender: string; content: string }>).map(item => [item.sender, item.content]),
      [["master", "Read the task and continue"]],
    );
    assert.ok((context.capabilities as Array<{ id: string }>).some(item => item.id === "local"));

    f.runtime.store.pauseTask(taskId);
    await assert.rejects(() => f.call(dispatch, "GetTaskContext"), /stale|unauthorized/);
  } finally {
    await f.close();
  }
});

test("assistant without GetTaskContext is discarded and the unhandled instruction retries on a fresh Conversation", async () => {
  const f = await setup();
  try {
    const taskId = f.start("context handshake required");
    await until(() => f.sent.length === 1);
    const first = f.sent[0]!;

    f.runtime.holo.delivered(first.turn_id, {
      status: "confirmed",
      url: "https://chatgpt.com/c/stale-schema",
    });
    f.runtime.holo.assistant(first.turn_id, "tool schema mismatch", "https://chatgpt.com/c/stale-schema");

    await until(() => f.sent.length === 2);
    const snapshot = f.runtime.store.snapshot();
    const messages = snapshot.messages as Array<{ sender: string; content: string }>;
    assert.deepEqual(messages.map(item => item.content), ["context handshake required"]);
    assert.equal(f.runtime.store.getTask(taskId)?.state, "Running");
    assert.equal(f.runtime.store.getHoloTurn(first.turn_id)?.end_reason, "Task Context was not loaded");
    assert.equal(f.sent[1]!.target_conversation_id, null);
  } finally {
    await f.close();
  }
});

test("ChatGPT assistant text is saved unchanged and ends only the Holo Turn", async () => {
  const f = await setup();
  try {
    const taskId = f.start("Say exactly what happened");
    await until(() => f.sent.length === 1);
    const first = f.sent[0]!;

    await f.call(first, "GetTaskContext");
    f.runtime.holo.delivered(first.turn_id, {
      status: "confirmed",
      url: "https://chatgpt.com/c/fixture-one",
    });
    const answer = "途中報告です。\n次の処理へ進みます。";
    f.runtime.holo.assistant(first.turn_id, answer, "https://chatgpt.com/c/fixture-one");

    const snapshot = f.runtime.store.snapshot();
    const messages = snapshot.messages as Array<{ sender: string; content: string }>;
    assert.equal(messages.at(-1)?.content, answer);
    assert.equal(f.runtime.store.getTask(taskId)?.state, "Running");
    assert.equal(f.runtime.store.getHoloTurn(first.turn_id)?.end_reason, "assistant");

    await delay(50);
    assert.equal(f.sent.length, 1);

    f.runtime.store.addMasterMessage(taskId, "Continue");
    f.runtime.engine.schedule();
    await until(() => f.sent.length === 2);
    assert.equal(f.sent[1]!.target_conversation_id, "fixture-one");
  } finally {
    await f.close();
  }
});

test("Resume ON continues every unfinished Task until CompleteTask or Master input", async () => {
  const f = await setup();
  try {
    const taskId = f.start("Keep going until complete");
    f.runtime.store.setTaskResume(taskId, true);
    f.runtime.engine.schedule();
    await until(() => f.sent.length === 1);

    const first = f.sent[0]!;
    await f.call(first, "GetTaskContext");
    f.runtime.holo.assistant(first.turn_id, "step one");
    await until(() => f.sent.length === 2);

    const second = f.sent[1]!;
    await f.call(second, "GetTaskContext");
    await f.call(second, "RequestMasterInput", { prompt: "Need a decision" });
    f.runtime.holo.assistant(second.turn_id, "Masterの回答待ちです。");
    await delay(50);
    assert.equal(f.sent.length, 2);

    const request = (f.runtime.store.snapshot().pending_requests as Array<{ id: string; revision: number }>)[0]!;
    f.runtime.service.handleMasterCommand({
      protocol_version: 1,
      command_id: randomUUID(),
      issued_at: new Date().toISOString(),
      type: "ResolveMasterRequest",
      target: request.id,
      expected_revision: request.revision,
      payload: { request_id: request.id, answer: { text: "continue" } },
    });
    await until(() => f.sent.length === 3);

    const third = f.sent[2]!;
    await f.call(third, "GetTaskContext");
    await f.call(third, "CompleteTask", { result_summary: "done" });
    f.runtime.holo.assistant(third.turn_id, "完了しました。");
    assert.equal(f.runtime.store.getTask(taskId)?.state, "Completed");
    await delay(50);
    assert.equal(f.sent.length, 3);
  } finally {
    await f.close();
  }
});

test("Timeout-class failures close the Turn and Resume uses the same unfinished-Task rule", async () => {
  const f = await setup();
  try {
    const taskId = f.start("Continue across a broken session");
    f.runtime.store.setTaskResume(taskId, true);
    f.runtime.engine.schedule();
    await until(() => f.sent.length === 1);

    const first = f.sent[0]!;
    f.runtime.holo.failed(first.turn_id, "Session Error");
    await until(() => f.sent.length === 2);

    assert.equal(f.runtime.store.getTask(taskId)?.state, "Running");
    assert.equal(f.runtime.store.getHoloTurn(first.turn_id)?.end_reason, "Session Error");
    assert.notEqual(f.sent[1]!.turn_id, first.turn_id);
  } finally {
    await f.close();
  }
});

test("stored Holo command receipts remain readable after Turn authority ends", async () => {
  const f = await setup();
  try {
    f.start("receipt");
    await until(() => f.sent.length === 1);
    const dispatch = f.sent[0]!;
    const savedCommand = envelope("GetTaskContext");
    const path = join(f.root, "control", "connection.json");

    const saved = await controlCommand(path, dispatch.turn_id, savedCommand);
    f.runtime.holo.assistant(dispatch.turn_id, "done with this turn");

    assert.deepEqual(await controlCommand(path, dispatch.turn_id, savedCommand), saved);
    await assert.rejects(
      () => controlCommand(path, dispatch.turn_id, envelope("GetTaskContext")),
      /stale|unauthorized/,
    );
  } finally {
    await f.close();
  }
});
