export type TaskState = "Running" | "Paused" | "Completed" | "Failed" | "Cancelled";
export type RunState = "Pending" | "Running" | "Completed" | "Failed" | "Cancelled" | "Interrupted";
export type RunEffects = "none" | "applied" | "partial" | "unknown";
export type RunSideEffects = "none" | "possible";
export type RequestState = "Pending" | "Resolved" | "Cancelled";
export type RequestKind = "approval";

export interface ResidentRecord {
  id: string;
  display_name: string;
  role: string | null;
  persona_path: string | null;
  capability_id: string | null;
  model: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResidentConfiguration {
  display_name: string;
  role?: string | null;
  persona_path?: string | null;
  capability_id?: string | null;
  model?: string | null;
}

export interface ConversationRecord {
  id: string;
  kind: "task" | "say" | "whisper";
  task_id: string | null;
  resident_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageRecord {
  id: string;
  conversation_id: string;
  seq: number;
  sender: string;
  content: string;
  audience: string[];
  reply_to_message_id: string | null;
  created_at: string;
}

export interface ChatResponseRecord {
  message_id: string;
  resident_id: string;
  state: "pending" | "running" | "completed" | "failed" | "interrupted";
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatContext {
  conversation: ConversationRecord;
  resident: ResidentRecord;
  messages: ChatMessageRecord[];
}

export interface ResidentChatMessage extends ChatMessageRecord {
  channel: "say" | "whisper";
}

export interface ResidentChatContext extends ChatContext {
  messages: ResidentChatMessage[];
}

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
  created_at: string;
  updated_at: string;
  started_at: string | null;
  ended_at: string | null;
  result_summary: string | null;
}

export interface HoloTurnRecord {
  id: string;
  task_id: string;
  control_epoch: number;
  instruction_seq: number;
  await_master: boolean;
  completion_summary: string | null;
  settings_json: string;
  created_at: string;
  ended_at: string | null;
  end_reason: string | null;
}

export interface RunRecord {
  id: string;
  task_id: string;
  turn_id: string | null;
  capability_id: string;
  operation: string;
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
  turn_id?: string;
  capability_id: string;
  operation: string;
  control_epoch: number;
  input: unknown;
  workspace_scope?: string;
  resources?: string[];
}
