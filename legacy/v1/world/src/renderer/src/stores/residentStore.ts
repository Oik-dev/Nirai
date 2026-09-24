import { create } from 'zustand'
import type { BrainProviderPayload, ResidentPayload } from '../protocol/types'

interface ResidentState {
  residents: readonly ResidentPayload[]
  providerStatuses: readonly BrainProviderPayload[]
  expandedResidentName: string | null
  setResidents: (residents: readonly ResidentPayload[]) => void
  setProviderStatuses: (providers: readonly BrainProviderPayload[]) => void
  upsertResident: (resident: ResidentPayload) => void
  removeResident: (name: string) => void
  setExpandedResidentName: (name: string | null) => void
}

export const useResidentStore = create<ResidentState>((set) => ({
  residents: [],
  providerStatuses: [],
  expandedResidentName: null,
  setResidents: (residents) => set((current) => ({
    residents,
    expandedResidentName: current.expandedResidentName != null
      && residents.some((resident) => resident.name === current.expandedResidentName)
      ? current.expandedResidentName
      : null
  })),
  setProviderStatuses: (providerStatuses) => set({ providerStatuses }),
  upsertResident: (resident) => set((current) => {
    const index = current.residents.findIndex((candidate) => candidate.name === resident.name)
    if (index < 0) {
      return { residents: [...current.residents, resident] }
    }
    const residents = [...current.residents]
    residents[index] = resident
    return { residents }
  }),
  removeResident: (name) => set((current) => ({
    residents: current.residents.filter((resident) => resident.name !== name),
    expandedResidentName: current.expandedResidentName === name ? null : current.expandedResidentName
  })),
  setExpandedResidentName: (expandedResidentName) => set({ expandedResidentName })
}))
