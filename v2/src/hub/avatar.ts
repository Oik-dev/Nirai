import type { Capability, CapabilityContext, CapabilityOperationSpec, CapabilityResult } from "./capability.js";
import type { HubStore } from "./store.js";
import type { AppearanceCatalog, AvatarRuntimeReport, AvatarState, DesiredAppearance } from "../shared/appearance.js";
import { defaultAppearance, validateAppearance, validateCatalog } from "../shared/appearance.js";
import type { RunRecord } from "../shared/types.js";

const modelId = /^[a-f0-9]{64}$/;

function fields(input: unknown, allowed: readonly string[]): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)
    || Object.keys(input).some(key => !allowed.includes(key))) throw new Error("invalid avatar operation input");
  return input as Record<string, unknown>;
}

export class AvatarCapability implements Capability {
  readonly id = "avatar";
  private readonly reports = new Map<string, AvatarRuntimeReport>();
  readonly operations: ReadonlyMap<string, CapabilityOperationSpec> = new Map([
    ["inspect", {
      side_effects: "none",
      input_schema: { type: "object", additionalProperties: false, properties: {} },
      validateInput: (input: unknown) => { fields(input, []); },
    }],
    ["set", {
      // This operation only saves its completed Run result. World projects that
      // saved choice; no external apply is dispatched before the durable result.
      side_effects: "none", resources: ["avatar:appearance"],
      input_schema: {
        type: "object", additionalProperties: false,
        required: ["model_id", "expected_revision", "appearance"],
        properties: {
          model_id: { type: "string", pattern: "^[a-f0-9]{64}$" },
          expected_revision: { type: ["string", "null"] },
          appearance: { type: "object", description: "Complete choice from avatar.inspect: expression {id,weight} or null; wardrobe includes every legacy item id with a boolean; when capabilities.controls exists, choices includes every control id with one of its option ids. Use semantic options, never mesh or morph names." },
        },
      },
      validateInput: (input: unknown) => {
        const value = fields(input, ["model_id", "expected_revision", "appearance"]);
        if (typeof value.model_id !== "string" || !modelId.test(value.model_id)
          || !(value.expected_revision === null || typeof value.expected_revision === "string" && value.expected_revision.length > 0 && value.expected_revision.length <= 128)
          || !value.appearance || typeof value.appearance !== "object") throw new Error("invalid avatar selection");
      },
    }],
  ]);

  constructor(private readonly store: HubStore, private readonly onChanged: () => void = () => {}) {}

  availability() { return { state: "ready" as const }; }

  report(input: AvatarRuntimeReport): boolean {
    const value = fields(input, ["resident_id", "model_path", "model_id", "token", "capabilities", "applied_revision", "status", "error"]);
    if (typeof value.resident_id !== "string" || !this.store.residentExists(value.resident_id)
      || typeof value.model_path !== "string" || this.store.getSettings().value.resident_avatars[value.resident_id] !== value.model_path
      || !["ready", "unavailable"].includes(String(value.status))
      || !(value.model_id === null || typeof value.model_id === "string" && modelId.test(value.model_id))
      || !(value.token === null || typeof value.token === "string" && value.token.length > 0 && value.token.length <= 128)
      || !(value.applied_revision === null || typeof value.applied_revision === "string" && value.applied_revision.length <= 128)
      || value.error !== undefined && (typeof value.error !== "string" || value.error.length > 1024)) throw new Error("invalid Avatar runtime report");
    const capabilities = value.capabilities === null ? null : validateCatalog(value.capabilities);
    if (value.status === "ready" && (!value.model_id || !value.token || !capabilities)) throw new Error("ready Avatar has no model or capabilities");
    const report: AvatarRuntimeReport = {
      resident_id: value.resident_id, model_path: value.model_path,
      model_id: value.model_id as string | null, token: value.token as string | null,
      capabilities, applied_revision: value.applied_revision as string | null,
      status: value.status as "ready" | "unavailable",
      ...(value.error === undefined ? {} : { error: value.error as string }),
    };
    if (report.status !== "ready") report.applied_revision = null;
    if (report.applied_revision !== null && !this.validAppliedRevision(report)) throw new Error("unknown Avatar applied revision");
    if (JSON.stringify(this.reports.get(report.resident_id)) === JSON.stringify(report)) return false;
    this.reports.set(report.resident_id, report);
    this.onChanged();
    return true;
  }

  reset(residentId?: string): boolean {
    const changed = residentId === undefined ? this.reports.size > 0 : this.reports.has(residentId);
    if (residentId === undefined) this.reports.clear(); else this.reports.delete(residentId);
    if (changed) this.onChanged();
    return changed;
  }

  states(): AvatarState[] {
    const runs = this.store.listRuns();
    return this.residentIds().map(id => this.state(id, runs));
  }

  private residentIds(): string[] {
    return this.store.listResidents().map(item => item.id);
  }

  private desired(residentId: string, id: string, catalog: AppearanceCatalog, runs = this.store.listRuns()): DesiredAppearance {
    // listRuns has stable rowid order. The shared resource serializes set Runs,
    // and expected_revision prevents overwriting a newer choice with stale input.
    for (let index = runs.length - 1; index >= 0; index--) {
      const run = runs[index]!;
      if (run.capability_id !== this.id || run.operation !== "set" || run.state !== "Completed"
        || this.store.getTask(run.task_id)?.resident_id !== residentId) continue;
      const value = JSON.parse(run.result_json ?? "{}").value;
      if (value?.desired_saved !== true || value.model_id !== id || value.revision !== run.id) continue;
      return { revision: run.id, appearance: validateAppearance(value.appearance, catalog) };
    }
    return { revision: null, appearance: defaultAppearance(catalog) };
  }

  private validAppliedRevision(report: AvatarRuntimeReport): boolean {
    const run = this.store.getRun(report.applied_revision!);
    if (!run || run.capability_id !== this.id || run.operation !== "set" || run.state !== "Completed"
      || this.store.getTask(run.task_id)?.resident_id !== report.resident_id) return false;
    const value = JSON.parse(run.result_json ?? "{}").value;
    return value?.desired_saved === true && value.model_id === report.model_id && value.revision === run.id;
  }

  private state(residentId: string, runs?: RunRecord[]): AvatarState {
    const path = this.store.getSettings().value.resident_avatars[residentId] ?? "";
    const observed = this.reports.get(residentId);
    const current = observed?.model_path === path ? observed : undefined;
    const report: AvatarRuntimeReport = current ?? {
      resident_id: residentId, model_path: path, model_id: null, token: null,
      capabilities: null, applied_revision: null, status: "unavailable",
      error: path ? "Avatar runtime is not ready" : "No Avatar selected",
    };
    let desired: DesiredAppearance | null = null;
    try {
      desired = report.model_id && report.capabilities ? this.desired(residentId, report.model_id, report.capabilities, runs) : null;
    } catch {
      return { ...report, status: "unavailable", applied_revision: null, desired: null, display_applied: false,
        error: "Saved appearance does not match the current Avatar capabilities" };
    }
    return {
      ...report, desired,
      display_applied: report.status === "ready" && desired !== null && report.applied_revision === desired.revision,
    };
  }

  async invoke(operation: string, input: unknown, context: CapabilityContext): Promise<CapabilityResult> {
    const task = this.store.getTask(context.task_id);
    if (!task || task.state !== "Running" || task.control_epoch !== context.control_epoch) throw new Error("Avatar Task is no longer active");
    const run = this.store.getRun(context.run_id);
    if (!run || run.task_id !== task.id || run.state !== "Running" || run.stop_requested_at) throw new Error("Avatar Run is no longer active");
    const state = this.state(task.resident_id);
    if (operation === "inspect") {
      fields(input, []);
      return { state: "Completed", effects: "none", cleanup_state: "clear", result: state };
    }
    if (operation !== "set") throw new Error("unsupported avatar operation");
    this.operations.get("set")!.validateInput!(input);
    const value = input as Record<string, unknown>;
    if (state.status !== "ready" || !state.capabilities || !state.desired) throw new Error("Avatar runtime is not ready");
    if (value.model_id !== state.model_id) throw new Error("Avatar model changed; inspect before choosing again");
    if (value.expected_revision !== state.desired.revision) throw new Error("stale Avatar appearance revision");
    const appearance = validateAppearance(value.appearance, state.capabilities);
    return {
      state: "Completed", effects: "none", cleanup_state: "clear",
      result: {
        desired_saved: true, display_applied: false,
        resident_id: task.resident_id, model_id: state.model_id,
        revision: context.run_id, previous_revision: state.desired.revision, appearance,
      },
    };
  }
}
