import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { CapabilityRegistry, type CapabilityContext } from "../src/hub/capability.js";
import { HubRuntime } from "../src/hub/runtime.js";
import { HubStore } from "../src/hub/store.js";
import { verificationRegistry } from "../src/verification/capability.js";
import { fingerprint } from "../src/shared/stable.js";
import type { HubCommandEnvelope } from "../src/shared/types.js";

const envelope = (type: string, payload: Record<string, unknown>, revision?: number): HubCommandEnvelope => ({
  protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(), type,
  target: String(payload.task_id ?? payload.request_id ?? "") || null, payload,
  ...(revision === undefined ? {} : { expected_revision: revision }),
});
async function until(predicate: () => unknown): Promise<void> {
  for (let i = 0; i < 200; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 10)); }
  throw new Error("saved state did not reach the expected boundary");
}
async function setup(registry: CapabilityRegistry) {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-flow-"));
  const runtime = await HubRuntime.start(root, registry);
  const master = (type: string, payload: Record<string, unknown>) => runtime.service.handleMasterCommand(envelope(type, payload,
    typeof payload.task_id === "string" ? runtime.store.getTask(payload.task_id)!.revision : undefined));
  const start = (content = "hold") => {
    const taskId = String(master("CreateTask", { resident_id: "holo" }).task_id);
    master("SendConversationMessage", { task_id: taskId, sender: "master", content });
    return taskId;
  };
  return { root, runtime, master, start, async close() { await runtime.close(); rmSync(root, { recursive: true, force: true }); } };
}

test("live Hub completes question, exact approval and verified child result through one handler, replay and restart", async () => {
  const f = await setup(verificationRegistry());
  try {
    const taskId = f.start("M1 / M2 flow");
    const requests = () => (f.runtime.store.snapshot().pending_requests as Array<{ id: string; kind: string; revision: number }>);
    await until(() => requests()[0]?.kind === "input");
    f.master("SendConversationMessage", { task_id: taskId, sender: "master", content: "ordinary Chat preserves CHECK" });
    assert.equal(requests().length, 1);
    const question = requests()[0]!;
    const answer = envelope("ResolveMasterRequest", { request_id: question.id, answer: { text: "verified" } }, question.revision);
    const receipt = f.runtime.service.handleMasterCommand(answer);
    assert.deepEqual(f.runtime.service.handleMasterCommand(answer), receipt);
    await until(() => requests()[0]?.kind === "approval");
    const approval = requests()[0]!;
    const approve = envelope("ResolveMasterRequest", { request_id: approval.id, answer: { approved: true } }, approval.revision);
    f.runtime.service.handleMasterCommand(approve);
    f.runtime.service.handleMasterCommand(approve);
    await until(() => f.runtime.store.getTask(taskId)?.state === "Completed");
    assert.equal(f.runtime.store.listRuns(taskId).length, 4);
    assert.equal(f.runtime.store.getTask(taskId)?.resume_enabled, false);
    const messages = f.runtime.store.snapshot().messages as Array<{ content: string }>;
    assert.equal(messages.filter(m => m.content.startsWith("検証完了:")).length, 1);
    await f.runtime.close();
    const reopened = await HubRuntime.start(f.root, verificationRegistry());
    try {
      assert.equal(reopened.store.getTask(taskId)?.state, "Completed");
      assert.deepEqual(reopened.service.handleMasterCommand(answer), receipt);
      assert.equal(reopened.store.listRuns(taskId).length, 4);
    } finally { await reopened.close(); }
  } finally { await f.close(); }
});

test("response authority, delivered instruction range, real verification and atomic completion cannot be bypassed", async () => {
  const contexts = new Map<string, CapabilityContext>();
  const registry = new CapabilityRegistry();
  registry.register({ id: "controlled", operations: new Map([
    ["respond", { side_effects: "none" }], ["verify", { side_effects: "none" }],
  ]), availability: () => ({ state: "ready" }),
  invoke: async (_op, _input, context) => { contexts.set(context.run_id, context); return { accepted: true }; },
  cancel: async () => ({ state: "Cancelled", effects: "none", cleanup_state: "clear" }) });
  registry.bindResident("holo", "controlled");
  const f = await setup(registry);
  try {
    const taskId = f.start();
    const response = () => f.runtime.store.listRuns(taskId).find(run => run.kind === "response")!;
    await until(() => response()?.state === "Running");
    const call = (type: string, payload: Record<string, unknown> = {}) => contexts.get(response().id)!.command!(envelope(type, { task_id: taskId, ...payload }));
    const context = call("GetTaskContext");
    assert.throws(() => call("ResolveMasterRequest", { request_id: "fake", answer: { approved: true } }), /unsupported response command/);
    assert.throws(() => call("GetTaskContext", { task_id: f.start("another") }), /another Task/);
    call("RefineTaskDefinition", { title: "Verified", completion_criteria: [{ id: "test", text: "must pass", required: true, verification_kind: "test" }] });
    assert.throws(() => call("RefineTaskDefinition", { title: "Weakened", completion_criteria: [{ id: "test", text: "must pass", required: false, verification_kind: null }] }), /weakened/);
    f.master("SendConversationMessage", { task_id: taskId, sender: "master", content: "new instruction" });
    assert.throws(() => call("FinishResponse", { disposition: "continue", summary: "done", instruction_seq: 2, wake_seq: context.wake_seq }), /not received/);
    const current = call("GetTaskContext");
    const child = String(call("InvokeCapability", { capability_id: "controlled", operation: "verify", input: { version: "a" } }).run_id);
    await until(() => contexts.has(child));
    const complete = { result_summary: "done", instruction_seq: current.instruction_seq, wake_seq: current.wake_seq,
      completion: [{ criterion_id: "test", artifact_ref: "test://result", fingerprint: "a", verification_run_id: child }] };
    assert.throws(() => call("CompleteTask", complete), /unfinished runs/);
    contexts.get(child)!.report!({ state: "Failed", effects: "none", cleanup_state: "clear", verification: { kind: "test", artifact_ref: "test://result", fingerprint: "a", passed: false } });
    assert.throws(() => call("ResolveRunFailure", { run_id: child, resolution: "not_needed", evidence: "ignore" }), /required verification/);
    assert.throws(() => call("ResolveRunFailure", { run_id: child, resolution: "recovered", evidence: "I fixed it" }), /recovery Run/);
    const recovery = String(call("InvokeCapability", { capability_id: "controlled", operation: "verify", input: { version: "b" }, retry_of: child }).run_id);
    await until(() => contexts.has(recovery));
    contexts.get(recovery)!.report!({ state: "Completed", effects: "none", cleanup_state: "clear",
      artifacts: [{ ref: "test://result", fingerprint: "b", ownership: "project" }], verification: { kind: "test", artifact_ref: "test://result", fingerprint: "b", passed: true } });
    call("ResolveRunFailure", { run_id: child, resolution: "recovered", evidence: "passed", recovery_run_id: recovery });
    assert.throws(() => call("CompleteTask", complete), /current content/);
    assert.equal(response().state, "Running");
    const final = envelope("CompleteTask", { task_id: taskId, ...complete,
      completion: [{ criterion_id: "test", artifact_ref: "test://result", fingerprint: "b", verification_run_id: recovery }] });
    const result = contexts.get(response().id)!.command!(final);
    assert.equal(f.runtime.store.getTask(taskId)?.state, "Completed");
    assert.equal(response().state, "Completed");
    assert.deepEqual(contexts.get(response().id)!.command!(final), result);
    assert.throws(() => call("InvokeCapability", { capability_id: "controlled", operation: "verify", input: {} }), /stale response/);
  } finally { await f.close(); }
});

test("stop acknowledgement retains resources; late actual result releases them and old response stays revoked", async () => {
  const contexts = new Map<string, CapabilityContext>();
  const cancelled: string[] = [];
  const registry = new CapabilityRegistry();
  registry.register({ id: "exclusive", operations: new Map([["respond", { side_effects: "possible", resources: ["one-view"] }]]),
    availability: () => ({ state: "ready" }), invoke: async (_op, _input, context) => { contexts.set(context.run_id, context); return { accepted: true }; },
    cancel: async id => { cancelled.push(id); } });
  registry.bindResident("holo", "exclusive");
  const f = await setup(registry);
  try {
    const first = f.start();
    await until(() => contexts.size === 1);
    const run = f.runtime.store.listRuns(first)[0]!;
    const second = f.start();
    f.master("PauseTask", { task_id: first });
    await until(() => cancelled.includes(run.id));
    assert.equal(f.runtime.store.listRuns(second).length, 0);
    f.master("ResumeTask", { task_id: first });
    assert.throws(() => contexts.get(run.id)!.command!(envelope("GetTaskContext", { task_id: first })), /stale response/);
    f.master("CancelTask", { task_id: first });
    contexts.get(run.id)!.report!({ state: "Cancelled", effects: "none", cleanup_state: "clear", result: { stopped: true } });
    await until(() => f.runtime.store.listRuns(second).length === 1);
    assert.equal(f.runtime.store.getTask(first)?.state, "Cancelled");
    await f.runtime.close();
    const reopened = await HubRuntime.start(f.root);
    try {
      assert.equal(reopened.store.getTask(second)?.state, "Paused");
      assert.equal(reopened.store.listRuns(second)[0]?.effects, "unknown");
      assert.equal(reopened.store.resourcesAvailable(["one-view"]), false);
    } finally { await reopened.close(); }
  } finally { await f.close(); }
});

test("settings are versioned and frozen on Runs; corrupt databases never become empty successful stores", async () => {
  const f = await setup(verificationRegistry());
  try {
    const settings = f.runtime.store.getSettings();
    const change = envelope("UpdateSettings", { settings: { command_timeout_ms: 1000 } }, settings.revision);
    f.runtime.service.handleMasterCommand(change);
    assert.throws(() => f.runtime.service.handleMasterCommand(envelope("UpdateSettings", { settings: {} }, settings.revision)), /stale settings/);
    const task = f.start();
    await until(() => f.runtime.store.listRuns(task).length);
    const run = f.runtime.store.listRuns(task)[0]!;
    f.runtime.service.handleMasterCommand(envelope("UpdateSettings", { settings: { command_timeout_ms: 2000 } }, settings.revision + 1));
    assert.equal(JSON.parse(run.settings_json).command_timeout_ms, 1000);
    const badPath = join(f.root, "corrupt.sqlite3");
    writeFileSync(badPath, "this is not sqlite");
    await assert.rejects(() => HubStore.open(badPath), /database/);
  } finally { await f.close(); }
});

test("saved wake intent survives unavailable capabilities; Resume OFF permits children but only ON starts the next response", async () => {
  let ready = false;
  const contexts = new Map<string, CapabilityContext>();
  const registry = new CapabilityRegistry();
  registry.register({ id: "wakes", operations: new Map([["respond", { side_effects: "none" }], ["read", { side_effects: "none" }]]),
    availability: () => ({ state: ready ? "ready" : "blocked", reason: "verification" }),
    invoke: async (_op, _input, context) => { contexts.set(context.run_id, context); return { accepted: true }; },
    cancel: async () => ({ state: "Cancelled", effects: "none", cleanup_state: "clear" }) });
  registry.bindResident("holo", "wakes");
  const f = await setup(registry);
  try {
    const taskId = f.start();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(contexts.size, 0);
    ready = true;
    registry.changed(); registry.changed();
    await until(() => contexts.size === 1);
    const first = f.runtime.store.listRuns(taskId)[0]!;
    const command = (run: string, type: string, payload: Record<string, unknown> = {}) => contexts.get(run)!.command!(envelope(type, { task_id: taskId, ...payload }));
    const a = command(first.id, "GetTaskContext");
    command(first.id, "FinishResponse", { disposition: "continue", summary: "more work", instruction_seq: a.instruction_seq, wake_seq: a.wake_seq });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(contexts.size, 1);
    f.master("SetTaskResume", { task_id: taskId, enabled: true });
    await until(() => contexts.size === 2);
    const second = f.runtime.store.listRuns(taskId).at(-1)!;
    f.master("SetTaskResume", { task_id: taskId, enabled: false });
    const child1 = String(command(second.id, "InvokeCapability", { capability_id: "wakes", operation: "read", input: { file: "a" } }).run_id);
    const child2 = String(command(second.id, "InvokeCapability", { capability_id: "wakes", operation: "read", input: { file: "b" } }).run_id);
    const b = command(second.id, "GetTaskContext");
    assert.throws(() => command(second.id, "FinishResponse", { disposition: "wait", summary: "bad", instruction_seq: b.instruction_seq, wake_seq: b.wake_seq, wait_for: ["missing"] }), /wait target/);
    command(second.id, "FinishResponse", { disposition: "wait", summary: "waiting for accepted children", instruction_seq: b.instruction_seq, wake_seq: b.wake_seq, wait_for: [child1, child2] });
    await until(() => contexts.has(child1) && contexts.has(child2));
    contexts.get(child1)!.report!({ state: "Completed", effects: "none", cleanup_state: "clear" });
    contexts.get(child2)!.report!({ state: "Completed", effects: "none", cleanup_state: "clear" });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.runtime.store.listRuns(taskId).filter(run => run.kind === "response").length, 2);
    assert.equal(f.runtime.store.getTask(taskId)?.wake_seq, 0);
    f.master("SetTaskResume", { task_id: taskId, enabled: true });
    await until(() => f.runtime.store.listRuns(taskId).filter(run => run.kind === "response").length === 3);
  } finally { await f.close(); }
});
