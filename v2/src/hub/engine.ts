import { CapabilityRegistry } from "./capability.js";
import { HubStore } from "./store.js";

export class TaskEngine {
  constructor(
    private readonly store: HubStore,
    private readonly registry: CapabilityRegistry,
  ) {}

  async dispatchRun(runId: string): Promise<void> {
    const run = this.store.getRun(runId);
    if (!run) throw new Error(`run not found: ${runId}`);
    if (run.state !== "Pending") throw new Error("run is not pending");

    const capability = this.registry.get(run.capability_id);
    if (!capability.operations.has(run.operation)) {
      throw new Error(`unsupported capability operation: ${run.operation}`);
    }
    const availability = capability.availability();
    if (availability.state !== "ready") {
      throw new Error(`capability is ${availability.state}${availability.reason ? `: ${availability.reason}` : ""}`);
    }

    const running = this.store.markRunRunning(runId);
    const result = await capability.invoke(
      running.operation,
      JSON.parse(running.input_json) as unknown,
      {
        task_id: running.task_id,
        run_id: running.id,
        parent_run_id: running.parent_run_id,
        control_epoch: running.control_epoch,
        workspace_scope: running.workspace_scope,
      },
    );
    this.store.recordRunResult(runId, result);
  }
}
