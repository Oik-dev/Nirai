import { describe, expect, it } from 'vitest'
import {
  DEFAULT_VISUAL_TUNING,
  formatVisualTuning,
  sanitizeVisualTuning
} from '../../src/renderer/src/runtime/VisualTuning'

describe('VisualTuning', () => {
  it('uses safe defaults and clamps the temporary tuning ranges', () => {
    expect(sanitizeVisualTuning(null)).toEqual(DEFAULT_VISUAL_TUNING)
    expect(DEFAULT_VISUAL_TUNING).toMatchObject({
      waterSpeed: 7,
      waterCalmness: 5,
      lightShaftSpeed: 2,
      causticsSpeed: 4,
      bubbleRiseSpeed: 1.9,
      bubbleVerticalDensity: 0.55,
      bubbleHorizontalDensity: 2.8
    })
    expect(sanitizeVisualTuning({
      waterSpeed: 20,
      waterCalmness: 10,
      lightShaftSpeed: Number.NaN,
      causticsSpeed: 20,
      bubbleRiseSpeed: 20,
      bubbleVerticalDensity: 20,
      bubbleHorizontalDensity: 0
    })).toEqual({
      waterSpeed: 10,
      waterCalmness: 5,
      lightShaftSpeed: 2,
      causticsSpeed: 10,
      bubbleRiseSpeed: 10,
      bubbleVerticalDensity: 5,
      bubbleHorizontalDensity: 0.2
    })
  })

  it('formats a plain Japanese handoff Master can paste back', () => {
    const text = formatVisualTuning({
      waterSpeed: 0.55,
      waterCalmness: 0.85,
      lightShaftSpeed: 0.4,
      causticsSpeed: 0.7,
      bubbleRiseSpeed: 1.25,
      bubbleVerticalDensity: 1.4,
      bubbleHorizontalDensity: 0.8
    })

    expect(text).toContain('水面速度: 55%')
    expect(text).toContain('水面の穏やかさ: 85%')
    expect(text).toContain('光柱速度: 40%')
    expect(text).toContain('Caustics速度: 70%')
    expect(text).toContain('気泡の上昇速度: 125%')
    expect(text).toContain('気泡の縦密度: 140%')
    expect(text).toContain('気泡の横密度: 80%')
  })
})
