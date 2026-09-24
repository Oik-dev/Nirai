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
      bubbleRiseSpeed: 2.6,
      bubbleVerticalDensity: 0.85,
      bubbleHorizontalDensity: 2.8,
      horizonHaze: 6,
      waterPaleness: 1.4,
      sandWhiteness: 1.65,
      sandRelief: 15,
      waterSurfacePresence: 1.1,
      residentBrightness: 1
    })
    expect(sanitizeVisualTuning({
      waterSpeed: 20,
      waterCalmness: 10,
      lightShaftSpeed: Number.NaN,
      causticsSpeed: 20,
      bubbleRiseSpeed: 20,
      bubbleVerticalDensity: 20,
      bubbleHorizontalDensity: 0,
      horizonHaze: 99,
      waterPaleness: 9,
      sandWhiteness: 9,
      sandRelief: 99,
      waterSurfacePresence: 9,
      residentBrightness: 0
    })).toEqual({
      waterSpeed: 10,
      waterCalmness: 5,
      lightShaftSpeed: 2,
      causticsSpeed: 10,
      bubbleRiseSpeed: 10,
      bubbleVerticalDensity: 5,
      bubbleHorizontalDensity: 0.2,
      horizonHaze: 20,
      waterPaleness: 2,
      sandWhiteness: 1.65,
      sandRelief: 20,
      waterSurfacePresence: 2.5,
      residentBrightness: 0.4
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
      bubbleHorizontalDensity: 0.8,
      horizonHaze: 1.35,
      waterPaleness: 1.2,
      sandWhiteness: 1.45,
      sandRelief: 2.4,
      waterSurfacePresence: 1.1,
      residentBrightness: 1.3
    })

    expect(text).toContain('水面速度: 55%')
    expect(text).toContain('水面の穏やかさ: 85%')
    expect(text).toContain('光柱速度: 40%')
    expect(text).toContain('Caustics速度: 70%')
    expect(text).toContain('気泡の上昇速度: 125%')
    expect(text).toContain('気泡の縦密度: 140%')
    expect(text).toContain('気泡の横密度: 80%')
    expect(text).toContain('水平線の溶け込み: 135%')
    expect(text).toContain('青の淡さ: 120%')
    expect(text).toContain('砂の白さ: 145%')
    expect(text).toContain('砂の凹凸（波）: 240%')
    expect(text).toContain('水面感: 110%')
    expect(text).toContain('キャラの明るさ: 130%')
  })
})
