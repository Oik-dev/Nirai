import { ipcRenderer } from 'electron'

export interface HoloSurfaceBounds {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

export interface HoloWebStatus {
  readonly visible: boolean
  readonly loaded: boolean
  readonly current_url: string | null
  readonly current_dive_url: string | null
  readonly current_dive_session_id: string | null
  readonly title: string | null
}

export interface HoloDiveResult extends HoloWebStatus {
  readonly bootstrap_prepared: boolean
}

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
  holo: {
    setSurface(visible: boolean, bounds?: HoloSurfaceBounds): Promise<HoloWebStatus>
    status(): Promise<HoloWebStatus>
    prepareDive(): Promise<HoloDiveResult>
    reload(): Promise<HoloWebStatus>
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
  holo: Object.freeze({
    setSurface: (visible: boolean, bounds?: HoloSurfaceBounds) => ipcRenderer.invoke('holo:surface', {
      visible,
      ...(bounds ? { bounds } : {})
    }),
    status: () => ipcRenderer.invoke('holo:status'),
    prepareDive: () => ipcRenderer.invoke('holo:prepare-dive'),
    reload: () => ipcRenderer.invoke('holo:reload')
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
