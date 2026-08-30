import type {
  ActionPayload,
  BrainProviderPayload,
  ChatEntryPayload,
  ChatSessionSummaryPayload,
  HelloAckPayload,
  NoticePayload,
  ProtocolMessage,
  ResidentPayload,
  ResponseStatePayload
} from './types'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function parseProtocolMessage(raw: unknown): ProtocolMessage | null {
  if (typeof raw !== 'string') {
    return null
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }

  if (!isRecord(parsed)) return null
  if (typeof parsed.type !== 'string' || !parsed.type) return null
  if (typeof parsed.ts !== 'string' || !parsed.ts) return null
  if (!isRecord(parsed.payload)) return null
  if ('id' in parsed && typeof parsed.id !== 'string') return null

  return parsed as unknown as ProtocolMessage
}

function isChatEntry(value: unknown): value is ChatEntryPayload {
  if (!isRecord(value)) return false
  return typeof value.ts === 'string'
    && typeof value.kind === 'string'
    && typeof value.from === 'string'
    && typeof value.text === 'string'
    && typeof value.session === 'string'
    && (value.request_id === undefined || typeof value.request_id === 'string')
    && (value.to === undefined || typeof value.to === 'string')
}

function isSessionSummary(value: unknown): value is ChatSessionSummaryPayload {
  if (!isRecord(value)) return false
  return typeof value.id === 'string'
    && typeof value.title === 'string'
    && typeof value.created_at === 'string'
    && typeof value.updated_at === 'string'
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
}

function isNullableNumber(value: unknown): value is number | null {
  return typeof value === 'number' || value === null
}

function isBrainProvider(value: unknown): value is BrainProviderPayload {
  if (!isRecord(value)) return false
  return typeof value.name === 'string'
    && typeof value.display_name === 'string'
    && typeof value.available === 'boolean'
    && typeof value.connected === 'boolean'
    && typeof value.configuration_mode === 'string'
    && Array.isArray(value.models)
    && value.models.every((model) => isRecord(model)
      && typeof model.id === 'string'
      && typeof model.display_name === 'string'
      && (model.default_reasoning_effort === undefined || isNullableString(model.default_reasoning_effort))
      && (model.reasoning_efforts === undefined || (
        Array.isArray(model.reasoning_efforts)
        && model.reasoning_efforts.every((effort) => isRecord(effort)
          && typeof effort.id === 'string'
          && typeof effort.display_name === 'string')
      )))
    && (typeof value.default_model === 'string' || value.default_model === null)
    && isNullableString(value.default_reasoning_effort)
    && typeof value.custom_model_allowed === 'boolean'
}

function isResident(value: unknown): value is ResidentPayload {
  if (!isRecord(value) || !isRecord(value.tts)) return false
  return typeof value.name === 'string'
    && isNullableString(value.brain)
    && isNullableString(value.brain_model)
    && isNullableString(value.brain_reasoning_effort)
    && isNullableString(value.avatar)
    && typeof value.location === 'string'
    && typeof value.tts.enabled === 'boolean'
    && typeof value.tts.provider === 'string'
    && isNullableString(value.tts.speaker_uuid)
    && isNullableNumber(value.tts.style_id)
    && typeof value.tts.speed === 'number'
    && typeof value.tts.pitch === 'number'
    && typeof value.tts.intonation === 'number'
}

export function isActionMessage(
  message: ProtocolMessage
): message is ProtocolMessage<ActionPayload> {
  return message.type === 'action'
    && typeof message.id === 'string'
    && typeof message.payload.name === 'string'
    && typeof message.payload.command === 'string'
    && isRecord(message.payload.args)
}

export function isChatAppendMessage(message: ProtocolMessage): boolean {
  return message.type === 'chat_append' && isChatEntry(message.payload.entry)
}

export function isHistoryResponseMessage(message: ProtocolMessage): boolean {
  return message.type === 'history_response'
    && typeof message.payload.session_id === 'string'
    && Array.isArray(message.payload.entries)
    && message.payload.entries.every(isChatEntry)
    && (typeof message.payload.next_before === 'string' || message.payload.next_before === null)
}

export function isChatSessionListMessage(message: ProtocolMessage): boolean {
  return message.type === 'chat_session_list'
    && Array.isArray(message.payload.sessions)
    && message.payload.sessions.every(isSessionSummary)
    && (typeof message.payload.active_session === 'string' || message.payload.active_session === null)
}

export function isBrainProviderListMessage(message: ProtocolMessage): boolean {
  return message.type === 'brain_provider_list'
    && Array.isArray(message.payload.providers)
    && message.payload.providers.every(isBrainProvider)
}

export function isNoticeMessage(
  message: ProtocolMessage
): message is ProtocolMessage<NoticePayload> {
  if (message.type !== 'notice') return false
  return ['INFO', 'WARN', 'ERROR'].includes(String(message.payload.level))
    && typeof message.payload.text === 'string'
}

export function isResidentRosterUpdatedMessage(message: ProtocolMessage): boolean {
  return message.type === 'resident_roster_updated'
    && Array.isArray(message.payload.residents)
    && message.payload.residents.every(isResident)
}

export function isResidentSettingsUpdatedMessage(message: ProtocolMessage): boolean {
  if (message.type !== 'resident_settings_updated') return false
  if (isResident(message.payload.resident)) return true
  return message.payload.resident === null && typeof message.payload.deleted_name === 'string'
}

export function isResponseStateMessage(
  message: ProtocolMessage
): message is ProtocolMessage<ResponseStatePayload> {
  if (message.type !== 'response_state') return false
  const payload = message.payload
  return typeof payload.active === 'boolean'
    && (payload.request_id === undefined || typeof payload.request_id === 'string')
    && (payload.session_id === undefined || typeof payload.session_id === 'string')
}

export function isHelloAckMessage(
  message: ProtocolMessage
): message is ProtocolMessage<HelloAckPayload> {
  if (message.type !== 'hello_ack') return false
  const payload = message.payload
  const settings = payload.settings

  return Array.isArray(payload.residents)
    && payload.residents.every(isResident)
    && Array.isArray(payload.locations)
    && ['morning', 'day', 'evening', 'night'].includes(String(payload.time_of_day))
    && isRecord(settings)
    && typeof settings.audio_volume === 'number'
    && (typeof payload.active_session === 'string' || payload.active_session === null)
}
