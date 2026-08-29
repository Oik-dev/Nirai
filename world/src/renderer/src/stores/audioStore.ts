import { create } from 'zustand'

interface AudioState {
  volume: number
  speakingResidentName: string | null
  activeSpeechRequestId: string | null
  voicevoxAvailable: boolean
  setVolume: (volume: number) => void
}

export function clampVolume(volume: number): number {
  return Math.min(100, Math.max(0, Math.round(volume)))
}

export const useAudioStore = create<AudioState>((set) => ({
  volume: 100,
  speakingResidentName: null,
  activeSpeechRequestId: null,
  voicevoxAvailable: false,
  setVolume: (volume) => set({ volume: clampVolume(volume) })
}))
