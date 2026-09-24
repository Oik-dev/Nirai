import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdtemp, mkdir, readFile, writeFile, rm, stat } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawn } from "node:child_process";
import test from "node:test";
import { localFiles, LocalFilePolicy } from "../src/hub/local-files.js";
import { HubRuntime } from "../src/hub/runtime.js";
import { CapabilityRegistry } from "../src/hub/capability.js";
import { controlCommand } from "../src/bridge/client.js";
import type { HubCommandEnvelope, RunRecord } from "../src/shared/types.js";
import { probeLocalWorker, recoverLocalRuns } from "../src/hub/local-recovery.js";
import { HubStore } from "../src/hub/store.js";

const hash = (text: string) => createHash("sha256").update(text).digest("hex");
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
async function until<T>(probe: () => Promise<T> | T): Promise<NonNullable<T>> {
  for (let i = 0; i < 1000; i++) { const value = await probe(); if (value) return value as NonNullable<T>; await delay(10); }
  throw new Error("local execution did not reach the expected boundary");
}
const envelope = (type: string, payload: Record<string, unknown> = {}): HubCommandEnvelope => ({ protocol_version: 1,
  command_id: randomUUID(), issued_at: new Date().toISOString(), target: null, type, payload });

test("real authenticated file -> patch -> fixed approved command -> saved verification; stale Turn cannot write", async () => {
  const root = await mkdtemp(join(tmpdir(), "nirai-v2-execution-")), workspace = join(root, "project"), data = join(root, "hub");
  await mkdir(workspace); await writeFile(join(workspace, "value.txt"), "before");
  await writeFile(join(workspace, "verify.cjs"), 'const fs=require("node:fs");if(fs.readFileSync("value.txt","utf8")!=="after")process.exit(1);console.log("verified");');
  const registry = new CapabilityRegistry();
  registry.register({ ...localFiles(new LocalFilePolicy([data]), data, [{ id: "fixture.test", workspace, executable: process.execPath, argv: ["verify.cjs"], sources: ["verify.cjs", "value.txt"], output_dirs: [], description: "isolated fixture verification", verification_kind: "test" }]), id: "fixture-local" });
  const runtime = await HubRuntime.start(data, registry);
  try {
    const task = runtime.store.createTask("holo"); runtime.store.updateTaskDefinition(task.id, { workspace_scope: workspace }); runtime.store.addMasterMessage(task.id, "change and verify");
    const turn = runtime.store.reserveHoloTurn(task.id)!;
    const call = (command: HubCommandEnvelope) => controlCommand(join(data, "control/connection.json"), turn.id, command);
    const invoke = async (operation: string, input: unknown) => {
      const command = envelope("InvokeCapability", { capability_id: "fixture-local", operation, input });
      const receipt = await call(command); assert.deepEqual(await call(command), receipt); return String(receipt.run_id);
    };
    const done = (id: string) => until(() => { const r = runtime.store.getRun(id)!; return ["Completed", "Failed", "Cancelled"].includes(r.state) ? r : null; });
    const read = await done(await invoke("read", { path: "value.txt" })); assert.equal(JSON.parse(read.result_json!).value.content, "before");
    const patch = await done(await invoke("apply_patch", { changes: [{ path: "value.txt", before_sha256: hash("before"), content: "after" }] }));
    assert.equal(patch.state, "Completed"); assert.equal(await readFile(join(workspace, "value.txt"), "utf8"), "after");
    assert.equal(await readFile(join(data, "runs", patch.id, "0.before"), "utf8"), "before");
    const search = await done(await invoke("search", { path: ".", query: "after" })); assert.ok(JSON.parse(search.result_json!).value.matches.length > 0);
    const inspected = await done(await invoke("inspect", { profile: "fixture.test" })); const proposal = JSON.parse(inspected.result_json!).value;
    const commandId = await invoke("run_command", { profile: "fixture.test", source_fingerprint: proposal.source_fingerprint });
    const request = await until(() => (runtime.store.snapshot().pending_requests as Array<{ id: string; run_id: string; revision: number }>).find(r => r.run_id === commandId));
    assert.equal(runtime.store.getRun(commandId)!.state, "Pending");
    const approve: HubCommandEnvelope = { protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(), type: "ResolveMasterRequest", target: request.id, expected_revision: request.revision, payload: { request_id: request.id, answer: { approved: true } } };
    runtime.service.handleMasterCommand(approve); runtime.service.handleMasterCommand(approve);
    const command = await done(commandId); assert.equal(command.state, "Completed", command.error_json ?? "");
    const result = JSON.parse(command.result_json!); assert.equal(result.verification.passed, true); assert.match(result.value.stdout_tail, /verified/); assert.equal(result.value.tree_empty, true);
    assert.ok(result.value.identity.creation_time); assert.equal(result.value.source_fingerprint, proposal.source_fingerprint);
    const staleId = await invoke("run_command", { profile: "fixture.test", source_fingerprint: proposal.source_fingerprint });
    const staleRequest = await until(() => (runtime.store.snapshot().pending_requests as Array<{ id: string; run_id: string; revision: number }>).find(r => r.run_id === staleId));
    await writeFile(join(workspace, "verify.cjs"), 'throw new Error("must not run changed source");');
    runtime.service.handleMasterCommand({ ...approve, command_id: randomUUID(), target: staleRequest.id, expected_revision: staleRequest.revision, payload: { request_id: staleRequest.id, answer: { approved: true } } });
    const stale = await done(staleId); assert.equal(stale.state, "Failed"); assert.equal(stale.effects, "none");
    assert.equal((runtime.store.snapshot().pending_requests as unknown[]).length, 0);
    runtime.store.pauseTask(task.id);
    await assert.rejects(() => invoke("apply_patch", { changes: [{ path: "value.txt", before_sha256: hash("after"), content: "stale" }] }), /stale|unauthorized/);
    assert.equal(await readFile(join(workspace, "value.txt"), "utf8"), "after");
  } finally { await runtime.close(); await rm(root, { recursive: true, force: true }); }
});

test("native patch refuses replaced inputs and restores its own partial application on cancel while excluding other writers", async () => {
  const root = await mkdtemp(join(tmpdir(), "nirai-v2-patch-")), workspace = join(root, "project"), data = join(root, "hub"); await mkdir(workspace); await mkdir(data);
  const cap = localFiles(new LocalFilePolicy([data]), data);
  const context = (run_id: string = randomUUID()) => ({ task_id: "task", run_id, turn_id: null, control_epoch: 1, workspace_scope: workspace });
  try {
    await writeFile(join(workspace, "original.txt"), "third party");
    const stale = await cap.invoke("apply_patch", { changes: [{ path: "original.txt", before_sha256: hash("before"), content: "after" }] }, context());
    assert.ok("state" in stale && stale.state === "Failed" && stale.effects === "none"); assert.equal(await readFile(join(workspace, "original.txt"), "utf8"), "third party");
    const changes = Array.from({ length: 16 }, (_, i) => ({ path: `${i}.txt`, before_sha256: i % 2 ? null : hash("original"), content: "x".repeat(8192) }));
    for (const change of changes) if (change.before_sha256) await writeFile(join(workspace, change.path), "original");
    const ctx = context();
    const execution = cap.invoke("apply_patch", { changes }, ctx);
    let cancelled = false;
    const poll = setInterval(() => {
      if (cancelled) return;
      void readFile(join(data, "runs", ctx.run_id, "journal.json"), "utf8").then(text => {
        if (!cancelled && JSON.parse(text).entries.some((entry: { phase: string }) => entry.phase === "applied")) { cancelled = true; void cap.cancel!(ctx.run_id); }
      }).catch(() => {});
    }, 1);
    const result = await execution; clearInterval(poll);
    assert.equal(cancelled, true); assert.ok("state" in result && result.state === "Cancelled", JSON.stringify(result));
    assert.ok("effects" in result && result.effects === "none", JSON.stringify(result));
    for (const change of changes) {
      if (change.before_sha256) assert.equal(await readFile(join(workspace, change.path), "utf8"), "original");
      else await assert.rejects(() => stat(join(workspace, change.path)), { code: "ENOENT" });
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("terminal Failed local writer is still reconciled when effects or cleanup remain uncertain", async () => {
  const root = await mkdtemp(join(tmpdir(), "nirai-v2-failed-recovery-")), workspace = join(root, "project"), data = join(root, "hub");
  await mkdir(workspace); await mkdir(data);
  const store = new HubStore(join(data, "recovery.sqlite3"));
  const policy = new LocalFilePolicy([data]);
  try {
    store.ensureResident("holo", "Holo");
    const task = store.createTask("holo");
    store.updateTaskDefinition(task.id, { workspace_scope: workspace });
    store.addMasterMessage(task.id, "write safely");
    const current = store.getTask(task.id)!;
    const run = store.createRun({ task_id: task.id, capability_id: "local", operation: "apply_patch",
      control_epoch: current.control_epoch, input: { changes: [] }, resources: ["local:workspace"] }, "possible");
    store.markRunRunning(run.id);
    store.recordRunResult(run.id, { state: "Failed", effects: "unknown", cleanup_state: "unknown",
      error: { message: "worker reply lost" } });

    const runDir = join(data, "runs", run.id);
    await mkdir(runDir, { recursive: true });
    const resolvedRoot = await policy.resolvePath(workspace, ".", false, true);
    await writeFile(join(runDir, "request.json"), JSON.stringify({ run_id: run.id, root: resolvedRoot, kind: "patch" }));
    await writeFile(join(runDir, "result.json"), JSON.stringify({ state: "Failed", effects: "applied", cleanup_state: "clear",
      result: { summary: "durable worker result" } }));

    await recoverLocalRuns(data, policy, store);
    const recovered = store.getRun(run.id)!;
    assert.equal(recovered.state, "Failed");
    assert.equal(recovered.effects, "applied");
    assert.equal(recovered.cleanup_state, "clear");
    assert.ok(recovered.supplemental_result_json?.includes("durable worker result"));
  } finally { store.close(); await rm(root, { recursive: true, force: true }); }
});

test("Windows job stops real descendants on cancellation, bounded output and parent disconnection", async () => {
  const root = await mkdtemp(join(tmpdir(), "nirai-v2-process-")), workspace = join(root, "project"), data = join(root, "hub"); await mkdir(workspace); await mkdir(data);
  const source = 'const {spawn}=require("node:child_process"); const fs=require("node:fs"); const child=spawn(process.execPath,["-e",\'setInterval(()=>{},100)\'],{stdio:"ignore"}); fs.writeFileSync("child.pid",String(child.pid)); setInterval(()=>{},100);';
  await writeFile(join(workspace, "tree.cjs"), source); await writeFile(join(workspace, "output.cjs"), 'setInterval(()=>process.stdout.write("x".repeat(65536)),1);');
  const profiles = ["tree", "output"].map(id => ({ id, workspace, executable: process.execPath, argv: [`${id}.cjs`], sources: [`${id}.cjs`], output_dirs: [], description: "isolated process test" }));
  const cap = localFiles(new LocalFilePolicy([data]), data, profiles);
  let recoveryStore: HubStore | undefined;
  const context = (run_id: string = randomUUID()) => ({ task_id: "task", run_id, turn_id: null, control_epoch: 1, workspace_scope: workspace });
  try {
    const proposal = await cap.invoke("inspect", { profile: "tree" }, context()); assert.ok("result" in proposal);
    const input = { profile: "tree", source_fingerprint: (proposal.result as { source_fingerprint: string }).source_fingerprint, timeout_ms: 10_000 };
    const ctx = context(); const running = cap.invoke("run_command", input, ctx);
    const pid = await until(() => readFile(join(workspace, "child.pid"), "utf8").catch(() => "")); await cap.cancel!(ctx.run_id);
    const stopped = await running; assert.ok("result" in stopped); assert.equal(stopped.state, "Cancelled", JSON.stringify(stopped));
    assert.equal((stopped.result as { tree_empty: boolean }).tree_empty, true); assert.throws(() => process.kill(Number(pid), 0));
    const outputProposal = await cap.invoke("inspect", { profile: "output" }, context()); assert.ok("result" in outputProposal);
    const noisy = await cap.invoke("run_command", { profile: "output", source_fingerprint: (outputProposal.result as { source_fingerprint: string }).source_fingerprint, output_bytes: 1024 }, context());
    assert.ok("result" in noisy); assert.equal((noisy.result as { reason: string }).reason, "output_limit"); assert.equal((noisy.result as { output_bytes: number }).output_bytes, 1024); assert.equal(noisy.cleanup_state, "clear");

    await rm(join(workspace, "child.pid"));
    const parentCode = `import {localFiles,LocalFilePolicy} from ${JSON.stringify(new URL("../src/hub/local-files.js", import.meta.url).href)};const cap=localFiles(new LocalFilePolicy([process.env.DATA]),process.env.DATA,JSON.parse(process.env.PROFILES));await cap.invoke('run_command',JSON.parse(process.env.INPUT),JSON.parse(process.env.CONTEXT));`;
    const store = recoveryStore = new HubStore(join(data, "recovery.sqlite3")); store.ensureResident("holo", "Holo");
    const task = store.createTask("holo"); store.updateTaskDefinition(task.id, { workspace_scope: workspace }); store.addMasterMessage(task.id, "work");
    const run = store.createRun({ task_id: task.id, capability_id: "local", operation: "run_command", control_epoch: store.getTask(task.id)!.control_epoch, input, resources: ["local:workspace"] }, "possible"); store.markRunRunning(run.id);
    const orphan = { ...context(run.id), task_id: task.id, control_epoch: run.control_epoch };
    const parent = spawn(process.execPath, ["--input-type=module", "-e", parentCode], { windowsHide: true, env: { ...process.env, DATA: data, PROFILES: JSON.stringify(profiles), INPUT: JSON.stringify(input), CONTEXT: JSON.stringify(orphan) }, stdio: "ignore" });
    const childPid = await until(() => readFile(join(workspace, "child.pid"), "utf8").catch(() => "")); parent.kill(); await new Promise<void>(resolve => parent.once("close", () => resolve()));
    await delay(1500);
    const saved = await readFile(join(data, "runs", orphan.run_id, "result.json"), "utf8").then(JSON.parse).catch(() => null);
    const proof = await probeLocalWorker(join(data, "runs", orphan.run_id, "request.json"));
    assert.equal(proof, "stopped", `worker liveness: ${proof}; result: ${JSON.stringify(saved)}`);
    if (saved) { assert.equal(saved.state, "Cancelled"); assert.equal(saved.result.tree_empty, true); }
    // The host itself may be terminated with its parent. A missing reply/result is
    // Interrupted/unknown, never success; the job probe independently proves cleanup.
    assert.throws(() => process.kill(Number(childPid), 0));
    store.recoverAfterRestart();
    await recoverLocalRuns(data, new LocalFilePolicy([data]), store);
    assert.equal(store.getTask(task.id)!.state, "Paused");
    const recovered = store.getRun(run.id)!;
    assert.equal(recovered.state, "Interrupted"); assert.equal(recovered.cleanup_state, "clear");
    if (!saved) { assert.equal(recovered.effects, "unknown"); assert.equal(store.resourcesAvailable(["local:workspace"]), false); }
    assert.ok(recovered.supplemental_result_json);
  } finally { recoveryStore?.close(); await rm(root, { recursive: true, force: true }); }
});
