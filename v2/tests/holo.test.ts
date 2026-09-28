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
  task_id: null,
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

test("native Holo composer input is recorded through the Master command contract exactly once", async () => {
  const f = await setup();
  try {
    const task = f.runtime.store.createTask("holo");
    const input = {
      event_id: randomUUID(),
      task_id: task.id,
      content: "native composer input",
      issued_at: new Date().toISOString(),
    };

    const first = f.runtime.service.handleNativeHoloMessage(input);
    const repeated = f.runtime.service.handleNativeHoloMessage(input);
    assert.deepEqual(repeated, first);
    assert.throws(
      () => f.runtime.service.handleNativeHoloMessage({ ...input, content: "different content" }),
      /event_id conflict/,
    );

    const messages = (f.runtime.store.snapshot().messages as Array<{
      conversation_id: string;
      sender: string;
      content: string;
    }>).filter(message =>
      message.conversation_id === task.conversation_id
      && message.sender === "master"
      && message.content === input.content
    );
    assert.equal(messages.length, 1);

    await until(() => f.sent.length === 1);
    assert.match(f.sent[0]!.prompt, /native composer input$/);
  } finally {
    await f.close();
  }
});

test("Holo Turn sends the Master input as raw Web text and keeps Task authority in Nirai", async () => {
  const f = await setup();
  try {
    const taskId = f.start("Read the task and continue");
    await until(() => f.sent.length === 1);
    const dispatch = f.sent[0]!;

    assert.equal(f.runtime.store.getHoloTurn(dispatch.turn_id)?.task_id, taskId);
    assert.match(dispatch.prompt, /^@nirai-v2\nturn_id=/);
    assert.match(dispatch.prompt, /Read the task and continue$/);
    assert.equal(dispatch.prompt.includes("GetTaskContext"), false);
    assert.equal(dispatch.prompt.includes("WORLD_RULES"), false);
    assert.equal(dispatch.prompt.includes("TASK_CONTEXT"), false);

    await assert.rejects(() => f.call(dispatch, "GetTaskContext"), /unsupported Holo/);

    f.runtime.store.pauseTask(taskId);
    await assert.rejects(
      () => f.call(dispatch, "AwaitMasterReply"),
      /stale|unauthorized/,
    );
  } finally {
    await f.close();
  }
});

test("Master input becomes handled only when the native Web Turn is finalized", async () => {
  const f = await setup();
  try {
    const taskId = f.start("MCP may be missing");
    await until(() => f.sent.length === 1);
    const first = f.sent[0]!;

    f.runtime.holo.delivered(first.turn_id, "https://chatgpt.com/c/no-tool");
    assert.equal(f.runtime.store.getTask(taskId)?.handled_instruction_seq, 0);

    const answer = "Niraiのツールが見つかりません。";
    f.runtime.holo.sync(first.turn_id, answer, true);
    await delay(50);

    const messages = f.runtime.store.snapshot().messages as Array<{ content: string }>;
    assert.deepEqual(messages.map(item => item.content), ["MCP may be missing", answer]);
    assert.ok((f.runtime.store.getTask(taskId)?.handled_instruction_seq ?? 0) > 0);
    assert.equal(f.runtime.holo.availability().state, "ready");
    await delay(50);
    assert.equal(f.sent.length, 1, "Resume OFF must stop after the assistant reply");
  } finally {
    await f.close();
  }
});

test("one finalized Holo Turn stays one Hub Chat record and does not advance control state before completion", async () => {
  const f = await setup();
  try {
    const taskId = f.start("mirror the resident turn");
    await until(() => f.sent.length === 1);
    const turn = f.sent[0]!;

    f.runtime.holo.sync(turn.turn_id, "考え中…", false);
    let snapshot = f.runtime.store.snapshot();
    let messages = snapshot.messages as Array<{ id: string; sender: string; content: string; turn_id: string | null }>;
    const mirrored = messages.at(-1)!;
    assert.equal(mirrored.content, "考え中…");
    assert.equal(mirrored.turn_id, turn.turn_id);
    assert.equal(f.runtime.store.getHoloTurn(turn.turn_id)?.ended_at, null);
    assert.equal(f.runtime.store.getTask(taskId)?.handled_instruction_seq, 0);

    f.runtime.holo.sync(turn.turn_id, "考え中…\nツールを呼び出しました。", false);
    snapshot = f.runtime.store.snapshot();
    messages = snapshot.messages as Array<{ id: string; sender: string; content: string; turn_id: string | null }>;
    assert.equal(messages.length, 2);
    assert.equal(messages.at(-1)?.id, mirrored.id);
    assert.equal(messages.at(-1)?.content, "考え中…\nツールを呼び出しました。");
    assert.equal(f.runtime.store.getHoloTurn(turn.turn_id)?.ended_at, null);

    f.runtime.holo.sync(turn.turn_id, "考え中…\nツールを呼び出しました。\n最終回答", true);
    snapshot = f.runtime.store.snapshot();
    messages = snapshot.messages as Array<{ id: string; sender: string; content: string; turn_id: string | null }>;
    assert.equal(messages.length, 2);
    assert.equal(messages.at(-1)?.id, mirrored.id);
    assert.equal(messages.at(-1)?.content, "考え中…\nツールを呼び出しました。\n最終回答");
    assert.equal(f.runtime.store.getHoloTurn(turn.turn_id)?.end_reason, "assistant");
    assert.ok((f.runtime.store.getTask(taskId)?.handled_instruction_seq ?? 0) > 0);
  } finally {
    await f.close();
  }
});

test("a proven unsent Turn blocks Holo at the Adapter boundary until the page changes", async () => {
  const f = await setup();
  try {
    const taskId = f.start("send when the page recovers");
    f.runtime.store.setTaskResume(taskId, true);
    f.runtime.engine.schedule();
    await until(() => f.sent.length === 1);

    f.runtime.holo.ended({ turn_id: f.sent[0]!.turn_id, reason: "送信ボタンを確認できません", sent: false });
    await delay(50);
    assert.equal(f.sent.length, 1);
    assert.equal(f.runtime.holo.availability().state, "blocked");

    f.runtime.holo.observe(ready);
    await until(() => f.sent.length === 2);
  } finally {
    await f.close();
  }
});

test("one Holo Web view serves one Turn at a time across Tasks", async () => {
  const f = await setup();
  try {
    f.start("first task");
    f.start("second task");
    await until(() => f.sent.length === 1);
    await delay(50);
    assert.equal(f.sent.length, 1);

    f.runtime.holo.sync(f.sent[0]!.turn_id, "first done", true);
    await until(() => f.sent.length === 2);
    assert.notEqual(f.runtime.store.getHoloTurn(f.sent[1]!.turn_id)?.task_id, f.runtime.store.getHoloTurn(f.sent[0]!.turn_id)?.task_id);
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

    f.runtime.holo.delivered(first.turn_id, "https://chatgpt.com/c/fixture-one");
    const answer = "途中報告です。\n次の処理へ進みます。";
    f.runtime.holo.sync(first.turn_id, answer, true);

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
    f.runtime.holo.sync(first.turn_id, "step one", true);
    await until(() => f.sent.length === 2);

    const second = f.sent[1]!;
    await f.call(second, "AwaitMasterReply");
    f.runtime.holo.sync(second.turn_id, "続ける方針を教えてください。", true);
    await delay(50);
    assert.equal(f.sent.length, 2);
    assert.equal((f.runtime.store.snapshot().pending_requests as unknown[]).length, 0);

    f.runtime.store.addMasterMessage(taskId, "continue");
    f.runtime.engine.schedule();
    await until(() => f.sent.length === 3);

    const third = f.sent[2]!;
    assert.match(third.prompt, /continue$/);
    const completion = await f.call(third, "CompleteTask", { result_summary: "done" }) as {
      completion_pending?: boolean;
      reply_required?: boolean;
      instruction?: string;
    };
    assert.equal(completion.completion_pending, true);
    assert.equal(completion.reply_required, true);
    assert.match(completion.instruction ?? "", /full requested answer/);
    assert.equal(f.runtime.store.getTask(taskId)?.state, "Running");
    const finalReply = "依頼された内容への最終回答です。";
    f.runtime.holo.sync(third.turn_id, finalReply, true);
    assert.equal(f.runtime.store.getTask(taskId)?.state, "Completed");
    const messages = f.runtime.store.snapshot().messages as Array<{ content: string }>;
    assert.equal(messages.at(-1)?.content, finalReply);
    await delay(50);
    assert.equal(f.sent.length, 3);
  } finally {
    await f.close();
  }
});

test("Master native Stop ends only the Turn and blocks Resume until a new Master instruction", async () => {
  const f = await setup();
  try {
    const taskId = f.start("Stop this response when Master asks");
    f.runtime.store.setTaskResume(taskId, true);
    f.runtime.engine.schedule();
    await until(() => f.sent.length === 1);

    const first = f.sent[0]!;
    f.runtime.holo.sync(first.turn_id, "途中までの回答", false);
    f.runtime.holo.ended({ turn_id: first.turn_id, reason: "master_stop", sent: true });

    await delay(100);
    assert.equal(f.sent.length, 1, "Resume ON must respect an explicit Master Stop");
    assert.equal(f.runtime.store.getTask(taskId)?.state, "Running");
    assert.equal(f.runtime.store.getTask(taskId)?.resume_enabled, true);
    assert.equal(f.runtime.store.getHoloTurn(first.turn_id)?.end_reason, "master_stop");
    assert.equal((f.runtime.store.snapshot().messages as Array<{ content: string }>).at(-1)?.content, "途中までの回答");

    f.runtime.store.addMasterMessage(taskId, "この方針で続けて");
    f.runtime.engine.schedule();
    await until(() => f.sent.length === 2);
    assert.notEqual(f.sent[1]!.turn_id, first.turn_id);
    assert.match(f.sent[1]!.prompt, /この方針で続けて$/);
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
    f.runtime.holo.ended({ turn_id: first.turn_id, reason: "Session Error", sent: true });
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
    const savedCommand = envelope("AwaitMasterReply");
    const path = join(f.root, "control", "connection.json");

    const saved = await controlCommand(path, dispatch.turn_id, savedCommand);
    f.runtime.holo.sync(dispatch.turn_id, "done with this turn", true);

    assert.deepEqual(await controlCommand(path, dispatch.turn_id, savedCommand), saved);
    await assert.rejects(
      () => controlCommand(path, dispatch.turn_id, envelope("AwaitMasterReply")),
      /stale|unauthorized/,
    );
  } finally {
    await f.close();
  }
});
