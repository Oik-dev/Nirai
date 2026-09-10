import { useMemo } from 'react'
import { useAgentStore } from '../stores/agentStore'
import type { AgentTaskPanelProps } from './agentPresentation'
import { canCancelAgentSession, findFileChangeApprovalContext, stateLabel, eventTitle } from './agentPresentation'
import { AgentMarkdown } from './AgentMarkdown'
import { EventBody } from './AgentEventBody'
import { PendingApproval, PendingQuestion, PendingPlan } from './AgentPendingInput'

// Preserve the panel's existing public helpers for callers and regression tests.
export {
  canCancelAgentSession, approvalOptionIsSupported, questionAllowsMultiple,
  questionAllowsFreeText, findFileChangeApprovalContext, canApprovePendingInput
} from './agentPresentation'
export { AgentMarkdown, safeHttpUrl, parseAgentFileReference } from './AgentMarkdown'
export type { AgentFileReference } from './AgentMarkdown'
export { CollapsedText } from './AgentEventBody'

export function AgentTaskPanel({ onApproval, onQuestion, onPlan, onCancel, onRecover }: AgentTaskPanelProps): JSX.Element | null {
  const sessions = useAgentStore((state) => state.sessions)
  const order = useAgentStore((state) => state.order)
  const activeSessionId = useAgentStore((state) => state.activeSessionId)
  const setActiveSession = useAgentStore((state) => state.setActiveSession)
  const session = activeSessionId ? sessions[activeSessionId] : null

  const visibleEvents = useMemo(() => {
    if (!session) return []
    const lastTodoSeq = [...session.events].reverse().find((event) => event.type === 'todo_update')?.seq ?? null
    return session.events.filter((event) => {
      if (event.type === 'run_state') return false
      if (event.type === 'todo_update' && event.seq !== lastTodoSeq) return false
      return true
    }).slice(-80)
  }, [session])

  const approvalContextEvent = useMemo(() => (
    session ? findFileChangeApprovalContext(session.events, session.pendingInput) : null
  ), [session])

  if (!session) return null

  return (
    <aside className="agent-task-panel" aria-label="Agent作業監督">
      <header className="agent-task-header">
        <div>
          <strong>{session.resident} · Agent Work</strong>
          <small>{session.taskText ?? session.taskId}</small>
        </div>
        <span className={`agent-run-state is-${session.state}`}>{stateLabel(session.state)}</span>
        {canCancelAgentSession(session.state) && (
          <button type="button" className="agent-cancel-button" onClick={() => onCancel(session.agentSessionId)}>停止</button>
        )}
      </header>

      {order.length > 1 && (
        <nav className="agent-session-tabs" aria-label="Agent Session">
          {order.slice(0, 6).map((agentSessionId) => {
            const candidate = sessions[agentSessionId]
            if (!candidate) return null
            return (
              <button
                type="button"
                key={agentSessionId}
                aria-pressed={agentSessionId === session.agentSessionId}
                onClick={() => setActiveSession(agentSessionId)}
              >
                {candidate.resident} · {stateLabel(candidate.state)}
              </button>
            )
          })}
        </nav>
      )}

      {session.state === 'interrupted' && session.recoveryOptions.length > 0 && (
        <section className="agent-master-card" aria-label="Agent中断復旧">
          <header><strong>中断した作業</strong><span>Recovery</span></header>
          <p>Core再起動などで作業が中断されています。自動では再開しません。</p>
          <div className="agent-master-actions">
            {session.recoveryOptions.includes('resume') && (
              <button type="button" onClick={() => onRecover(session.agentSessionId, 'resume')}>続きから再開</button>
            )}
            {session.recoveryOptions.includes('rerun') && (
              <button type="button" onClick={() => onRecover(session.agentSessionId, 'rerun')}>最初から再実行</button>
            )}
            {session.recoveryOptions.includes('abandon') && (
              <button type="button" className="is-danger" onClick={() => onRecover(session.agentSessionId, 'abandon')}>破棄</button>
            )}
          </div>
        </section>
      )}

      {session.state === 'waiting_for_master' && session.pendingInput?.type === 'approval_request' && (
        <PendingApproval
          agentSessionId={session.agentSessionId}
          pending={session.pendingInput}
          contextEvent={approvalContextEvent}
          onApproval={onApproval}
        />
      )}
      {session.state === 'waiting_for_master' && session.pendingInput?.type === 'question_request' && (
        <PendingQuestion agentSessionId={session.agentSessionId} pending={session.pendingInput} onQuestion={onQuestion} />
      )}
      {session.state === 'waiting_for_master' && session.pendingInput?.type === 'plan' && (
        <PendingPlan agentSessionId={session.agentSessionId} pending={session.pendingInput} onPlan={onPlan} />
      )}

      <div className="agent-event-feed">
        {visibleEvents.map((event) => (
          <article className={`agent-event-card is-${event.type}`} key={event.event_id}>
            <header>
              <strong>{eventTitle(event)}</strong>
              <small>{new Date(event.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</small>
            </header>
            <EventBody event={event} agentSessionId={session.agentSessionId} />
          </article>
        ))}
        {session.finalSummary && (
          <article className="agent-event-card is-summary">
            <header><strong>Completed</strong></header>
            <AgentMarkdown text={session.finalSummary} agentSessionId={session.agentSessionId} />
          </article>
        )}
      </div>
    </aside>
  )
}
