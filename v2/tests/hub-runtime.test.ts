import assert from "node:assert/strict";
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { HubRuntime } from "../src/hub/runtime.js";
import { HubStore } from "../src/hub/store.js";
import { ConversationProviders } from "../src/hub/conversation.js";
import { CodexTaskDriver } from "../src/hub/codex-task.js";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(complete => { resolve = complete; });
  return { promise, resolve };
}

async function waitFor(condition: () => boolean, label: string): Promise<void> {
  const deadline = Date.now() + 5000;
  while (!condition() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
  assert.ok(condition(), label);
}

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

test("connection refresh cannot interrupt normal conversation Persona preparation or generation", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-chat-refresh-"));
  const personaPath = join(root, "persona.md");
  writeFileSync(personaPath, "本人の設定を保って返答してください。", "utf8");
  const reply = deferred<string>();
  let generations = 0;
  let refreshes = 0;
  const providers = new ConversationProviders();
  providers.register({
    id: "fixture-ai", availability: () => ({ state: "ready" }),
    async refresh() { refreshes++; },
    async generate(input) {
      generations++;
      assert.equal(input.persona?.text, "本人の設定を保って返答してください。");
      return reply.promise;
    },
  });
  const runtime = await HubRuntime.start(root, undefined, providers);
  try {
    runtime.store.createResident("resident-a", { display_name: "Resident A", capability_id: "fixture-ai", persona_path: personaPath });
    const input = runtime.store.addChatMasterMessage("whisper", "resident-a", "質問の原文");
    runtime.conversation.schedule();
    assert.equal(generations, 0, "connection is reserved before asynchronous Persona reading finishes");
    await assert.rejects(runtime.refreshConversationProvider("fixture-ai"), /準備・生成/);
    assert.equal(refreshes, 0);
    await waitFor(() => generations === 1, "normal conversation reaches generation");
    await assert.rejects(runtime.refreshConversationProvider("fixture-ai"), /準備・生成/);
    assert.equal(refreshes, 0, "the confirmation does not reach the active provider");
    reply.resolve("返答の原文");
    await runtime.conversation.idle();
    assert.equal(runtime.store.getChatResponse(input.message_id, "resident-a")?.state, "completed");
    await runtime.refreshConversationProvider("fixture-ai");
    assert.equal(refreshes, 1, "confirmation succeeds after the reply is saved");
    assert.equal(runtime.store.listTasks().length, 0);
    assert.equal(runtime.store.listRuns().length, 0);
  } finally {
    reply.resolve("終了時の返答");
    await runtime.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("connection refresh protects Task preparation and generation, then reevaluates a waiting Task", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-task-refresh-"));
  const personaPath = join(root, "persona.md");
  writeFileSync(personaPath, "このPersonaを使って作業してください。", "utf8");
  const preparationStarted = deferred<void>();
  let reply = deferred<string>();
  let generations = 0;
  let refreshes = 0;
  let ready = true;
  const availability = () => ({ state: ready ? "ready" as const : "unavailable" as const });
  const providers = new ConversationProviders();
  providers.register({
    id: "fixture-ai", availability,
    async refresh() { refreshes++; ready = true; },
    async generate() { throw new Error("this test never requests an ordinary reply"); },
  });
  const runtime = await HubRuntime.start(root, undefined, providers);
  const driver = new CodexTaskDriver(runtime.store, runtime.service, {
    availability,
    async run(options) {
      generations++;
      assert.match(options.developerInstructions, /このPersonaを使って作業してください。/);
      return reply.promise;
    },
  });
  driver.onChanged = () => runtime.engine.schedule();
  runtime.engine.registerTaskDriver("fixture-ai", resident => resident.capability_id === "fixture-ai", {
    availability: () => driver.availability(),
    start(turn) { driver.start(turn); preparationStarted.resolve(undefined); },
    reconcile: () => driver.reconcile(), close: () => driver.close(),
  });
  try {
    runtime.store.createResident("resident-a", { display_name: "Resident A", capability_id: "fixture-ai", persona_path: personaPath });
    const firstTask = runtime.store.createTask("resident-a");
    runtime.store.addMasterMessage(firstTask.id, "最初の指示");
    runtime.engine.schedule();
    await preparationStarted.promise;
    assert.equal(generations, 0, "Task driver owns the connection before Persona preparation finishes");
    await assert.rejects(runtime.refreshConversationProvider("fixture-ai"), /準備・生成/);
    await waitFor(() => generations === 1, "Task reaches generation");
    await assert.rejects(runtime.refreshConversationProvider("fixture-ai"), /準備・生成/);
    assert.equal(refreshes, 0);
    reply.resolve("最初の返答");
    await waitFor(() => driver.availability().state !== "busy", "Task reply and driver finish");
    assert.equal(runtime.store.listHoloTurns(firstTask.id)[0]?.end_reason, "assistant");

    ready = false;
    reply = deferred<string>();
    const waitingTask = runtime.store.createTask("resident-a");
    runtime.store.addMasterMessage(waitingTask.id, "接続待ちの指示");
    runtime.engine.schedule();
    await new Promise<void>(resolve => setImmediate(resolve));
    assert.equal(runtime.store.listHoloTurns(waitingTask.id).length, 0, "unavailable connection receives no Turn authority");
    await runtime.refreshConversationProvider("fixture-ai");
    assert.equal(refreshes, 1);
    await waitFor(() => generations === 2, "successful refresh schedules the waiting Task");
    assert.equal(runtime.store.listHoloTurns(waitingTask.id).length, 1);
    reply.resolve("接続後の返答");
    await waitFor(() => driver.availability().state !== "busy", "waiting Task reply finishes");
    assert.equal(generations, 2, "ordinary Task is not automatically generated again");
  } finally {
    reply.resolve("終了時の返答");
    await runtime.close();
    rmSync(root, { recursive: true, force: true });
  }
});
