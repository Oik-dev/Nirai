import { describe, expect, it } from 'vitest'
import {
  DEFAULT_MOTION_TUNING,
  formatMotionTuning,
  sanitizeMotionTuning
} from '../../src/renderer/src/runtime/MotionTuning'

describe('MotionTuning', () => {
  it('keeps the current Move presentation as defaults and clamps debug ranges', () => {
    expect(DEFAULT_MOTION_TUNING).toEqual({
      turnSpeedScale: 0.2,
      rightLegMatch: 1,
      kneeStraightening: 0.5
    })
    expect(formatMotionTuning(DEFAULT_MOTION_TUNING)).toBe(
      'Turn 20% / Right Match 100% / Knee 50%'
    )

    expect(sanitizeMotionTuning({
      turnSpeedScale: 0,
      rightLegMatch: 9,
      kneeStraightening: 2
    })).toEqual({
      turnSpeedScale: 0.2,
      rightLegMatch: 1,
      kneeStraightening: 1
    })
  })
})
