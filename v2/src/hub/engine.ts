import type { CreateRunInput, HoloTurnRecord, RunRecord } from "../shared/types.js";
import { CapabilityRegistry, type CapabilityContext, type CapabilityResult } from "./capability.js";
import { HubStore } from "./store.js";

export interface HoloDriver {
  availability(taskId?: string): { state: "ready" | "busy" | "blocked" | "unavailable"; reason?: string };
  start(turn: HoloTurnRecord): void;
}

export class TaskEngine {
  private scheduled = false;
  private closing = false;
  private closed = false;
  private started = false;
  private readonly cancelling = new Set<string>();
  private readonly preparing = new Set<string>();
  private readonly executing = new Set<Promise<void>>();

  holo: HoloDriver | null = null;
  onChanged: () => void = () => {};

  constructor(private readonly store: HubStore, readonly registry: CapabilityRegistry) {
    registry.onChanged = () => this.schedule();
  }

  start(): void {
    this.started = true;
    this.schedule();
  }

  createRun(input: CreateRunInput): RunRecord {
    const operation = this.registry.get(input.capability_id).operations.get(input.operation);
    if (!operation) throw new Error(`unsupported capability operation: ${input.operation}`);
    operation.validateInput?.(input.input);
    return this.store.transaction(() => {
      const run = this.store.createRun({ ...input, resources: [...(operation.resources ?? [])] }, operation.side_effects);
      if (operation.approval) {
        this.store.createMasterRequest({
          task_id: run.task_id,
          run_id: run.id,
          kind: "approval",
          prompt: operation.approval,
        });
      }
      return run;
    });
  }

  schedule(): void {
    if (!this.started || this.scheduled || this.closing) return;
    this.scheduled = true;
    setImmediate(() => {
      this.scheduled = false;
      if (!this.closing) this.evaluate();
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
      if (!this.holo) break;
      if (this.holo.availability(task.id).state !== "ready") continue;
      const turn = this.store.reserveHoloTurn(task.id);
      if (turn) this.holo.start(turn);
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
      this.store.recordRunResult(run.id, {
        state: "Failed",
        effects: "none",
        cleanup_state: "clear",
        error: { message: String(error) },
      });
      throw error;
    }

    const availability = this.registry.availability(capability.id);
    if (availability.state !== "ready") throw new Error(`capability is ${availability.state}`);
    if (!this.store.resourcesAvailable(JSON.parse(run.resources_json), run.id) || this.preparing.has(run.id)) return;

    if (capability.prepare) {
      this.preparing.add(run.id);
      try {
        const prepared = await capability.prepare(run.operation, JSON.parse(run.input_json), this.context(run));
        if (this.closing) return;
        const current = this.store.getRun(runId);
        if (!current || current.state !== "Pending") return;
        if (prepared.approval) {
          this.store.ensureRunApproval(run.id, prepared.approval);
          this.onChanged();
        }
      } catch (error) {
        this.store.recordRunResult(run.id, {
          state: "Failed",
          effects: "none",
          cleanup_state: "clear",
          error: { message: String(error) },
        });
        this.onChanged();
        this.schedule();
        return;
      } finally {
        this.preparing.delete(run.id);
      }
    }

    const current = this.store.getRun(runId);
    if (!current || current.state !== "Pending") return;
    const running = this.store.transaction(() => this.store.markRunRunning(runId));
    await this.invoke(running);
  }

  private report(runId: string, result: CapabilityResult): void {
    if (this.closed) return;
    const run = this.store.getRun(runId);
    if (!run) return;
    if (!["Completed", "Failed", "Cancelled"].includes(result.state)
      || !["none", "applied", "partial", "unknown"].includes(result.effects)) {
      throw new Error("invalid capability result");
    }
    this.registry.get(run.capability_id).operations.get(run.operation)?.validateResult?.(result);
    this.store.recordRunResult(runId, result);
    this.onChanged();
    this.schedule();
  }

  private async invoke(run: RunRecord): Promise<void> {
    const capability = this.registry.get(run.capability_id);
    try {
      const result = await capability.invoke(run.operation, JSON.parse(run.input_json), this.context(run));
      if (this.closed) return;
      if (!("accepted" in result)) this.report(run.id, result);
    } catch (error) {
      if (this.closed) return;
      const current = this.store.getRun(run.id);
      if (current?.state === "Running") {
        this.report(run.id, {
          state: "Failed",
          effects: run.side_effects === "possible" ? "unknown" : "none",
          cleanup_state: run.side_effects === "possible" ? "unknown" : "clear",
          error: { message: String(error) },
        });
      }
      throw error;
    } finally {
      if (!this.closed) {
        this.onChanged();
        this.schedule();
      }
    }
  }

  private context(run: RunRecord): CapabilityContext {
    const ownAction = (id: string) => {
      const target = this.store.getRun(id);
      if (!target || target.task_id !== run.task_id || target.id === run.id) {
        throw new Error("only another action in this Task can be inspected or stopped");
      }
      return target;
    };
    return {
      task_id: run.task_id,
      run_id: run.id,
      turn_id: run.turn_id,
      control_epoch: run.control_epoch,
      workspace_scope: run.workspace_scope,
      settings: JSON.parse(run.settings_json),
      inspectRun: ownAction,
      stopRun: id => {
        ownAction(id);
        const stopped = this.store.requestActionStop(id);
        this.schedule();
        return stopped;
      },
    };
  }

  private stop(run: RunRecord): void {
    if (this.cancelling.has(run.id)) return;
    this.cancelling.add(run.id);
    this.track((async () => {
      try {
        const result = await this.registry.get(run.capability_id).cancel?.(run.id);
        if (result) this.report(run.id, result);
      } catch {
        // Saved Action remains authoritative until recovery can reconcile it.
      } finally {
        this.cancelling.delete(run.id);
      }
    })());
  }

  async close(): Promise<void> {
    this.closing = true;
    this.store.prepareShutdown();
    for (const run of this.store.listRuns()) if (run.state === "Running") this.stop(run);

    let timer: NodeJS.Timeout | undefined;
    await Promise.race([
      Promise.allSettled([...this.executing]),
      new Promise<void>(resolve => { timer = setTimeout(resolve, 1000); }),
    ]);
    clearTimeout(timer);

    for (const run of this.store.listRuns()) {
      if (run.state === "Running") this.store.interruptRun(run.id, "Hub stopped before final result");
    }
    this.closed = true;
  }
}
