import type { AgentEventPayload, AgentPendingInputPayload, AgentRecoveryActionPayload } from '../protocol/types'
import { TERMINAL_AGENT_RUN_STATES } from '../protocol/agentState'

export interface AgentTaskPanelProps {
  readonly onApproval: (
    agentSessionId: string,
    requestId: string,
    decision: 'approve_once' | 'approve_session' | 'reject' | 'cancel'
  ) => boolean
  readonly onQuestion: (
    agentSessionId: string,
    requestId: string,
    answers: Record<string, readonly string[]>
  ) => boolean
  readonly onPlan: (
    agentSessionId: string,
    requestId: string,
    decision: 'approve' | 'revise' | 'cancel',
    reason?: string
  ) => boolean
  readonly onCancel: (agentSessionId: string) => boolean
  readonly onRecover: (agentSessionId: string, action: AgentRecoveryActionPayload) => boolean
}

const TERMINAL_STATES = TERMINAL_AGENT_RUN_STATES

export function canCancelAgentSession(state: string): boolean {
  return state !== 'cancelling' && !TERMINAL_STATES.has(state)
}

export function canDismissAgentSession(state: string): boolean {
  return state === 'completed' || state === 'failed' || state === 'cancelled'
}

export function asString(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

export function asNumber(value: unknown): number | null {
  return typeof value === 'number' ? value : null
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

export function asRecordArray(value: unknown): readonly Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item) => asRecord(item) !== null) as Record<string, unknown>[] : []
}

export function asStringArray(value: unknown): readonly string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.length > 0) : []
}

export function approvalOptionIsSupported(value: unknown, option: string): boolean {
  // Legacy approval payloads omitted `options`; preserve that one historical
  // shape only. An explicitly present options value is a capability boundary:
  // empty, malformed, or mixed-type arrays must not broaden permissions.
  if (value === undefined) return true
  if (!Array.isArray(value) || value.length === 0) return false
  const options = asStringArray(value)
  return options.length === value.length && options.includes(option)
}

export function questionAllowsMultiple(question: Record<string, unknown>): boolean {
  return question.allow_multiple === true
}

export function questionAllowsFreeText(question: Record<string, unknown>): boolean {
  return question.allow_free_text !== false
}

export function findFileChangeApprovalContext(
  events: readonly AgentEventPayload[],
  pending: AgentPendingInputPayload | null | undefined
): AgentEventPayload | null {
  if (pending?.type !== 'approval_request' || pending.payload.kind !== 'file_change') return null
  const operationId = asString(pending.payload.operation_id)
  if (!operationId) return null
  return [...events].reverse().find((event) => (
    event.type === 'file_change' && asString(event.payload.operation_id) === operationId
  )) ?? null
}

export function stateLabel(state: string): string {
  return {
    queued: '待機',
    starting: '起動中',
    running: '作業中',
    waiting_for_master: 'Master待ち',
    cancelling: '停止中',
    completed: '完了',
    failed: '失敗',
    cancelled: '停止',
    interrupted: '中断'
  }[state] ?? state
}

export function eventTitle(event: AgentEventPayload): string {
  return {
    assistant_message: 'Agent Message',
    status_message: 'Status',
    tool_call: 'Tool Call',
    command_execution: 'Command',
    file_change: 'File Change',
    diff: 'Diff',
    approval_request: 'Approval',
    question_request: 'Question',
    plan: 'Plan',
    todo_update: 'Todo',
    subagent_update: 'Subagent',
    artifact: 'Artifact',
    run_state: 'Run State',
    error: 'Error'
  }[event.type]
}

export function canApprovePendingInput(
  pending: AgentPendingInputPayload,
  contextEvent: AgentEventPayload | null
): boolean {
  return pending.payload.kind !== 'file_change' || contextEvent !== null
}
