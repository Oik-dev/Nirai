import { create } from 'zustand'
import type {
  AgentEventPayload,
  AgentPendingInputPayload,
  AgentRunStatePayload,
  AgentSessionSnapshotPayload,
  TaskUpdatePayload
} from '../protocol/types'

export interface AgentSessionView {
  readonly agentSessionId: string
  readonly taskId: string
  readonly resident: string
  readonly provider: string
  readonly state: AgentRunStatePayload
  readonly workingDir: string
  readonly startedAt: string
  readonly updatedAt: string
  readonly finalSummary: string | null
  readonly events: readonly AgentEventPayload[]
  readonly pendingInput: AgentPendingInputPayload | null
  readonly taskText: string | null
  readonly taskPhase: TaskUpdatePayload['phase'] | null
}

interface AgentStoreState {
  readonly sessions: Readonly<Record<string, AgentSessionView>>
  readonly order: readonly string[]
  readonly activeSessionId: string | null
  readonly appendEvent: (event: AgentEventPayload) => void
  readonly applySnapshot: (snapshot: AgentSessionSnapshotPayload) => void
  readonly applyTaskUpdate: (update: TaskUpdatePayload) => void
  readonly setActiveSession: (agentSessionId: string | null) => void
}

function eventPendingInput(event: AgentEventPayload): AgentPendingInputPayload | null {
  if (event.type === 'approval_request' || event.type === 'question_request') {
    const requestId = event.payload.request_id
    if (typeof requestId !== 'string' || !requestId) return null
    return {
      type: event.type,
      request_id: requestId,
      payload: event.payload
    }
  }
  if (event.type === 'plan' && event.payload.approval_required === true) {
    const requestId = event.payload.request_id
    if (typeof requestId !== 'string' || !requestId) return null
    return {
      type: 'plan',
      request_id: requestId,
      payload: event.payload
    }
  }
  return null
}

function nextStateFromEvent(current: AgentRunStatePayload, event: AgentEventPayload): AgentRunStatePayload {
  if (event.type !== 'run_state') return current
  const value = event.payload.state
  if (
    value === 'queued'
    || value === 'starting'
    || value === 'running'
    || value === 'waiting_for_master'
    || value === 'cancelling'
    || value === 'completed'
    || value === 'failed'
    || value === 'cancelled'
    || value === 'interrupted'
  ) return value
  return current
}

function sessionFromEvent(event: AgentEventPayload): AgentSessionView {
  const state = nextStateFromEvent('starting', event)
  return {
    agentSessionId: event.agent_session_id,
    taskId: event.task_id,
    resident: event.resident,
    provider: event.provider,
    state,
    workingDir: '',
    startedAt: event.ts,
    updatedAt: event.ts,
    finalSummary: null,
    events: [event],
    pendingInput: eventPendingInput(event),
    taskText: null,
    taskPhase: null
  }
}

function moveToFront(order: readonly string[], agentSessionId: string): readonly string[] {
  return [agentSessionId, ...order.filter((value) => value !== agentSessionId)]
}

function lastEventSeq(session: AgentSessionView): number {
  return session.events.reduce((highest, event) => Math.max(highest, event.seq), 0)
}

export const useAgentStore = create<AgentStoreState>((set) => ({
  sessions: {},
  order: [],
  activeSessionId: null,
  appendEvent: (event) => set((state) => {
    const current = state.sessions[event.agent_session_id]
    if (!current) {
      const created = sessionFromEvent(event)
      return {
        sessions: { ...state.sessions, [event.agent_session_id]: created },
        order: moveToFront(state.order, event.agent_session_id),
        activeSessionId: event.agent_session_id
      }
    }
    if (
      event.seq <= lastEventSeq(current)
      || current.events.some((candidate) => candidate.event_id === event.event_id)
    ) return state

    const nextState = nextStateFromEvent(current.state, event)
    const pendingFromEvent = eventPendingInput(event)
    const shouldClearPending = event.type === 'run_state'
      && nextState !== 'waiting_for_master'
    const next: AgentSessionView = {
      ...current,
      state: nextState,
      updatedAt: event.ts,
      events: [...current.events, event].sort((left, right) => left.seq - right.seq),
      pendingInput: pendingFromEvent ?? (shouldClearPending ? null : current.pendingInput)
    }
    return {
      sessions: { ...state.sessions, [event.agent_session_id]: next },
      order: moveToFront(state.order, event.agent_session_id),
      activeSessionId: state.activeSessionId ?? event.agent_session_id
    }
  }),
  applySnapshot: (snapshot) => set((state) => {
    const current = state.sessions[snapshot.agent_session_id]
    if (current && snapshot.last_event_seq < lastEventSeq(current)) return state
    const next: AgentSessionView = {
      agentSessionId: snapshot.agent_session_id,
      taskId: snapshot.task_id,
      resident: snapshot.resident,
      provider: snapshot.provider,
      state: snapshot.state,
      workingDir: snapshot.working_dir,
      startedAt: snapshot.started_at,
      updatedAt: snapshot.updated_at,
      finalSummary: snapshot.final_summary,
      events: [...snapshot.events].sort((left, right) => left.seq - right.seq),
      pendingInput: snapshot.pending_input ?? null,
      taskText: current?.taskText ?? null,
      taskPhase: current?.taskPhase ?? null
    }
    return {
      sessions: { ...state.sessions, [snapshot.agent_session_id]: next },
      order: moveToFront(state.order, snapshot.agent_session_id),
      activeSessionId: state.activeSessionId ?? snapshot.agent_session_id
    }
  }),
  applyTaskUpdate: (update) => set((state) => {
    const agentSessionId = update.agent_session_id
    if (!agentSessionId) return state
    const current = state.sessions[agentSessionId]
    if (!current) return state
    return {
      sessions: {
        ...state.sessions,
        [agentSessionId]: {
          ...current,
          workingDir: typeof update.working_dir === 'string' && update.working_dir
            ? update.working_dir
            : current.workingDir,
          taskText: update.text,
          taskPhase: update.phase
        }
      },
      order: moveToFront(state.order, agentSessionId),
      activeSessionId: state.activeSessionId ?? agentSessionId
    }
  }),
  setActiveSession: (activeSessionId) => set({ activeSessionId })
}))
