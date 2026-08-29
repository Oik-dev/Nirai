import { ipcRenderer } from 'electron'

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

export interface NiraiApi {
  avatar: {
    pick(): Promise<string | null>
    read(relativePath: string): Promise<Uint8Array>
  }
  persona: {
    open(residentName: string): Promise<void>
  }
  voicevox: {
    health(): Promise<boolean>
    speakers(): Promise<VoicevoxSpeaker[]>
    synthesize(request: VoicevoxSynthesisRequest): Promise<Uint8Array>
  }
}

export const niraiApi: NiraiApi = Object.freeze({
  avatar: Object.freeze({
    pick: () => ipcRenderer.invoke('avatar:pick'),
    read: (relativePath: string) => ipcRenderer.invoke('avatar:read', relativePath)
  }),
  persona: Object.freeze({
    open: (residentName: string) => ipcRenderer.invoke('persona:open', residentName)
  }),
  voicevox: Object.freeze({
    health: () => ipcRenderer.invoke('voicevox:health'),
    speakers: () => ipcRenderer.invoke('voicevox:speakers'),
    synthesize: (request: VoicevoxSynthesisRequest) => ipcRenderer.invoke('voicevox:synthesize', request)
  })
})
