export interface MotionTuning {
  readonly turnSpeedScale: number
  readonly rightLegMatch: number
  readonly kneeStraightening: number
}

export const DEFAULT_MOTION_TUNING: MotionTuning = Object.freeze({
  turnSpeedScale: 0.2,
  rightLegMatch: 1,
  kneeStraightening: 0.5
})

function finiteOr(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

export function formatMotionTuning(value: MotionTuning): string {
  return [
    `Turn ${Math.round(value.turnSpeedScale * 100)}%`,
    `Right Match ${Math.round(value.rightLegMatch * 100)}%`,
    `Knee ${Math.round(value.kneeStraightening * 100)}%`
  ].join(' / ')
}

export function sanitizeMotionTuning(value: unknown): MotionTuning {
  const candidate = value && typeof value === 'object'
    ? value as Partial<Record<keyof MotionTuning, unknown>>
    : {}

  return {
    turnSpeedScale: clamp(
      finiteOr(candidate.turnSpeedScale, DEFAULT_MOTION_TUNING.turnSpeedScale),
      0.2,
      1.5
    ),
    rightLegMatch: clamp(
      finiteOr(candidate.rightLegMatch, DEFAULT_MOTION_TUNING.rightLegMatch),
      0,
      1
    ),
    kneeStraightening: clamp(
      finiteOr(candidate.kneeStraightening, DEFAULT_MOTION_TUNING.kneeStraightening),
      0,
      1
    )
  }
}
