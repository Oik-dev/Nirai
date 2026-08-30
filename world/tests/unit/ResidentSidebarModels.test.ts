import { describe, expect, it } from 'vitest'
import { sortBrainModels } from '../../src/renderer/src/ui/ResidentSidebar'

describe('ResidentSidebar model ordering', () => {
  it('sorts provider models by display name without mutating the source list', () => {
    const source = [
      { id: 'z-model', display_name: 'Zeta' },
      { id: 'grok-xhigh', display_name: 'Grok 4.6 XHigh' },
      { id: 'alpha', display_name: 'Alpha' },
      { id: 'grok-high', display_name: 'Grok 4.6 High' }
    ] as const

    const sorted = sortBrainModels(source)

    expect(sorted.map((model) => model.display_name)).toEqual([
      'Alpha',
      'Grok 4.6 High',
      'Grok 4.6 XHigh',
      'Zeta'
    ])
    expect(source.map((model) => model.display_name)).toEqual([
      'Zeta',
      'Grok 4.6 XHigh',
      'Alpha',
      'Grok 4.6 High'
    ])
  })
})
