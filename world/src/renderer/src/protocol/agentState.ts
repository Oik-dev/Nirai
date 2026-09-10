/** Agent wire states shared by decoding, the store and execution controls. */
export const AGENT_RUN_STATE_VALUES = [
  'queued', 'starting', 'running', 'waiting_for_master', 'cancelling',
  'completed', 'failed', 'cancelled', 'interrupted'
] as const

export const AGENT_RUN_STATES: ReadonlySet<string> = new Set(AGENT_RUN_STATE_VALUES)
export const TERMINAL_AGENT_RUN_STATES: ReadonlySet<string> = new Set([
  'completed', 'failed', 'cancelled', 'interrupted'
])

export function isAgentRunState(value: unknown): value is typeof AGENT_RUN_STATE_VALUES[number] {
  return typeof value === 'string' && AGENT_RUN_STATES.has(value)
}
