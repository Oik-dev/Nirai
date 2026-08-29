export interface SpeechTask {
  readonly requestId: string
  readonly residentName: string
  readonly text: string
  readonly audio: Uint8Array
}

export interface ResidentVoiceSettings {
  readonly speaker_uuid: string | null
  readonly style_id: number | null
  readonly speed: number
  readonly pitch: number
  readonly intonation: number
}
