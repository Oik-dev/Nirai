import type { ArtifactReference, RunEffects, RunSideEffects, VerificationResult, RunRecord } from "../shared/types.js";
import type { HubSettings } from "../shared/settings.js";

export type CapabilityAvailability = "ready" | "busy" | "blocked" | "unavailable";

export interface CapabilityOperationSpec {
  readonly side_effects: RunSideEffects;
  readonly resources?: readonly string[];
  readonly approval?: string;
  readonly risks?: readonly string[];
  readonly validateInput?: (input: unknown) => void;
  readonly validateResult?: (result: CapabilityResult) => void;
}

export interface CapabilityContext {
  task_id: string;
  run_id: string;
  turn_id: string | null;
  control_epoch: number;
  workspace_scope: string | null;
  settings?: HubSettings;
  inspectRun?: (runId: string) => RunRecord;
  stopRun?: (runId: string) => RunRecord;
}

export interface CapabilityResult {
  state: "Completed" | "Failed" | "Cancelled";
  effects: RunEffects;
  cleanup_state?: "clear" | "pending" | "unknown";
  result?: unknown;
  error?: unknown;
  artifacts?: ArtifactReference[];
  verification?: VerificationResult;
}

export interface Capability {
  readonly id: string;
  readonly operations: ReadonlyMap<string, CapabilityOperationSpec>;
  availability(): { state: CapabilityAvailability; reason?: string };
  prepare?(operation: string, input: unknown, context: CapabilityContext): Promise<{ approval?: string }>;
  invoke(operation: string, input: unknown, context: CapabilityContext): Promise<CapabilityResult | { accepted: true }>;
  cancel?(runId: string): Promise<CapabilityResult | void>;
}

export class CapabilityRegistry {
  private readonly capabilities = new Map<string, Capability>();
  onChanged: () => void = () => {};

  register(capability: Capability): void {
    if (this.capabilities.has(capability.id)) throw new Error(`capability already registered: ${capability.id}`);
    this.capabilities.set(capability.id, capability);
    this.onChanged();
  }

  changed(): void { this.onChanged(); }

  get(id: string): Capability {
    const capability = this.capabilities.get(id);
    if (!capability) throw new Error(`capability not found: ${id}`);
    return capability;
  }

  availability(id: string): ReturnType<Capability["availability"]> {
    try {
      return this.get(id).availability();
    } catch (error) {
      return { state: "unavailable", reason: error instanceof Error ? error.message : String(error) };
    }
  }

  list(): Array<{ id: string; operations: string[]; availability: ReturnType<Capability["availability"]> }> {
    return [...this.capabilities.values()].map(capability => ({
      id: capability.id,
      operations: [...capability.operations.keys()],
      availability: this.availability(capability.id),
    }));
  }
}
