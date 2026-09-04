import { beforeEach, describe, expect, it } from 'vitest'
import type { AgentEventPayload, AgentSessionSnapshotPayload } from '../../src/renderer/src/protocol/types'
import { useAgentStore } from '../../src/renderer/src/stores/agentStore'

function event(
  seq: number,
  type: AgentEventPayload['type'],
  payload: Record<string, unknown>
): AgentEventPayload {
  return {
    event_id: `AE-AGENT-1-${String(seq).padStart(6, '0')}`,
    seq,
    ts: `2026-09-03T22:00:0${seq}+09:00`,
    task_id: 'TASK-1',
    agent_session_id: 'AGENT-1',
    resident: 'Codex',
    provider: 'codex',
    type,
    payload
  }
}

beforeEach(() => {
  useAgentStore.setState({ sessions: {}, order: [], activeSessionId: null })
})

describe('AgentStore', () => {
  it('keeps Agent state separate and clears pending input when work resumes', () => {
    const store = useAgentStore.getState()

    store.appendEvent(event(1, 'run_state', { state: 'running' }))
    store.appendEvent(event(2, 'approval_request', {
      request_id: 'approval-1',
      title: 'Command execution requires approval',
      command: 'npm test'
    }))
    store.appendEvent(event(3, 'run_state', { state: 'waiting_for_master' }))

    let session = useAgentStore.getState().sessions['AGENT-1']
    expect(session.state).toBe('waiting_for_master')
    expect(session.pendingInput?.request_id).toBe('approval-1')

    useAgentStore.getState().appendEvent(event(4, 'run_state', { state: 'running' }))
    session = useAgentStore.getState().sessions['AGENT-1']

    expect(session.state).toBe('running')
    expect(session.pendingInput).toBeNull()
    expect(session.events.map((value) => value.seq)).toEqual([1, 2, 3, 4])
  })

  it('hydrates the live working directory from task updates before reconnect', () => {
    useAgentStore.getState().appendEvent(event(1, 'run_state', { state: 'starting' }))
    useAgentStore.getState().applyTaskUpdate({
      task_id: 'TASK-1',
      phase: 'assigned',
      text: '安全なSmoke Testを実行',
      agent_session_id: 'AGENT-1',
      working_dir: 'D:/Products/Nirai/runtime/workspace/TASK-1'
    })

    const session = useAgentStore.getState().sessions['AGENT-1']
    expect(session.workingDir).toBe('D:/Products/Nirai/runtime/workspace/TASK-1')
    expect(session.taskPhase).toBe('assigned')
  })

  it('restores a reconnect snapshot and preserves task metadata', () => {
    useAgentStore.getState().appendEvent(event(1, 'run_state', { state: 'starting' }))
    useAgentStore.getState().applyTaskUpdate({
      task_id: 'TASK-1',
      phase: 'assigned',
      text: '安全なSmoke Testを実行',
      agent_session_id: 'AGENT-1',
      working_dir: 'D:/Products/Nirai/runtime/workspace/TASK-1'
    })

    const snapshot: AgentSessionSnapshotPayload = {
      agent_session_id: 'AGENT-1',
      task_id: 'TASK-1',
      resident: 'Codex',
      provider: 'codex',
      state: 'waiting_for_master',
      working_dir: 'D:/Products/Nirai/runtime/workspace/TASK-1',
      started_at: '2026-09-03T22:00:00+09:00',
      updated_at: '2026-09-03T22:00:05+09:00',
      last_event_seq: 2,
      final_summary: null,
      events: [
        event(1, 'run_state', { state: 'running' }),
        event(2, 'question_request', {
          request_id: 'question-1',
          questions: [{ id: 'q1', question: '続ける？', is_secret: true }]
        })
      ],
      pending_input: {
        type: 'question_request',
        request_id: 'question-1',
        payload: {
          request_id: 'question-1',
          questions: [{ id: 'q1', question: '続ける？', is_secret: true }]
        }
      }
    }

    useAgentStore.getState().applySnapshot(snapshot)
    const restored = useAgentStore.getState().sessions['AGENT-1']

    expect(restored.state).toBe('waiting_for_master')
    expect(restored.pendingInput?.type).toBe('question_request')
    expect(restored.taskText).toBe('安全なSmoke Testを実行')
    expect(restored.workingDir).toContain('runtime/workspace/TASK-1')
  })

  it('ignores a reconnect snapshot older than an already applied live event', () => {
    useAgentStore.getState().appendEvent(event(1, 'run_state', { state: 'running' }))
    useAgentStore.getState().appendEvent(event(2, 'approval_request', {
      request_id: 'approval-1',
      title: 'approval'
    }))
    useAgentStore.getState().appendEvent(event(3, 'run_state', { state: 'running' }))

    const staleSnapshot: AgentSessionSnapshotPayload = {
      agent_session_id: 'AGENT-1',
      task_id: 'TASK-1',
      resident: 'Codex',
      provider: 'codex',
      state: 'waiting_for_master',
      working_dir: 'D:/Products/Nirai/runtime/workspace/TASK-1',
      started_at: '2026-09-03T22:00:00+09:00',
      updated_at: '2026-09-03T22:00:02+09:00',
      last_event_seq: 2,
      final_summary: null,
      events: [
        event(1, 'run_state', { state: 'running' }),
        event(2, 'approval_request', { request_id: 'approval-1', title: 'approval' })
      ],
      pending_input: {
        type: 'approval_request',
        request_id: 'approval-1',
        payload: { request_id: 'approval-1', title: 'approval' }
      }
    }

    useAgentStore.getState().applySnapshot(staleSnapshot)
    const session = useAgentStore.getState().sessions['AGENT-1']
    expect(session.state).toBe('running')
    expect(session.pendingInput).toBeNull()
    expect(session.events.map((value) => value.seq)).toEqual([1, 2, 3])
  })

  it('deduplicates replayed Agent Events by event_id', () => {
    const first = event(1, 'status_message', { text: 'starting' })
    useAgentStore.getState().appendEvent(first)
    useAgentStore.getState().appendEvent(first)

    expect(useAgentStore.getState().sessions['AGENT-1'].events).toHaveLength(1)
  })
})
