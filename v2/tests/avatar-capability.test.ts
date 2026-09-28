import assert from "node:assert/strict";
import test from "node:test";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { AvatarCapability } from "../src/hub/avatar.js";
import { CapabilityRegistry } from "../src/hub/capability.js";
import { TaskEngine } from "../src/hub/engine.js";
import { HubService } from "../src/hub/service.js";
import { HubStore } from "../src/hub/store.js";
import { defaultAppearance, validateAppearance, validateCatalog } from "../src/shared/appearance.js";
import type { AppearanceCatalog, AvatarRuntimeReport } from "../src/shared/appearance.js";

const catalog: AppearanceCatalog = {
  expressions: [{ id: "happy", is_binary: false }, { id: "surprised", is_binary: true }],
  wardrobe: [
    { id: "coat", category: "top", tags: ["coat"], default_visible: true, removable: true },
    { id: "base", category: "body", tags: [], default_visible: true, removable: false },
  ],
};
const hash = "a".repeat(64);
const choice = { expression: { id: "happy", weight: .6 }, wardrobe: { coat: false, base: true } };

function setup() {
  const root = mkdtempSync(join(tmpdir(), "nirai-avatar-"));
  const path = join(root, "hub.sqlite3");
  const store = new HubStore(path);
  store.ensureResident("holo", "Holo");
  store.ensureResident("other", "Other");
  const modelPath = join(root, "avatar.vrm");
  store.updateSettings({ resident_avatars: { holo: modelPath, other: modelPath } }, store.getSettings().revision);
  const task = store.createTask("holo");
  store.addMasterMessage(task.id, "Choose your appearance.");
  const turn = store.reserveHoloTurn(task.id)!;
  const registry = new CapabilityRegistry();
  let changes = 0;
  const avatar = new AvatarCapability(store, () => changes++);
  registry.register(avatar);
  const engine = new TaskEngine(store, registry);
  const service = new HubService(store, engine);
  const report: AvatarRuntimeReport = {
    resident_id: "holo", model_path: modelPath, model_id: hash, token: "renderer-load-1",
    capabilities: catalog, applied_revision: null, status: "ready",
  };
  avatar.report(report);
  const command = (operation: string, input: unknown, commandId = randomUUID()) => ({
    protocol_version: 1, command_id: commandId, issued_at: new Date().toISOString(), target: null,
    type: "InvokeCapability", payload: { capability_id: "avatar", operation, input },
  });
  const invoke = (operation: string, input: unknown) => {
    const result = service.handleTurnCommand(turn.id, command(operation, input));
    return String(result.run_id);
  };
  const select = (expected_revision: string | null = null) => ({ model_id: hash, expected_revision, appearance: choice });
  return { root, path, store, task, turn, avatar, engine, service, report, command, invoke, select,
    changes: () => changes, close: () => { store.close(); rmSync(root, { recursive: true, force: true }); } };
}

test("Avatar semantics reject unsupported choices and expose no arbitrary model controls", () => {
  assert.deepEqual(validateCatalog(catalog), catalog);
  assert.deepEqual(defaultAppearance(catalog), { expression: null, wardrobe: { coat: true, base: true } });
  assert.deepEqual(validateAppearance(choice, catalog), choice);
  for (const input of [
    { ...choice, expression: { id: "missing", weight: 1 } },
    { ...choice, expression: { id: "happy", weight: 1.1 } },
    { ...choice, expression: { id: "happy", weight: NaN } },
    { ...choice, expression: { id: "surprised", weight: .5 } },
    { ...choice, wardrobe: { coat: false } },
    { ...choice, wardrobe: { coat: false, base: false } },
    { ...choice, wardrobe: { coat: false, base: true, arbitrary_node: false } },
  ]) assert.throws(() => validateAppearance(input, catalog));
  assert.throws(() => validateCatalog({ ...catalog, expressions: [...catalog.expressions, catalog.expressions[0]] }), /duplicate/);
});

test("self selection is saved durably before display is confirmed and belongs only to its Task Resident", async () => {
  const f = setup();
  try {
    assert.throws(() => f.invoke("set", { ...f.select(), resident_id: "other" }), /invalid/);
    assert.throws(() => f.service.handleMasterCommand(f.command("set", f.select())), /unsupported Master/);
    const id = f.invoke("set", f.select());
    assert.equal(f.avatar.states()[0]?.desired?.revision, null);
    await f.engine.dispatchRun(id);
    const run = f.store.getRun(id)!;
    assert.equal(run.state, "Completed");
    assert.equal(run.side_effects, "none");
    const saved = JSON.parse(run.result_json!).value;
    assert.equal(saved.desired_saved, true);
    assert.equal(saved.display_applied, false);
    assert.equal(saved.resident_id, "holo");
    const state = f.avatar.states().find(item => item.resident_id === "holo")!;
    assert.deepEqual(state.desired, { revision: id, appearance: choice });
    assert.equal(state.display_applied, false);
    assert.equal(f.avatar.states().find(item => item.resident_id === "other")?.desired, null);
    f.avatar.report({ ...f.report, applied_revision: id });
    assert.equal(f.avatar.states()[0]?.display_applied, true);
    const inspect = f.invoke("inspect", {});
    await f.engine.dispatchRun(inspect);
    assert.equal(JSON.parse(f.store.getRun(inspect)!.result_json!).value.display_applied, true);
  } finally { f.close(); }
});

test("concurrent stale selections serialize and model changes reject old choices", async () => {
  const f = setup();
  try {
    const first = f.invoke("set", f.select());
    const second = f.invoke("set", f.select());
    await Promise.all([f.engine.dispatchRun(first), f.engine.dispatchRun(second)]);
    assert.equal(f.store.getRun(first)?.state, "Completed");
    assert.equal(f.store.getRun(second)?.state, "Pending");
    await assert.rejects(f.engine.dispatchRun(second), /stale Avatar/);
    assert.equal(f.store.getRun(second)?.state, "Failed");
    assert.equal(f.avatar.states()[0]?.desired?.revision, first);
    f.avatar.report({ ...f.report, model_id: "b".repeat(64), token: "renderer-load-2" });
    const oldModel = f.invoke("set", f.select(first));
    await assert.rejects(f.engine.dispatchRun(oldModel), /model changed/);
    assert.equal(f.avatar.states()[0]?.desired?.revision, null);
    assert.throws(() => f.avatar.report({ ...f.report, model_id: "b".repeat(64), applied_revision: first }), /unknown Avatar/);
  } finally { f.close(); }
});

test("Pause closes Turn authority and late Run results cannot become an Appearance choice", async () => {
  const f = setup();
  try {
    const id = f.invoke("set", f.select());
    const dispatch = f.engine.dispatchRun(id);
    f.store.pauseTask(f.task.id);
    await dispatch;
    const run = f.store.getRun(id)!;
    assert.equal(run.state, "Interrupted");
    assert.equal(run.effects, "none");
    assert.equal(run.cleanup_state, "clear");
    assert.ok(run.supplemental_result_json);
    assert.equal(f.avatar.states()[0]?.desired?.revision, null);
    assert.throws(() => f.invoke("set", f.select()), /active|expired|stale|running/i);
    assert.throws(() => f.avatar.report({ ...f.report, applied_revision: id }), /unknown Avatar/);
  } finally { f.close(); }
});

test("restart keeps completed selections but resets display evidence and interrupted work", async () => {
  const f = setup();
  let reopened: HubStore | null = null;
  try {
    const saved = f.invoke("set", f.select());
    await f.engine.dispatchRun(saved);
    const unfinished = f.invoke("set", f.select(saved));
    f.store.markRunRunning(unfinished);
    f.store.close();
    reopened = new HubStore(f.path);
    reopened.recoverAfterRestart();
    const avatar = new AvatarCapability(reopened);
    assert.equal(avatar.states()[0]?.status, "unavailable");
    avatar.report(f.report);
    assert.deepEqual(avatar.states()[0]?.desired, { revision: saved, appearance: choice });
    assert.equal(avatar.states()[0]?.display_applied, false);
    assert.equal(reopened.getRun(unfinished)?.state, "Interrupted");
    assert.equal(reopened.getRun(unfinished)?.cleanup_state, "clear");
    avatar.report({ ...f.report, applied_revision: saved });
    assert.equal(avatar.states()[0]?.display_applied, true);
  } finally {
    if (reopened) reopened.close(); else f.store.close();
    rmSync(f.root, { recursive: true, force: true });
  }
});

test("runtime reports are bounded, idempotent and cannot break snapshots on incompatible capabilities", async () => {
  const f = setup();
  try {
    const changes = f.changes();
    assert.equal(f.avatar.report(f.report), false);
    assert.equal(f.changes(), changes);
    assert.throws(() => f.avatar.report({ ...f.report, model_path: join(f.root, "other.vrm") }), /invalid/);
    assert.throws(() => f.avatar.report({ ...f.report, resident_id: "stranger" }), /invalid/);
    const id = f.invoke("set", f.select());
    await f.engine.dispatchRun(id);
    f.avatar.report({ ...f.report, capabilities: { ...catalog, expressions: [] } });
    assert.equal(f.avatar.states()[0]?.status, "unavailable");
    assert.equal(f.avatar.states()[0]?.display_applied, false);
    assert.equal(f.avatar.states().length, 2);
    f.avatar.report({ ...f.report, applied_revision: id });
    assert.equal(f.avatar.states()[0]?.display_applied, true);
    f.avatar.report({ ...f.report, status: "unavailable", error: "WebGL context lost" });
    assert.equal(f.avatar.states()[0]?.display_applied, false);
    assert.equal(f.avatar.reset("holo"), true);
    assert.equal(f.avatar.reset("holo"), false);
  } finally { f.close(); }
});
