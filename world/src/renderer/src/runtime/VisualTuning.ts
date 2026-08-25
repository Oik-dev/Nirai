export interface VisualTuning {
  readonly waterSpeed: number
  readonly waterCalmness: number
  readonly lightShaftSpeed: number
  readonly causticsSpeed: number
  readonly bubbleRiseSpeed: number
  readonly bubbleVerticalDensity: number
  readonly bubbleHorizontalDensity: number
}

export const DEFAULT_VISUAL_TUNING: VisualTuning = Object.freeze({
  waterSpeed: 7,
  waterCalmness: 5,
  lightShaftSpeed: 2,
  causticsSpeed: 4,
  bubbleRiseSpeed: 1.9,
  bubbleVerticalDensity: 0.55,
  bubbleHorizontalDensity: 2.8
})

function finiteOr(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

export function sanitizeVisualTuning(value: unknown): VisualTuning {
  const candidate = value && typeof value === 'object'
    ? value as Partial<Record<keyof VisualTuning, unknown>>
    : {}

  return {
    waterSpeed: clamp(
      finiteOr(candidate.waterSpeed, DEFAULT_VISUAL_TUNING.waterSpeed),
      0,
      10
    ),
    waterCalmness: clamp(
      finiteOr(candidate.waterCalmness, DEFAULT_VISUAL_TUNING.waterCalmness),
      0,
      5
    ),
    lightShaftSpeed: clamp(
      finiteOr(candidate.lightShaftSpeed, DEFAULT_VISUAL_TUNING.lightShaftSpeed),
      0,
      10
    ),
    causticsSpeed: clamp(
      finiteOr(candidate.causticsSpeed, DEFAULT_VISUAL_TUNING.causticsSpeed),
      0,
      10
    ),
    bubbleRiseSpeed: clamp(
      finiteOr(candidate.bubbleRiseSpeed, DEFAULT_VISUAL_TUNING.bubbleRiseSpeed),
      0,
      10
    ),
    bubbleVerticalDensity: clamp(
      finiteOr(candidate.bubbleVerticalDensity, DEFAULT_VISUAL_TUNING.bubbleVerticalDensity),
      0,
      5
    ),
    bubbleHorizontalDensity: clamp(
      finiteOr(candidate.bubbleHorizontalDensity, DEFAULT_VISUAL_TUNING.bubbleHorizontalDensity),
      0.2,
      5
    )
  }
}

export function formatVisualTuning(value: VisualTuning): string {
  const tuning = sanitizeVisualTuning(value)
  return [
    'Nirai Visual Speed Lab',
    `水面速度: ${Math.round(tuning.waterSpeed * 100)}%`,
    `水面の穏やかさ: ${Math.round(tuning.waterCalmness * 100)}%`,
    `光柱速度: ${Math.round(tuning.lightShaftSpeed * 100)}%`,
    `Caustics速度: ${Math.round(tuning.causticsSpeed * 100)}%`,
    `気泡の上昇速度: ${Math.round(tuning.bubbleRiseSpeed * 100)}%`,
    `気泡の縦密度: ${Math.round(tuning.bubbleVerticalDensity * 100)}%`,
    `気泡の横密度: ${Math.round(tuning.bubbleHorizontalDensity * 100)}%`
  ].join('\n')
}
