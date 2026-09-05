export interface ProtocolMessage<TPayload extends Record<string, unknown> = Record<string, unknown>> {
  readonly type: string
  readonly id?: string
  readonly ts: string
  readonly payload: TPayload
}

export interface ChatEntryPayload extends Record<string, unknown> {
  readonly entry_id?: string
  readonly ts: string
  readonly kind: 'say' | 'whisper' | 'resident_say' | 'resident_whisper' | 'resident_chat' | 'holo_say' | 'task' | 'system'
  readonly from: string
  readonly to?: string
  readonly text: string
  readonly session: string
  readonly request_id?: string
  readonly task_id?: string
  readonly agent_session_id?: string
}

export interface ChatSessionSummaryPayload extends Record<string, unknown> {
  readonly id: string
  readonly title: string
  readonly created_at: string
  readonly updated_at: string
}

export interface ActionPayload extends Record<string, unknown> {
  readonly name: string
  readonly command: string
  readonly args: Record<string, unknown>
}

export interface ResponseStatePayload extends Record<string, unknown> {
  readonly active: boolean
  readonly request_id?: string
  readonly session_id?: string
}

export interface ResidentTtsPayload extends Record<string, unknown> {
  readonly enabled: boolean
  readonly provider: string
  readonly speaker_uuid: string | null
  readonly style_id: number | null
  readonly speed: number
  readonly pitch: number
  readonly intonation: number
}

export interface ResidentPayload extends Record<string, unknown> {
  readonly name: string
  readonly brain: string | null
  readonly brain_model: string | null
  readonly brain_reasoning_effort: string | null
  readonly avatar: string | null
  readonly location: string
  readonly tts: ResidentTtsPayload
}

export interface BrainReasoningEffortPayload extends Record<string, unknown> {
  readonly id: string
  readonly display_name: string
}

export interface BrainModelPayload extends Record<string, unknown> {
  readonly id: string
  readonly display_name: string
  readonly default_reasoning_effort?: string | null
  readonly reasoning_efforts?: readonly BrainReasoningEffortPayload[]
  readonly capabilities?: Omit<AgentCapabilitiesPayload, 'conversation'>
}

export interface AgentCapabilitiesPayload extends Record<string, unknown> {
  readonly conversation: boolean
  readonly agent_work: boolean
  readonly approval: boolean
  readonly question: boolean
  readonly plan: boolean
  readonly todo: boolean
  readonly subagent: boolean
  readonly file_diff: boolean
  readonly command_result: boolean
  readonly artifact: boolean
}

export interface BrainProviderPayload extends Record<string, unknown> {
  readonly name: string
  readonly display_name: string
  readonly available: boolean
  readonly connected: boolean
  readonly configuration_mode: string
  readonly models: readonly BrainModelPayload[]
  readonly default_model: string | null
  readonly default_reasoning_effort: string | null
  readonly custom_model_allowed: boolean
  readonly capabilities?: AgentCapabilitiesPayload
}

export type AgentEventTypePayload =
  | 'assistant_message'
  | 'status_message'
  | 'tool_call'
  | 'command_execution'
  | 'file_change'
  | 'diff'
  | 'approval_request'
  | 'question_request'
  | 'plan'
  | 'todo_update'
  | 'subagent_update'
  | 'artifact'
  | 'run_state'
  | 'error'

export type AgentRunStatePayload =
  | 'queued'
  | 'starting'
  | 'running'
  | 'waiting_for_master'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface AgentEventPayload extends Record<string, unknown> {
  readonly event_id: string
  readonly seq: number
  readonly ts: string
  readonly task_id: string
  readonly agent_session_id: string
  readonly resident: string
  readonly provider: string
  readonly type: AgentEventTypePayload
  readonly payload: Record<string, unknown>
}

export interface AgentPendingInputPayload extends Record<string, unknown> {
  readonly type: 'approval_request' | 'question_request' | 'plan'
  readonly request_id: string
  readonly payload: Record<string, unknown>
}

export interface AgentSessionSnapshotPayload extends Record<string, unknown> {
  readonly agent_session_id: string
  readonly task_id: string
  readonly resident: string
  readonly provider: string
  readonly state: AgentRunStatePayload
  readonly working_dir: string
  readonly started_at: string
  readonly updated_at: string
  readonly last_event_seq: number
  readonly final_summary: string | null
  readonly origin_chat_session_id?: string | null
  readonly task_phase?: TaskUpdatePayload['phase'] | null
  readonly result_reported?: boolean
  readonly events: readonly AgentEventPayload[]
  readonly pending_input?: AgentPendingInputPayload
}

export interface TaskUpdatePayload extends Record<string, unknown> {
  readonly task_id: string
  readonly phase: 'queued' | 'consulting' | 'assigned' | 'running' | 'done' | 'failed' | 'cancelled'
  readonly text: string
  readonly agent_session_id?: string
  readonly working_dir?: string
  readonly queue_position?: number
  readonly target?: string
}

export interface NoticePayload extends Record<string, unknown> {
  readonly level: 'INFO' | 'WARN' | 'ERROR'
  readonly text: string
}

export type HoloLocalBridgeState = 'not_started' | 'attach_waiting' | 'attached'

export interface HoloAddonStatePayload extends Record<string, unknown> {
  readonly local_bridge_state: HoloLocalBridgeState
  readonly current_dive_session_id: string | null
}

export interface HelloAckPayload extends Record<string, unknown> {
  readonly residents: readonly ResidentPayload[]
  readonly locations: readonly unknown[]
  readonly time_of_day: 'morning' | 'day' | 'evening' | 'night'
  readonly settings: {
    readonly audio_volume: number
  }
  readonly active_session: string | null
  readonly holo_addon: HoloAddonStatePayload
}

export function nowIsoLocal(date = new Date()): string {
  const pad = (value: number): string => String(value).padStart(2, '0')
  const offsetMinutes = -date.getTimezoneOffset()
  const sign = offsetMinutes >= 0 ? '+' : '-'
  const absoluteOffset = Math.abs(offsetMinutes)
  const offsetHours = pad(Math.floor(absoluteOffset / 60))
  const offsetRemainderMinutes = pad(absoluteOffset % 60)

  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
    `${sign}${offsetHours}:${offsetRemainderMinutes}`
  ].join('')
}

export function createProtocolMessage<TPayload extends Record<string, unknown>>(
  type: string,
  payload: TPayload,
  id?: string
): ProtocolMessage<TPayload> {
  return {
    type,
    ...(id ? { id } : {}),
    ts: nowIsoLocal(),
    payload
  }
}
