import type { ResidentVoiceSettings } from './types'

export class TtsService {
  async synthesize(text: string, voice: ResidentVoiceSettings): Promise<Uint8Array | null> {
    if (!text.trim() || voice.style_id === null || voice.speaker_uuid === null) {
      return null
    }

    return window.nirai.voicevox.synthesize({
      text,
      style_id: voice.style_id,
      speed: voice.speed,
      pitch: voice.pitch,
      intonation: voice.intonation
    })
  }
}
