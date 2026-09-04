import { ipcRenderer } from 'electron'

export interface HoloSurfaceBounds {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

export interface HoloAddonStatus {
  readonly phase: 'loading' | 'ready' | 'unavailable' | 'error'
  readonly visible: boolean
  readonly loaded: boolean
  readonly web_state: 'idle' | 'loading' | 'ready' | 'unavailable' | 'error'
  readonly dive_state: 'none' | 'preparing' | 'current'
  readonly current_url: string | null
  readonly current_dive_url: string | null
  readonly current_dive_session_id: string | null
  readonly title: string | null
  readonly skin_mode: 'checking' | 'applied' | 'fallback'
  readonly issue: 'web_load_failed' | 'unexpected_error' | null
  readonly persistence_issue: 'state_persistence_failed' | null
}

export interface HoloDiveResult extends HoloAddonStatus {
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
  core: {
    authSecret(): string
  }
  external: {
    open(url: string): Promise<void>
  }
  agent: {
    openFile(path: string, workingDir: string): Promise<void>
  }
  avatar: {
    pick(): Promise<string | null>
    read(relativePath: string): Promise<Uint8Array>
  }
  holo: {
    setSurface(visible: boolean, bounds?: HoloSurfaceBounds): Promise<HoloAddonStatus>
    status(): Promise<HoloAddonStatus>
    prepareDive(): Promise<HoloDiveResult>
    reload(): Promise<HoloAddonStatus>
    simulateSkinFallbackForQa(): Promise<HoloAddonStatus>
    onWebFocusChanged(listener: (focused: boolean) => void): void
    offWebFocusChanged(): void
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
  core: Object.freeze({
    authSecret: () => process.env.NIRAI_WORLD_SECRET ?? ''
  }),
  external: Object.freeze({
    open: (url: string) => ipcRenderer.invoke('external:open', url)
  }),
  agent: Object.freeze({
    openFile: (path: string, workingDir: string) => ipcRenderer.invoke('agent:open-file', path, workingDir)
  }),
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
    reload: () => ipcRenderer.invoke('holo:reload'),
    simulateSkinFallbackForQa: () => ipcRenderer.invoke('holo:skin-fallback-qa'),
    // The single Holo Whisper Surface is the only consumer of this signal.
    onWebFocusChanged: (listener: (focused: boolean) => void) => {
      ipcRenderer.removeAllListeners('holo:web-focus-changed')
      ipcRenderer.on('holo:web-focus-changed', (_event, focused: boolean) => listener(focused === true))
    },
    offWebFocusChanged: () => {
      ipcRenderer.removeAllListeners('holo:web-focus-changed')
    }
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
