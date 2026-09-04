import { describe, expect, it } from 'vitest'
import {
  isActionMessage,
  isAgentEventMessage,
  isAgentSessionSnapshotMessage,
  isBrainProviderListMessage,
  isHelloAckMessage,
  isHoloAddonStateMessage,
  isHistoryResponseMessage,
  isNoticeMessage,
  isResidentRosterUpdatedMessage,
  isResidentSettingsUpdatedMessage,
  isResponseStateMessage,
  isTaskUpdateMessage,
  parseProtocolMessage
} from '../../src/renderer/src/protocol/parser'
import { createProtocolMessage } from '../../src/renderer/src/protocol/types'

describe('Protocol parser', () => {
  it('accepts a valid hello_ack envelope', () => {
    const raw = JSON.stringify(createProtocolMessage('hello_ack', {
      residents: [],
      locations: [],
      time_of_day: 'day',
      settings: { audio_volume: 100 },
      active_session: null,
      holo_addon: { local_bridge_state: 'not_started', current_dive_session_id: null }
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isHelloAckMessage(message)).toBe(true)
  })

  it('accepts only allowlisted observable Holo Addon states', () => {
    const valid = parseProtocolMessage(JSON.stringify(createProtocolMessage('holo_addon_state', {
      local_bridge_state: 'attached',
      current_dive_session_id: 'DIVE-1'
    })))
    const invalid = parseProtocolMessage(JSON.stringify(createProtocolMessage('holo_addon_state', {
      local_bridge_state: 'thinking',
      current_dive_session_id: 'DIVE-1'
    })))

    expect(valid && isHoloAddonStateMessage(valid)).toBe(true)
    expect(invalid && isHoloAddonStateMessage(invalid)).toBe(false)
  })

  it('accepts history_response with an opaque older-page cursor', () => {
    const raw = JSON.stringify(createProtocolMessage('history_response', {
      session_id: 'S-1',
      entries: [],
      next_before: '25'
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isHistoryResponseMessage(message)).toBe(true)
  })

  it('accepts brain_provider_list with availability data', () => {
    const raw = JSON.stringify(createProtocolMessage('brain_provider_list', {
      providers: [{
        name: 'codex',
        display_name: 'Codex',
        available: true,
        connected: true,
        configuration_mode: 'subscription-cli',
        models: [{
          id: 'gpt-5.6-sol',
          display_name: 'GPT-5.6-Sol',
          default_reasoning_effort: 'low',
          reasoning_efforts: [
            { id: 'low', display_name: 'Low' },
            { id: 'high', display_name: 'High' }
          ]
        }],
        default_model: 'gpt-5.6-sol',
        default_reasoning_effort: 'high',
        custom_model_allowed: true,
        capabilities: {
          conversation: true,
          agent_work: true,
          approval: true,
          question: true,
          plan: true,
          todo: true,
          subagent: true,
          file_diff: true,
          command_result: true,
          artifact: true
        }
      }]
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isBrainProviderListMessage(message)).toBe(true)
  })

  it('accepts normalized Agent Event, Snapshot, and Task Update messages', () => {
    const event = {
      event_id: 'AE-AGENT-1-000001',
      seq: 1,
      ts: '2026-09-03T22:00:00+09:00',
      task_id: 'TASK-1',
      agent_session_id: 'AGENT-1',
      resident: 'Codex',
      provider: 'codex',
      type: 'question_request',
      payload: {
        request_id: 'question-1',
        questions: [{ id: 'q1', question: '続ける？', is_secret: true }]
      }
    }
    const agentMessage = parseProtocolMessage(JSON.stringify(createProtocolMessage('agent_event', { event })))
    const snapshotMessage = parseProtocolMessage(JSON.stringify(createProtocolMessage('agent_session_snapshot', {
      agent_session_id: 'AGENT-1',
      task_id: 'TASK-1',
      resident: 'Codex',
      provider: 'codex',
      state: 'waiting_for_master',
      working_dir: 'D:/workspace/TASK-1',
      started_at: '2026-09-03T22:00:00+09:00',
      updated_at: '2026-09-03T22:00:01+09:00',
      last_event_seq: 1,
      final_summary: null,
      events: [event],
      pending_input: {
        type: 'question_request',
        request_id: 'question-1',
        payload: event.payload
      }
    })))
    const taskMessage = parseProtocolMessage(JSON.stringify(createProtocolMessage('task_update', {
      task_id: 'TASK-1',
      phase: 'assigned',
      text: 'Codexへ作業を依頼しました',
      agent_session_id: 'AGENT-1'
    })))

    expect(agentMessage && isAgentEventMessage(agentMessage)).toBe(true)
    expect(snapshotMessage && isAgentSessionSnapshotMessage(snapshotMessage)).toBe(true)
    expect(taskMessage && isTaskUpdateMessage(taskMessage)).toBe(true)
  })

  it('rejects Provider-native Agent fields masquerading as malformed shared events', () => {
    const raw = JSON.stringify(createProtocolMessage('agent_event', {
      event: {
        event_id: 'AE-1',
        seq: 1,
        ts: '2026-09-03T22:00:00+09:00',
        task_id: 'TASK-1',
        agent_session_id: 'AGENT-1',
        resident: 'Codex',
        provider: 'codex',
        type: 'provider/raw/event',
        payload: { threadId: 'provider-thread' }
      }
    }))

    const message = parseProtocolMessage(raw)
    expect(message && isAgentEventMessage(message)).toBe(false)
  })

  it('accepts notice messages used for recoverable Core errors', () => {
    const raw = JSON.stringify(createProtocolMessage('notice', {
      level: 'WARN',
      text: 'Resident作成にはAI選択が必要です'
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isNoticeMessage(message)).toBe(true)
  })

  it('accepts resident_settings_updated with real resident data', () => {
    const raw = JSON.stringify(createProtocolMessage('resident_settings_updated', {
      resident: {
        name: 'Lapan',
        brain: 'codex',
        brain_model: null,
        brain_reasoning_effort: null,
        avatar: 'lapan/lapan.vrm',
        location: 'center',
        tts: {
          enabled: true,
          provider: 'voicevox',
          speaker_uuid: null,
          style_id: null,
          speed: 1,
          pitch: 0,
          intonation: 1
        }
      }
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isResidentSettingsUpdatedMessage(message)).toBe(true)
  })

  it('accepts resident_roster_updated after drag reorder', () => {
    const raw = JSON.stringify(createProtocolMessage('resident_roster_updated', {
      residents: [{
        name: 'Codex',
        brain: 'codex',
        brain_model: 'gpt-5.6-sol',
        brain_reasoning_effort: null,
        avatar: 'lapan/lapan.vrm',
        location: 'center',
        tts: {
          enabled: true,
          provider: 'voicevox',
          speaker_uuid: null,
          style_id: null,
          speed: 1,
          pitch: 0,
          intonation: 1
        }
      }]
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isResidentRosterUpdatedMessage(message)).toBe(true)
  })

  it('accepts resident_settings_updated deletion payload', () => {
    const raw = JSON.stringify(createProtocolMessage('resident_settings_updated', {
      resident: null,
      deleted_name: 'Lapan'
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isResidentSettingsUpdatedMessage(message)).toBe(true)
  })

  it('accepts action messages with correlation id', () => {
    const raw = JSON.stringify(createProtocolMessage('action', {
      name: 'Lapan',
      command: 'approach',
      args: { target: 'Kina' }
    }, 'ACT-1'))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isActionMessage(message)).toBe(true)
  })

  it('accepts response_state with request_id', () => {
    const raw = JSON.stringify(createProtocolMessage('response_state', {
      active: true,
      request_id: 'REQ-1',
      session_id: 'S-1'
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isResponseStateMessage(message)).toBe(true)
  })

  it('rejects malformed and incomplete messages without throwing', () => {
    expect(parseProtocolMessage('not-json')).toBeNull()
    expect(parseProtocolMessage(JSON.stringify({ type: 'hello_ack', payload: {} }))).toBeNull()
  })
})
