import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { CapabilityRegistry } from "../src/hub/capability.js";
import { TaskEngine } from "../src/hub/engine.js";
import { HubService } from "../src/hub/service.js";
import { HubStore } from "../src/hub/store.js";
import type { HoloTurnRecord, HubCommandEnvelope } from "../src/shared/types.js";

const tick = () => new Promise<void>(resolve => setImmediate(resolve));
const command = (type: string, payload: Record<string, unknown> = {}): HubCommandEnvelope => ({
  protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(),
  target: null, type, payload,
});

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-task-driver-"));
  const store = new HubStore(join(root, "hub.sqlite3"));
  store.ensureResident("holo", "Holo");
  store.createResident("resident-a", { display_name: "Resident A", capability_id: "fixture-ai" });
  const registry = new CapabilityRegistry();
  const engine = new TaskEngine(store, registry);
  const service = new HubService(store, engine);
  let closed = false;
  return {
    store, engine, service,
    async close() {
      if (!closed) { await engine.close(); closed = true; }
      store.close();
      rmSync(root, { recursive: true, force: true });
    },
  };
}

test("a registered ordinary Task driver handles each instruction once and cannot enable Holo Resume", async () => {
  const f = fixture();
  try {
    const starts: HoloTurnRecord[] = [];
    let replacedStarts = 0;
    let ready = false;
    f.engine.registerTaskDriver("fixture-ai", resident => resident.capability_id === "fixture-ai", {
      availability: () => ({ state: "ready" }), start: () => { replacedStarts++; },
    });
    f.engine.registerTaskDriver("fixture-ai", resident => resident.capability_id === "fixture-ai", {
      availability: () => ({ state: ready ? "ready" : "unavailable" }),
      start: turn => starts.push(turn),
    });
    const task = f.store.createTask("resident-a");
    f.store.addMasterMessage(task.id, "最初の依頼");
    f.engine.start();
    await tick();
    assert.equal(f.store.listHoloTurns(task.id).length, 0, "unavailable drivers receive no Turn authority");
    ready = true;
    f.engine.schedule();
    await tick();
    assert.equal(replacedStarts, 0);
    assert.equal(starts.length, 1);
    assert.equal(f.store.getHoloInput(starts[0]!.id), "最初の依頼");
    assert.equal(f.store.reserveHoloTurn(task.id), null, "Holo-only callers cannot dispatch ordinary Resident Tasks");
    assert.throws(() => f.store.setTaskResume(task.id, true), /only for Holo/);

    f.store.syncHoloTurn(starts[0]!.id, "最初の返答", true);
    f.engine.schedule();
    await tick();
    assert.equal(starts.length, 1, "ordinary drivers wait after the final reply");
    f.store.addMasterMessage(task.id, "次の依頼");
    f.engine.schedule();
    await tick();
    assert.equal(starts.length, 2);
    assert.equal(f.store.getHoloInput(starts[1]!.id), "次の依頼");
  } finally { await f.close(); }
});

test("Task control reconciles the original driver after Resident changes and awaits its shutdown", async () => {
  const f = fixture();
  try {
    const active = new Set<string>();
    const stopped: string[] = [];
    const starts: HoloTurnRecord[] = [];
    let closeFinished = false;
    f.engine.registerTaskDriver("fixture-ai", resident => resident.capability_id === "fixture-ai", {
      availability: () => ({ state: "ready" }),
      start(turn) { active.add(turn.id); starts.push(turn); },
      reconcile() {
        for (const id of active) {
          if (f.store.getHoloTurn(id)?.ended_at === null) continue;
          active.delete(id); stopped.push(id);
        }
      },
      async close() {
        assert.equal(active.size, 0, "shutdown invalidates saved authority before closing the provider");
        await tick(); closeFinished = true;
      },
    });
    const task = f.store.createTask("resident-a");
    f.store.addMasterMessage(task.id, "作業");
    f.engine.start();
    await tick();
    const first = starts[0]!;
    f.store.updateResident("resident-a", { capability_id: "other-ai", model: "changed" });
    f.store.pauseTask(task.id);
    f.engine.onChanged = () => assert.ok(stopped.includes(first.id), "provider cancellation precedes state notification");
    f.engine.schedule();
    await tick();
    assert.deepEqual(stopped, [first.id]);
    assert.throws(() => f.service.handleTurnCommand(first.id, command("AwaitMasterReply")), /stale/);

    f.store.updateResident("resident-a", { capability_id: "fixture-ai" });
    f.store.resumeTask(task.id);
    f.engine.schedule();
    await tick();
    assert.equal(starts.length, 2);
    f.store.cancelTask(task.id);
    f.engine.schedule();
    await tick();
    assert.equal(active.size, 0);
    assert.equal(f.store.getTask(task.id)?.state, "Cancelled");
    await f.engine.close();
    assert.equal(closeFinished, true);
  } finally { await f.close(); }
});

test("ordinary Task Turns retain Master handoff, Action safety, final-reply completion and restart boundaries", async () => {
  const f = fixture();
  try {
    const task = f.store.createTask("resident-a");
    f.store.addMasterMessage(task.id, "確認して");
    const first = f.store.reserveTaskTurn(task.id, false)!;
    f.service.handleTurnCommand(first.id, command("AwaitMasterReply"));
    f.store.syncHoloTurn(first.id, "Aで進めますか？", true);
    assert.equal(f.store.reserveTaskTurn(task.id, false), null);
    f.store.addMasterMessage(task.id, "Aで");
    const second = f.store.reserveTaskTurn(task.id, false)!;
    assert.equal(f.store.getHoloInput(second.id), "Aで");

    const run = f.store.createRun({
      task_id: task.id, turn_id: second.id, control_epoch: second.control_epoch,
      capability_id: "fixture-action", operation: "read", input: {},
    }, "none");
    assert.throws(() => f.service.handleTurnCommand(second.id,
      command("CompleteTask", { result_summary: "確認完了" })), /unfinished runs/);
    f.store.markRunRunning(run.id);
    f.store.recordRunResult(run.id, { state: "Completed", effects: "none", cleanup_state: "clear" });
    const completing = command("CompleteTask", { result_summary: "確認完了" });
    const receipt = f.service.handleTurnCommand(second.id, completing);
    assert.equal(f.store.getTask(task.id)?.state, "Running");
    f.store.syncHoloTurn(second.id, "確認が完了しました。", true);
    assert.equal(f.store.getTask(task.id)?.state, "Completed");
    assert.deepEqual(f.service.handleTurnCommand(second.id, completing), receipt);
    assert.throws(() => f.service.handleTurnCommand(second.id, command("AwaitMasterReply")), /stale/);
    assert.equal(f.store.reserveTaskTurn(task.id, false), null);

    const interruptedTask = f.store.createTask("resident-a");
    f.store.addMasterMessage(interruptedTask.id, "中断対象");
    const interruptedTurn = f.store.reserveTaskTurn(interruptedTask.id, false)!;
    f.service.handleTurnCommand(interruptedTurn.id, command("CompleteTask", { result_summary: "未保存" }));
    f.store.recoverAfterRestart();
    assert.equal(f.store.getTask(interruptedTask.id)?.state, "Paused");
    assert.equal(f.store.getHoloTurn(interruptedTurn.id)?.completion_summary, null);
    assert.equal(f.store.reserveTaskTurn(interruptedTask.id, false), null);
    assert.throws(() => f.service.handleTurnCommand(interruptedTurn.id, command("AwaitMasterReply")), /stale/);
  } finally { await f.close(); }
});

test("Task driver context is bounded to its Task and excludes inputs newer than the active Turn", async () => {
  const f = fixture();
  try {
    const otherTask = f.store.createTask("resident-a");
    f.store.addMasterMessage(otherTask.id, "別Taskの秘密");
    const task = f.store.createTask("resident-a");
    f.store.addMasterMessage(task.id, "古い入力".repeat(500));
    const first = f.store.reserveTaskTurn(task.id, false)!;
    f.store.syncHoloTurn(first.id, "過去の返答", true);
    f.store.addMasterMessage(task.id, "現在の入力");
    const turn = f.store.reserveTaskTurn(task.id, false)!;
    f.store.addMasterMessage(task.id, "まだ処理してはいけない入力");
    const messages = f.store.getTaskTurnMessages(turn.id, 40, 1024);
    assert.deepEqual(messages, [
      { sender: "resident-a", content: "過去の返答" },
      { sender: "master", content: "現在の入力" },
    ]);
    assert.ok(Buffer.byteLength(JSON.stringify(messages)) <= 1024);
    assert.deepEqual(f.store.getTaskTurnMessages(turn.id, 1), [{ sender: "master", content: "現在の入力" }]);
    f.store.pauseTask(task.id);
    assert.throws(() => f.store.getTaskTurnMessages(turn.id), /stale/);
  } finally { await f.close(); }
});
