import type { RunEffects } from "../shared/types.js";

export type CapabilityAvailability = "ready" | "busy" | "blocked" | "unavailable";

export interface CapabilityContext {
  task_id: string;
  run_id: string;
  parent_run_id: string | null;
  control_epoch: number;
  workspace_scope: string | null;
}

export interface CapabilityResult {
  state: "Completed" | "Failed" | "Cancelled";
  effects: RunEffects;
  result?: unknown;
  error?: unknown;
}

export interface Capability {
  readonly id: string;
  readonly operations: ReadonlySet<string>;
  availability(): { state: CapabilityAvailability; reason?: string };
  invoke(operation: string, input: unknown, context: CapabilityContext): Promise<CapabilityResult>;
  cancel?(runId: string): Promise<void>;
}

export class CapabilityRegistry {
  private readonly capabilities = new Map<string, Capability>();

  register(capability: Capability): void {
    if (this.capabilities.has(capability.id)) {
      throw new Error(`capability already registered: ${capability.id}`);
    }
    this.capabilities.set(capability.id, capability);
  }

  get(id: string): Capability {
    const capability = this.capabilities.get(id);
    if (!capability) throw new Error(`capability not found: ${id}`);
    return capability;
  }

  list(): Array<{ id: string; operations: string[]; availability: ReturnType<Capability["availability"]> }> {
    return [...this.capabilities.values()].map((capability) => ({
      id: capability.id,
      operations: [...capability.operations],
      availability: capability.availability(),
    }));
  }
}
