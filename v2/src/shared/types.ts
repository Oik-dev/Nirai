export type TaskState = "Running" | "Paused" | "Completed" | "Failed" | "Cancelled";
export type RunState = "Pending" | "Running" | "Completed" | "Failed" | "Cancelled" | "Interrupted";
export type RunKind = "response" | "action";
export type RunEffects = "none" | "applied" | "partial" | "unknown";
export type RunSideEffects = "none" | "possible";
export type RequestState = "Pending" | "Resolved" | "Cancelled";
export type RequestKind = "approval" | "input";

export interface CompletionCriterion {
  id: string;
  text: string;
  required: boolean;
  verification_kind: string | null;
}

export interface CompletionEvidence {
  criterion_id: string;
  artifact_ref: string;
  fingerprint: string;
  verification_run_id?: string;
}

export interface ArtifactReference {
  ref: string;
  fingerprint: string;
  ownership: "temporary" | "project" | "shared" | "recovery";
}

export interface VerificationResult {
  kind: string;
  artifact_ref: string;
  fingerprint: string;
  passed: boolean;
}

export interface TaskRecord {
  id: string;
  title: string;
  resident_id: string;
  objective: string | null;
  initial_message_id: string | null;
  workspace_scope: string | null;
  completion_criteria: CompletionCriterion[];
  state: TaskState;
  resume_enabled: boolean;
  revision: number;
  control_epoch: number;
  conversation_id: string;
  handled_instruction_seq: number;
  wake_seq: number;
  handled_wake_seq: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  ended_at: string | null;
  result_summary: string | null;
}

export interface RunRecord {
  id: string;
  task_id: string;
  capability_id: string;
  operation: string;
  kind: RunKind;
  parent_run_id: string | null;
  state: RunState;
  control_epoch: number;
  side_effects: RunSideEffects;
  input_json: string;
  input_fingerprint: string;
  workspace_scope: string | null;
  result_json: string | null;
  supplemental_result_json: string | null;
  error_json: string | null;
  effects: RunEffects;
  cleanup_state: "clear" | "pending" | "unknown";
  failure_resolution: "not_required" | "unresolved" | "recovered" | "not_needed";
  resolution_note: string | null;
  retry_of: string | null;
  delivery_id: string | null;
  delivery_state: "unsent" | "started" | "acknowledged" | "unknown" | null;
  context_instruction_seq: number;
  context_wake_seq: number;
  resources_json: string;
  settings_json: string;
  stop_requested_at: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface HubCommandEnvelope {
  protocol_version: number;
  command_id: string;
  issued_at: string;
  type: string;
  target: string | null;
  expected_revision?: number;
  payload: Record<string, unknown>;
}

export type CommandResult = Record<string, unknown>;

export interface CreateRunInput {
  task_id: string;
  capability_id: string;
  operation: string;
  kind: RunKind;
  parent_run_id?: string;
  control_epoch: number;
  input: unknown;
  workspace_scope?: string;
  retry_of?: string;
  delivery_id?: string;
  resources?: string[];
}
