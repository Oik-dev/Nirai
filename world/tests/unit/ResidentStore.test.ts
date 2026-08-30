import { beforeEach, describe, expect, it } from 'vitest'
import { useResidentStore } from '../../src/renderer/src/stores/residentStore'
import type { ResidentPayload } from '../../src/renderer/src/protocol/types'

function resident(name: string, brain: string | null = null): ResidentPayload {
  return {
    name,
    brain,
    brain_model: null,
    brain_reasoning_effort: null,
    avatar: null,
    location: 'center',
    tts: {
      enabled: true,
      provider: 'voicevox',
      speaker_uuid: null,
      style_id: null,
      speed: 1,
      pitch: 0,
      intonation: 1
    }
  }
}

describe('residentStore', () => {
  beforeEach(() => {
    useResidentStore.setState({
      residents: [],
      providerStatuses: [],
      expandedResidentName: null
    })
  })

  it('stores Brain provider availability for create/change menus', () => {
    useResidentStore.getState().setProviderStatuses([{
      name: 'codex',
      display_name: 'Codex',
      available: true,
      connected: true,
      configuration_mode: 'subscription-cli',
      models: [],
      default_model: null,
      default_reasoning_effort: null,
      custom_model_allowed: true
    }])

    expect(useResidentStore.getState().providerStatuses[0].name).toBe('codex')
    expect(useResidentStore.getState().providerStatuses[0].available).toBe(true)
  })

  it('sets the roster and upserts a created resident without duplicates', () => {
    useResidentStore.getState().setResidents([resident('Lapan', 'codex')])
    useResidentStore.getState().upsertResident(resident('Kina'))
    useResidentStore.getState().upsertResident(resident('Lapan', 'codex'))

    expect(useResidentStore.getState().residents.map((entry) => entry.name)).toEqual(['Lapan', 'Kina'])
  })

  it('removes a resident and clears expansion for that resident', () => {
    useResidentStore.getState().setResidents([resident('Lapan'), resident('Kina')])
    useResidentStore.getState().setExpandedResidentName('Lapan')

    useResidentStore.getState().removeResident('Lapan')

    expect(useResidentStore.getState().residents.map((entry) => entry.name)).toEqual(['Kina'])
    expect(useResidentStore.getState().expandedResidentName).toBeNull()
  })

  it('clears an expanded resident when roster sync removes it', () => {
    useResidentStore.getState().setResidents([resident('Lapan')])
    useResidentStore.getState().setExpandedResidentName('Lapan')
    useResidentStore.getState().setResidents([])

    expect(useResidentStore.getState().expandedResidentName).toBeNull()
  })
})
