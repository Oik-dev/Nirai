import { ipcMain } from 'electron'

const DEFAULT_VOICEVOX_URL = 'http://127.0.0.1:50021'
const HEALTH_TIMEOUT_MS = 5_000
const SPEAKERS_TIMEOUT_MS = 5_000
const AUDIO_QUERY_TIMEOUT_MS = 10_000
const SYNTHESIS_TIMEOUT_MS = 30_000

export interface VoicevoxStyle {
  readonly name: string
  readonly id: number
}

export interface VoicevoxSpeaker {
  readonly name: string
  readonly speaker_uuid: string
  readonly styles: readonly VoicevoxStyle[]
}

export interface VoicevoxSynthesisRequest {
  readonly text: string
  readonly style_id: number
  readonly speed: number
  readonly pitch: number
  readonly intonation: number
}

function getVoicevoxUrl(): string {
  return process.env.NIRAI_VOICEVOX_URL?.trim() || DEFAULT_VOICEVOX_URL
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number
): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

function ensureOk(response: Response, operation: string): Response {
  if (!response.ok) {
    throw new Error(`VOICEVOX ${operation} failed: HTTP ${response.status}`)
  }
  return response
}

function parseSpeakers(value: unknown): VoicevoxSpeaker[] {
  if (!Array.isArray(value)) {
    throw new Error('VOICEVOX speakers response is invalid')
  }

  return value.map((speaker) => {
    if (typeof speaker !== 'object' || speaker === null || Array.isArray(speaker)) {
      throw new Error('VOICEVOX speaker entry is invalid')
    }
    const raw = speaker as Record<string, unknown>
    if (typeof raw.name !== 'string' || typeof raw.speaker_uuid !== 'string' || !Array.isArray(raw.styles)) {
      throw new Error('VOICEVOX speaker entry is incomplete')
    }
    const styles = raw.styles.map((style) => {
      if (typeof style !== 'object' || style === null || Array.isArray(style)) {
        throw new Error('VOICEVOX style entry is invalid')
      }
      const rawStyle = style as Record<string, unknown>
      if (typeof rawStyle.name !== 'string' || typeof rawStyle.id !== 'number') {
        throw new Error('VOICEVOX style entry is incomplete')
      }
      return { name: rawStyle.name, id: rawStyle.id }
    })
    return { name: raw.name, speaker_uuid: raw.speaker_uuid, styles }
  })
}

function validateSynthesisRequest(value: unknown): VoicevoxSynthesisRequest {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new TypeError('VOICEVOX synthesis request must be an object')
  }
  const request = value as Record<string, unknown>
  if (typeof request.text !== 'string' || !request.text.trim()) {
    throw new TypeError('VOICEVOX synthesis text is required')
  }
  if (!Number.isInteger(request.style_id)) {
    throw new TypeError('VOICEVOX style_id must be an integer')
  }
  for (const key of ['speed', 'pitch', 'intonation'] as const) {
    if (typeof request[key] !== 'number' || !Number.isFinite(request[key])) {
      throw new TypeError(`VOICEVOX ${key} must be a finite number`)
    }
  }
  return request as unknown as VoicevoxSynthesisRequest
}

export function registerVoicevoxIpc(): void {
  ipcMain.handle('voicevox:health', async () => {
    try {
      const response = await fetchWithTimeout(`${getVoicevoxUrl()}/version`, { method: 'GET' }, HEALTH_TIMEOUT_MS)
      return response.ok
    } catch {
      return false
    }
  })

  ipcMain.handle('voicevox:speakers', async () => {
    const response = ensureOk(
      await fetchWithTimeout(`${getVoicevoxUrl()}/speakers`, { method: 'GET' }, SPEAKERS_TIMEOUT_MS),
      'speakers'
    )
    return parseSpeakers(await response.json())
  })

  ipcMain.handle('voicevox:synthesize', async (_event, rawRequest: unknown) => {
    const request = validateSynthesisRequest(rawRequest)
    const queryUrl = new URL(`${getVoicevoxUrl()}/audio_query`)
    queryUrl.searchParams.set('text', request.text)
    queryUrl.searchParams.set('speaker', String(request.style_id))
    const queryResponse = ensureOk(
      await fetchWithTimeout(queryUrl.toString(), { method: 'POST' }, AUDIO_QUERY_TIMEOUT_MS),
      'audio_query'
    )
    const audioQuery = await queryResponse.json() as Record<string, unknown>
    audioQuery.speedScale = request.speed
    audioQuery.pitchScale = request.pitch
    audioQuery.intonationScale = request.intonation

    const synthesisUrl = new URL(`${getVoicevoxUrl()}/synthesis`)
    synthesisUrl.searchParams.set('speaker', String(request.style_id))
    const synthesisResponse = ensureOk(
      await fetchWithTimeout(
        synthesisUrl.toString(),
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(audioQuery)
        },
        SYNTHESIS_TIMEOUT_MS
      ),
      'synthesis'
    )
    return new Uint8Array(await synthesisResponse.arrayBuffer())
  })
}
