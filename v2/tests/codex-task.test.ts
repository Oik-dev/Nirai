import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { CapabilityRegistry } from "../src/hub/capability.js";
import type { CodexConversationProvider } from "../src/hub/codex.js";
import { CodexTaskDriver } from "../src/hub/codex-task.js";
import { TaskEngine } from "../src/hub/engine.js";
import { HubService } from "../src/hub/service.js";
import { HubStore } from "../src/hub/store.js";

type RunOptions = Parameters<CodexConversationProvider["run"]>[0];
interface Request {
  options: RunOptions;
  signal: AbortSignal;
  resolve(content: string): void;
}

const tick = () => new Promise<void>(resolve => setImmediate(resolve));

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-codex-task-"));
  const store = new HubStore(join(root, "hub.sqlite3"));
  store.createResident("resident-a", { display_name: "Resident A", capability_id: "codex", model: "fixture-model" });
  const registry = new CapabilityRegistry();
  const engine = new TaskEngine(store, registry);
  const service = new HubService(store, engine);
  const requests: Request[] = [];
  const driver = new CodexTaskDriver(store, service, {
    availability: () => ({ state: "ready" }),
    run(options: RunOptions, signal: AbortSignal) {
      return new Promise<string>(resolve => requests.push({ options, signal, resolve }));
    },
  });
  engine.registerTaskDriver("codex", resident => resident.capability_id === "codex", driver);
  driver.onChanged = () => { engine.schedule(); engine.onChanged(); };
  return {
    store, registry, engine, service, requests, driver,
    async start(content = "依頼の原文") {
      const task = store.createTask("resident-a");
      store.addMasterMessage(task.id, content);
      engine.start();
      await tick();
      assert.equal(requests.length, 1);
      return { task, request: requests[0]! };
    },
    async close() {
      const closing = engine.close();
      for (const request of requests) request.resolve("終了後の遅い返答");
      await closing;
      store.close();
      rmSync(root, { recursive: true, force: true });
    },
  };
}

async function call(request: Request, type: string, payload: Record<string, unknown> = {}, turnId?: string) {
  const { onToolCall } = request.options;
  assert.ok(onToolCall);
  const prompt = JSON.parse(request.options.prompt) as { turn_id: string };
  return onToolCall("nirai_command", {
    turn_id: turnId ?? prompt.turn_id,
    envelope: { command_id: randomUUID(), type, payload },
  }, request.signal);
}

async function finish(request: Request, content: string): Promise<void> {
  request.resolve(content);
  await tick();
  await tick();
}

test("Codex Task final text is saved unchanged and completion needs the same Turn's final reply", async () => {
  const f = fixture();
  try {
    const { task, request } = await f.start("この原文で依頼します。");
    assert.equal(request.options.model, "fixture-model");
    const prompt = JSON.parse(request.options.prompt) as { turn_id: string; current_input: string };
    assert.equal(prompt.current_input, "この原文で依頼します。");
    assert.equal(request.options.dynamicTools?.[0]?.name, "nirai_command");
    assert.equal(request.options.dynamicTools?.[0]?.deferLoading, false);
    const foreign = await call(request, "CompleteTask", { result_summary: "別Turnの操作" }, randomUUID());
    assert.equal(foreign.success, false);

    const completed = await call(request, "CompleteTask", { result_summary: "作業完了" });
    assert.equal(completed.success, true);
    assert.equal(f.store.getTask(task.id)?.state, "Running");
    assert.equal(f.store.getHoloTurn(prompt.turn_id)?.completion_summary, "作業完了");
    assert.equal((await call(request, "AwaitMasterReply")).success, false, "completion staging closes new Tool authority");
    const content = "回答の原文です。  \n\n[成果物](https://example.test/result)\n最後の行。";
    await finish(request, content);
    assert.equal(f.store.getTask(task.id)?.state, "Completed");
    const saved = (f.store.snapshot().messages as Array<{ turn_id: string | null; content: string }>)
      .filter(message => message.turn_id === prompt.turn_id);
    assert.equal(saved.length, 1);
    assert.equal(saved[0]?.content, content);
    assert.equal(f.requests.length, 1);
  } finally { await f.close(); }
});

test("Codex Master handoff preserves the question and starts one Turn for a new Master answer", async () => {
  const f = fixture();
  try {
    const { task, request } = await f.start();
    assert.equal((await call(request, "AwaitMasterReply")).success, true);
    await finish(request, "好きな色は？");
    assert.equal(f.store.getTask(task.id)?.state, "Running");
    assert.equal(f.requests.length, 1, "ordinary Codex does not automatically continue after its question");
    f.store.addMasterMessage(task.id, "青");
    f.engine.schedule();
    await tick();
    assert.equal(f.requests.length, 2);
    const next = f.requests[1]!;
    const prompt = JSON.parse(next.options.prompt) as {
      current_input: string; history: Array<{ sender: string; content: string }>;
    };
    assert.equal(prompt.current_input, "青");
    assert.ok(prompt.history.some(message => message.sender === "resident-a" && message.content === "好きな色は？"));
    assert.equal((await call(next, "CompleteTask", { result_summary: "回答を確認" })).success, true);
    await finish(next, "青ですね。確認完了です。");
    assert.equal(f.store.getTask(task.id)?.state, "Completed");
  } finally { await f.close(); }
});

test("Pause and Cancel abort the original Codex request, reject old Tools and ignore late text after Resident changes", async () => {
  const f = fixture();
  try {
    const { task, request } = await f.start();
    const turnId = (JSON.parse(request.options.prompt) as { turn_id: string }).turn_id;
    f.store.updateResident("resident-a", { capability_id: "other-provider", model: "other-model" });
    assert.equal(request.options.model, "fixture-model", "already started generation keeps its selected settings");
    f.store.pauseTask(task.id);
    f.engine.schedule();
    await tick();
    assert.equal(request.signal.aborted, true);
    assert.equal((await call(request, "CompleteTask", { result_summary: "停止後の操作" })).success, false);
    await finish(request, "保存してはいけない遅い回答");
    assert.equal(f.store.getTask(task.id)?.state, "Paused");
    assert.equal((f.store.snapshot().messages as Array<{ turn_id: string | null }>).some(message => message.turn_id === turnId), false);

    f.store.updateResident("resident-a", { capability_id: "codex" });
    f.store.resumeTask(task.id);
    f.engine.schedule();
    await tick();
    const next = f.requests[1]!;
    assert.ok(next);
    f.store.cancelTask(task.id);
    f.engine.schedule();
    await tick();
    assert.equal(next.signal.aborted, true);
    assert.equal((await call(next, "AwaitMasterReply")).success, false);
    await finish(next, "取消後の遅い回答");
    assert.equal(f.store.getTask(task.id)?.state, "Cancelled");
    assert.equal((f.store.snapshot().messages as Array<{ sender: string }>).filter(message => message.sender === "resident-a").length, 0);
    assert.equal(f.requests.length, 2);
  } finally { await f.close(); }
});

test("Codex Approval resumes with the same completed Action reference and never reinvokes it", async () => {
  const f = fixture();
  try {
    let invoked = 0;
    let finishAction: (() => void) | undefined;
    f.registry.register({
      id: "approved-fixture", availability: () => ({ state: "ready" }),
      operations: new Map([["work", { side_effects: "none", approval: "Masterの確認が必要です。" }]]),
      async invoke() {
        invoked++;
        await new Promise<void>(resolve => { finishAction = resolve; });
        return { state: "Completed", effects: "none", cleanup_state: "clear", result: { summary: "一度だけ実行した結果" } };
      },
    });
    const { task, request } = await f.start();
    const action = await call(request, "InvokeCapability", { capability_id: "approved-fixture", operation: "work", input: {} });
    assert.equal(action.success, true);
    await tick();
    const run = f.store.listRuns(task.id)[0]!;
    assert.equal(run.state, "Pending");
    assert.equal(invoked, 0);
    assert.equal((f.store.snapshot().pending_requests as unknown[]).length, 1);
    assert.equal((await call(request, "CompleteTask", { result_summary: "承認を迂回して完了" })).success, false);
    await finish(request, "操作を進めるには承認が必要です。");
    assert.equal(f.store.getTask(task.id)?.state, "Running");
    assert.equal(f.store.getRun(run.id)?.state, "Pending");
    assert.equal(invoked, 0);
    assert.equal(f.requests.length, 1);

    const approval = (f.store.snapshot().pending_requests as Array<{ id: string; revision: number }>)[0]!;
    f.service.handleMasterCommand({
      protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(),
      target: approval.id, expected_revision: approval.revision, type: "ResolveMasterRequest",
      payload: { request_id: approval.id, answer: { approved: true } },
    });
    await tick();
    assert.equal(f.store.getRun(run.id)?.state, "Running");
    assert.equal(invoked, 1);
    assert.equal(f.requests.length, 1, "the new Turn waits until the approved Action has finished");
    assert.ok(finishAction);
    finishAction();
    for (let attempt = 0; attempt < 10 && f.requests.length < 2; attempt++) await tick();
    assert.equal(f.requests.length, 2, "Action completion starts exactly one continuation for the approval control input");
    const next = f.requests[1]!;
    const prompt = JSON.parse(next.options.prompt) as {
      current_input: string; history: Array<{ content: string }>;
      action_runs: Array<{ id: string; state: string; capability_id: string; operation: string; has_result: boolean }>;
    };
    assert.equal(prompt.current_input, "Master approved the requested operation.");
    assert.ok(prompt.history.every(message => !message.content.includes(run.id)), "assistant text did not preserve the Run id");
    assert.deepEqual(prompt.action_runs.map(reference => ({
      id: reference.id, state: reference.state, capability_id: reference.capability_id,
      operation: reference.operation, has_result: reference.has_result,
    })), [{ id: run.id, state: "Completed", capability_id: "approved-fixture", operation: "work", has_result: true }]);
    const result = await call(next, "GetRunResult", { run_id: prompt.action_runs[0]!.id });
    assert.equal(result.success, true);
    const detail = JSON.parse(result.contentItems[0]!.text) as {
      run: { id: string; state: string }; result: { value: { value: { summary: string } } };
    };
    assert.equal(detail.run.id, run.id);
    assert.equal(detail.run.state, "Completed");
    assert.equal(detail.result.value.value.summary, "一度だけ実行した結果");
    assert.equal((await call(next, "CompleteTask", { result_summary: "承認された操作の結果を確認" })).success, true);
    assert.equal(f.store.getTask(task.id)?.state, "Running");
    await finish(next, "承認された操作を一度だけ実行し、結果を確認しました。");
    assert.equal(f.store.getTask(task.id)?.state, "Completed");
    assert.equal(f.store.listRuns(task.id).length, 1);
    assert.equal(invoked, 1);
    assert.equal(f.requests.length, 2);
  } finally { await f.close(); }
});
