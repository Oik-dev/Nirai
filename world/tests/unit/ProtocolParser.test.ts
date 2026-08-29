import { describe, expect, it } from 'vitest'
import {
  isBrainProviderListMessage,
  isHelloAckMessage,
  isHistoryResponseMessage,
  isNoticeMessage,
  isResidentSettingsUpdatedMessage,
  isResponseStateMessage,
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
      active_session: null
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isHelloAckMessage(message)).toBe(true)
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
        configuration_mode: 'subscription-cli'
      }]
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isBrainProviderListMessage(message)).toBe(true)
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

  it('accepts resident_settings_updated deletion payload', () => {
    const raw = JSON.stringify(createProtocolMessage('resident_settings_updated', {
      resident: null,
      deleted_name: 'Lapan'
    }))

    const message = parseProtocolMessage(raw)

    expect(message).not.toBeNull()
    expect(message && isResidentSettingsUpdatedMessage(message)).toBe(true)
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
