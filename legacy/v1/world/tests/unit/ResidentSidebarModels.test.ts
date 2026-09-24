import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import {
  isHoloAddonBrain,
  ResidentRoleField,
  sortBrainModels,
  UsageBudgetView
} from '../../src/renderer/src/ui/ResidentSidebar'
import type { ResidentPayload } from '../../src/renderer/src/protocol/types'

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

  it('identifies the holo-addon brain kind that hides model / voice settings', () => {
    expect(isHoloAddonBrain('holo-addon')).toBe(true)
    expect(isHoloAddonBrain('codex')).toBe(false)
    expect(isHoloAddonBrain(null)).toBe(false)
  })

  it('renders role selection and fresh/stale usage windows in the resident glass surface', () => {
    const resident: ResidentPayload = {
      name: 'Cursor',
      role: 'executor',
      brain: 'cursor',
      brain_model: 'cursor-grok-4.6-xhigh',
      brain_reasoning_effort: null,
      avatar: null,
      location: 'center',
      availability: 'limited',
      usage_budget: {
        provider: 'cursor',
        profile: 'Pro',
        status: 'limited',
        fetched_at: '2026-09-12T00:00:00Z',
        source: 'test',
        stale: true,
        last_error: 'refresh failed',
        windows: [{
          id: 'cursor_models',
          type: 'cursor_models',
          duration_seconds: null,
          used_percent: 100,
          remaining_percent: 0,
          used_amount: null,
          limit_amount: null,
          unit: null,
          reset_at: '2026-09-13T00:00:00Z',
          reset_in_seconds: 3600,
          limit_reached: true
        }]
      },
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
    const roleHtml = renderToStaticMarkup(createElement(ResidentRoleField, {
      id: 'test-role',
      value: 'executor',
      disabled: false,
      onChange: () => undefined
    }))
    const html = renderToStaticMarkup(createElement(UsageBudgetView, { resident }))

    expect(roleHtml).toContain('住人')
    expect(roleHtml).toContain('実行者')
    expect(roleHtml).toContain('統合監査者')
    expect(roleHtml).toContain('指揮者')
    expect(roleHtml).toContain('selected')
    expect(html).toContain('Cursor Models')
    expect(html).toContain('100% used')
    expect(html).toContain('0% remaining')
    expect(html).toContain('STALE')
    expect(html).toContain('LIMITED')
    expect(html).toContain('Reset')
  })
})
