export interface VisualTuning {
  readonly waterSpeed: number
  readonly waterCalmness: number
  readonly lightShaftSpeed: number
  readonly causticsSpeed: number
  readonly bubbleRiseSpeed: number
  readonly bubbleVerticalDensity: number
  readonly bubbleHorizontalDensity: number
  readonly horizonHaze: number
  readonly waterPaleness: number
  readonly sandWhiteness: number
  readonly sandRelief: number
  readonly waterSurfacePresence: number
  readonly residentBrightness: number
}

export const DEFAULT_VISUAL_TUNING: VisualTuning = Object.freeze({
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
    ),
    horizonHaze: clamp(
      finiteOr(candidate.horizonHaze, DEFAULT_VISUAL_TUNING.horizonHaze),
      0,
      20
    ),
    waterPaleness: clamp(
      finiteOr(candidate.waterPaleness, DEFAULT_VISUAL_TUNING.waterPaleness),
      0,
      2
    ),
    sandWhiteness: clamp(
      finiteOr(candidate.sandWhiteness, DEFAULT_VISUAL_TUNING.sandWhiteness),
      0,
      1.65
    ),
    sandRelief: clamp(
      finiteOr(candidate.sandRelief, DEFAULT_VISUAL_TUNING.sandRelief),
      0,
      20
    ),
    waterSurfacePresence: clamp(
      finiteOr(candidate.waterSurfacePresence, DEFAULT_VISUAL_TUNING.waterSurfacePresence),
      0,
      2.5
    ),
    residentBrightness: clamp(
      finiteOr(candidate.residentBrightness, DEFAULT_VISUAL_TUNING.residentBrightness),
      0.4,
      2.5
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
    `気泡の横密度: ${Math.round(tuning.bubbleHorizontalDensity * 100)}%`,
    `水平線の溶け込み: ${Math.round(tuning.horizonHaze * 100)}%`,
    `青の淡さ: ${Math.round(tuning.waterPaleness * 100)}%`,
    `砂の白さ: ${Math.round(tuning.sandWhiteness * 100)}%`,
    `砂の凹凸（波）: ${Math.round(tuning.sandRelief * 100)}%`,
    `水面感: ${Math.round(tuning.waterSurfacePresence * 100)}%`,
    `キャラの明るさ: ${Math.round(tuning.residentBrightness * 100)}%`
  ].join('\n')
}
