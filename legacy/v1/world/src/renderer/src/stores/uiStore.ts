import { create } from 'zustand'

export type RightSidebarMode = 'resident' | 'debug'

interface UiState {
  chatActive: boolean
  historyOpaque: boolean
  leftSidebarOpen: boolean
  rightSidebarOpen: boolean
  rightSidebarMode: RightSidebarMode
  voicePanelResidentName: string | null
  setVoicePanelResidentName: (name: string | null) => void
  setChatActive: (active: boolean) => void
  setHistoryOpaque: (opaque: boolean) => void
  toggleLeftSidebar: () => void
  openRightSidebar: (mode: RightSidebarMode) => void
  closeRightSidebar: () => void
  closeSidebars: () => void
}

export const useUiStore = create<UiState>((set) => ({
  chatActive: false,
  historyOpaque: false,
  leftSidebarOpen: false,
  rightSidebarOpen: false,
  rightSidebarMode: 'resident',
  voicePanelResidentName: null,
  setVoicePanelResidentName: (voicePanelResidentName) => set({ voicePanelResidentName }),
  setChatActive: (chatActive) => set({ chatActive }),
  setHistoryOpaque: (historyOpaque) => set({ historyOpaque }),
  toggleLeftSidebar: () => set((state) => ({ leftSidebarOpen: !state.leftSidebarOpen })),
  openRightSidebar: (rightSidebarMode) => set({ rightSidebarOpen: true, rightSidebarMode }),
  closeRightSidebar: () => set({ rightSidebarOpen: false, voicePanelResidentName: null }),
  closeSidebars: () => set({ leftSidebarOpen: false, rightSidebarOpen: false, voicePanelResidentName: null })
}))
