import type { CommandResult, CreateRunInput, HubCommandEnvelope, RunRecord } from "../shared/types.js";
import { randomUUID } from "node:crypto";
import type { HubSettings } from "../shared/settings.js";
import { CapabilityRegistry, type CapabilityResult } from "./capability.js";
import { HubStore } from "./store.js";

export class TaskEngine {
  private scheduled = false;
  private closing = false;
  private closed = false;
  private started = false;
  private readonly cancelling = new Set<string>();
  private readonly executing = new Set<Promise<void>>();
  private readonly deadlines = new Map<string, NodeJS.Timeout>();
  responseCommand: (runId: string, envelope: HubCommandEnvelope) => CommandResult = () => { throw new Error("response handler unavailable"); };
  onChanged: () => void = () => {};

  constructor(private readonly store: HubStore, readonly registry: CapabilityRegistry) {
    registry.onChanged = () => this.schedule();
  }

  start(): void { this.started = true; this.schedule(); }

  createRun(input: CreateRunInput): RunRecord {
    const operation = this.registry.get(input.capability_id).operations.get(input.operation);
    if (!operation) throw new Error(`unsupported capability operation: ${input.operation}`);
    operation.validateInput?.(input.input);
    return this.store.transaction(() => {
      const run = this.store.createRun({ ...input, resources: [...(operation.resources ?? [])] }, operation.side_effects);
      if (operation.approval) this.store.createMasterRequest({ task_id: run.task_id, run_id: run.id, kind: "approval", prompt: operation.approval });
      return run;
    });
  }

  schedule(): void {
    if (!this.started || this.scheduled || this.closing) return;
    this.scheduled = true;
    setImmediate(() => {
      this.scheduled = false;
      if (this.closing) return;
      this.evaluate();
    });
  }

  private evaluate(): void {
    for (const run of this.store.listRuns()) {
      if (run.stop_requested_at && run.state === "Running") this.stop(run);
      if (run.state !== "Pending") continue;
      const task = this.store.getTask(run.task_id)!;
      if (task.state !== "Running" || task.control_epoch !== run.control_epoch) continue;
      this.track(this.dispatchRun(run.id));
    }
    for (const task of this.store.listTasks()) {
      const binding = this.registry.responseFor(task.resident_id);
      if (!binding || !this.store.needsResponse(task.id)) continue;
      const capability = this.registry.get(binding.capability_id);
      if (this.registry.availability(capability.id).state !== "ready") continue;
      const operation = capability.operations.get(binding.operation)!;
      const run = this.store.reserveResponse({ task_id: task.id, ...binding, kind: "response",
        control_epoch: task.control_epoch, input: { task_id: task.id }, resources: [...(operation.resources ?? [])],
        ...(operation.delivery ? { delivery_id: randomUUID() } : {}) }, operation.side_effects);
      if (run) this.track(this.invoke(run));
    }
    this.onChanged();
  }

  private track(promise: Promise<void>): void {
    this.executing.add(promise);
    void promise.catch(() => {}).finally(() => this.executing.delete(promise));
  }

  async dispatchRun(runId: string): Promise<void> {
    const run = this.store.getRun(runId);
    if (!run) throw new Error(`run not found: ${runId}`);
    if (run.state !== "Pending") throw new Error("run is not pending");
    let capability;
    try {
      capability = this.registry.get(run.capability_id);
      const operation = capability.operations.get(run.operation);
      if (!operation) throw new Error(`unsupported capability operation: ${run.operation}`);
      if (run.side_effects !== operation.side_effects) throw new Error("run side-effect policy does not match capability operation");
      operation.validateInput?.(JSON.parse(run.input_json));
    } catch (error) {
      this.store.recordRunResult(run.id, { state: "Failed", effects: "none", cleanup_state: "clear", error: { message: String(error) } });
      throw error;
    }
    const availability = this.registry.availability(capability.id);
    if (availability.state !== "ready") throw new Error(`capability is ${availability.state}`);
    const running = this.store.transaction(() => this.store.markRunRunning(runId));
    await this.invoke(running);
  }

  private report(runId: string, result: CapabilityResult): void {
    if (this.closed) return;
    const run = this.store.getRun(runId)!;
    if (!["Completed", "Failed", "Cancelled"].includes(result.state) || !["none", "applied", "partial", "unknown"].includes(result.effects)) throw new Error("invalid capability result");
    this.registry.get(run.capability_id).operations.get(run.operation)?.validateResult?.(result);
    this.store.recordRunResult(runId, result);
    clearTimeout(this.deadlines.get(runId));
    this.deadlines.delete(runId);
    this.onChanged();
    this.schedule();
  }

  private async invoke(run: RunRecord): Promise<void> {
    const capability = this.registry.get(run.capability_id);
    if (run.kind === "response") {
      const settings = JSON.parse(run.settings_json) as HubSettings;
      this.deadlines.set(run.id, setTimeout(() => {
        this.store.interruptRun(run.id, "Response deadline exceeded; execution requires reconciliation");
        this.stop(run);
        this.onChanged();
        this.schedule();
      }, settings.response_deadline_ms));
    }
    try {
      const result = await capability.invoke(run.operation, JSON.parse(run.input_json), {
        task_id: run.task_id, run_id: run.id, parent_run_id: run.parent_run_id,
        control_epoch: run.control_epoch, workspace_scope: run.workspace_scope,
        settings: JSON.parse(run.settings_json),
        command: envelope => this.responseCommand(run.id, envelope),
        report: result => this.report(run.id, result),
        observeDelivery: state => {
          if (this.closed) return;
          this.store.transaction(() => this.store.recordDelivery(run.id, state));
          this.onChanged(); this.schedule();
        },
      });
      if (this.closed) return;
      if (!("accepted" in result)) this.report(run.id, result);
      if (this.store.getRun(run.id)?.state !== "Running") this.responseFinished(run.id);
    } catch (error) {
      if (this.closed) return;
      const current = this.store.getRun(run.id);
      if (current?.state === "Running") this.report(run.id, {
        state: "Failed", effects: run.side_effects === "possible" ? "unknown" : "none",
        cleanup_state: run.side_effects === "possible" ? "unknown" : "clear", error: { message: String(error) },
      });
      throw error;
    } finally {
      if (!this.closed) { this.onChanged(); this.schedule(); }
    }
  }

  private stop(run: RunRecord): void {
    if (this.cancelling.has(run.id)) return;
    this.cancelling.add(run.id);
    this.track((async () => {
      try {
        const result = await this.registry.get(run.capability_id).cancel?.(run.id);
        // Acknowledging cancel is not proof of physical termination.
        if (result) this.report(run.id, result);
      } catch { /* Keep the saved Run and its resources until reconciliation. */ }
    })());
  }

  responseFinished(runId: string): void {
    clearTimeout(this.deadlines.get(runId));
    this.deadlines.delete(runId);
  }

  async close(): Promise<void> {
    this.closing = true;
    this.store.prepareShutdown();
    for (const run of this.store.listRuns()) if (run.state === "Running") this.stop(run);
    let timer: NodeJS.Timeout | undefined;
    await Promise.race([Promise.allSettled([...this.executing]), new Promise<void>(resolve => { timer = setTimeout(resolve, 1000); })]);
    clearTimeout(timer);
    for (const timer of this.deadlines.values()) clearTimeout(timer);
    this.deadlines.clear();
    for (const run of this.store.listRuns()) if (run.state === "Running") this.store.interruptRun(run.id, "Hub stopped before final result");
    this.closed = true;
  }
}
