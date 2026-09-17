export type HoloAutoResumeReason =
  | 'done'
  | 'failed'
  | 'cancelled'
  | 'interrupted'
  | 'waiting_for_master'
  | 'workflow_stalled'

export interface HoloAutoResumeTrigger {
  readonly kind?: 'task' | 'review'
  readonly task_id: string
  readonly agent_session_id?: string | null
  readonly reason: HoloAutoResumeReason
  readonly request_id?: string | null
  readonly request_kind?: 'approval' | 'question' | 'plan' | null
  readonly dive_session_id?: string | null
  readonly workflow_id?: string | null
  readonly conversation_url?: string | null
  // Durable delivery identity, shown as one short Resume ID line so the browser
  // can confirm this exact delivery without keeping a second receipt store.
  readonly delivery_id?: string | null
}

export type HoloAutoResumeSubmitStatus =
  | 'submitted'
  | 'busy'
  | 'draft_present'
  | 'not_ready'

export function isHoloAutoResumeTrigger(value: unknown): value is HoloAutoResumeTrigger {
  if (!value || typeof value !== 'object') return false
  const trigger = value as Partial<HoloAutoResumeTrigger>
  return (trigger.kind == null || ['task', 'review'].includes(String(trigger.kind)))
    && typeof trigger.task_id === 'string'
    && trigger.task_id.trim().length > 0
    && ['done', 'failed', 'cancelled', 'interrupted', 'waiting_for_master', 'workflow_stalled'].includes(String(trigger.reason))
    && (trigger.agent_session_id == null || typeof trigger.agent_session_id === 'string')
    && (trigger.request_id == null || typeof trigger.request_id === 'string')
    && (trigger.request_kind == null || ['approval', 'question', 'plan'].includes(String(trigger.request_kind)))
    && (trigger.dive_session_id == null || typeof trigger.dive_session_id === 'string')
    && (trigger.workflow_id == null || typeof trigger.workflow_id === 'string')
    && (trigger.delivery_id == null || (
      typeof trigger.delivery_id === 'string' && /^[A-Za-z0-9._:-]{1,128}$/.test(trigger.delivery_id)
    ))
    && (trigger.conversation_url == null || (
      typeof trigger.conversation_url === 'string' && isHoloConversationUrl(trigger.conversation_url)
    ))
}

export function holoAutoResumeTriggerKey(trigger: HoloAutoResumeTrigger): string {
  const taskId = trigger.task_id.trim()
  const agentSessionId = trigger.agent_session_id?.trim() || '-'
  const requestId = trigger.request_id?.trim() || '-'
  // Preserve legacy Task keys already persisted in the outbox/processed set.
  // Review events use their own namespace so an HR-* lifecycle can never
  // collide with a normal Task trigger.
  const prefix = trigger.kind === 'review' ? 'review:' : ''
  return `${prefix}${taskId}:${agentSessionId}:${trigger.reason}:${requestId}`
}

export function isHoloConversationUrl(value: string): boolean {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.hostname !== 'chatgpt.com') return false
    return /(?:^|\/)c\/[^/]+/.test(url.pathname)
  } catch {
    return false
  }
}

export function isSameHoloConversationUrl(left: string | null | undefined, right: string | null | undefined): boolean {
  if (!left || !right) return false
  try {
    const a = new URL(left)
    const b = new URL(right)
    if (a.protocol !== 'https:' || b.protocol !== 'https:' || a.hostname !== 'chatgpt.com' || b.hostname !== 'chatgpt.com') {
      return false
    }
    const conversationId = (url: URL): string | null => {
      const match = url.pathname.match(/(?:^|\/)c\/([^/]+)/)
      const rawId = match?.[1] ?? null
      return rawId?.startsWith('WEB:') ? rawId.slice(4) : rawId
    }
    const aId = conversationId(a)
    const bId = conversationId(b)
    return Boolean(aId && bId && aId === bId)
  } catch {
    return false
  }
}
