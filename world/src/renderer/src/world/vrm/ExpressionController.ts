export type EmotionName =
  | 'neutral'
  | 'happy'
  | 'angry'
  | 'sad'
  | 'relaxed'
  | 'surprised'
  | 'awkward'
  | 'doubt'

export const EMOTION_NAMES: readonly EmotionName[] = [
  'neutral',
  'happy',
  'angry',
  'sad',
  'relaxed',
  'surprised',
  'awkward',
  'doubt'
]

// World resolves Core meaning names onto Avatar-specific VRM expressions.
// Missing clips stay no-op; do not invent a second public emotion set.

export interface ExpressionManagerPort {
  setValue(name: string, weight: number): void
  getExpression?(name: string): { overrideBlink?: string } | null
  readonly expressionMap?: Readonly<Record<string, { overrideBlink?: string }>>
}

const EMOTION_PATTERNS: Readonly<Record<Exclude<EmotionName, 'neutral'>, readonly RegExp[]>> = {
  happy: [/^happy$/i, /happy\d*$/i, /^joy$/i],
  angry: [/^angry$/i, /angry\d*$/i],
  sad: [/^sad$/i, /sad\d*$/i, /^sorrow$/i],
  relaxed: [/^relaxed$/i, /^fun$/i],
  surprised: [/^surprised$/i, /startled$/i],
  awkward: [/awkward\d*$/i],
  doubt: [/doubt$/i]
}

export class ExpressionController {
  currentEmotion: EmotionName = 'neutral'

  private currentExpressionName: string | null = null
  private blinkElapsed = 0
  private blinkRemaining = 0
  private blinkHeld = false
  private heldBlinkOverride: {
    expression: { overrideBlink?: string }
    original: string | undefined
  } | null = null

  constructor(
    private readonly expressionManager: ExpressionManagerPort | undefined,
    private readonly blinkIntervalSec = 4,
    private readonly blinkDurationSec = 0.12,
    private readonly random: () => number = Math.random
  ) {
    const blinkExpression = this.expressionManager?.getExpression?.('blink')

    if (blinkExpression?.overrideBlink === 'block') {
      blinkExpression.overrideBlink = 'none'
    }
  }

  setEmotion(next: EmotionName): void {
    if (next === this.currentEmotion) {
      return
    }

    if (this.currentExpressionName) {
      this.expressionManager?.setValue(this.currentExpressionName, 0)
    }

    this.currentEmotion = next
    this.currentExpressionName = next === 'neutral'
      ? null
      : this.pickEmotionExpression(next)

    if (this.currentExpressionName) {
      this.expressionManager?.setValue(this.currentExpressionName, 1)
    }

    if (this.blinkHeld) {
      this.allowHeldBlinkThroughEmotion()
    }
  }

  getAvailableEmotions(): readonly EmotionName[] {
    return EMOTION_NAMES.filter((name) =>
      name === 'neutral' || this.resolveEmotionExpressions(name).length > 0
    )
  }

  triggerBlink(durationSec = this.blinkDurationSec): void {
    this.expressionManager?.setValue('blink', 1)
    this.blinkRemaining = Math.max(0, durationSec)
    this.blinkElapsed = 0
  }

  setBlinkHeld(held: boolean): void {
    if (this.blinkHeld === held) {
      return
    }

    this.blinkHeld = held
    this.blinkRemaining = 0
    this.blinkElapsed = 0
    if (held) {
      this.allowHeldBlinkThroughEmotion()
    } else {
      this.restoreHeldBlinkOverride()
    }
    this.expressionManager?.setValue('blink', held ? 1 : 0)
  }

  update(delta: number): void {
    const safeDelta = Math.max(0, delta)

    if (this.blinkHeld) {
      this.expressionManager?.setValue('blink', 1)
      return
    }

    if (this.blinkRemaining > 0) {
      this.blinkRemaining = Math.max(0, this.blinkRemaining - safeDelta)

      if (this.blinkRemaining <= 1e-6) {
        this.blinkRemaining = 0
        this.expressionManager?.setValue('blink', 0)
      }

      return
    }

    this.blinkElapsed += safeDelta

    if (this.blinkElapsed >= this.blinkIntervalSec) {
      this.triggerBlink()
    }
  }

  dispose(): void {
    this.restoreHeldBlinkOverride()
    if (this.currentExpressionName) {
      this.expressionManager?.setValue(this.currentExpressionName, 0)
    }
    this.expressionManager?.setValue('blink', 0)
    this.currentEmotion = 'neutral'
    this.currentExpressionName = null
    this.blinkElapsed = 0
    this.blinkRemaining = 0
    this.blinkHeld = false
  }

  private allowHeldBlinkThroughEmotion(): void {
    this.restoreHeldBlinkOverride()
    if (!this.currentExpressionName) {
      return
    }

    const expression = this.expressionManager?.getExpression?.(this.currentExpressionName)
    if (!expression || expression.overrideBlink === undefined || expression.overrideBlink === 'none') {
      return
    }

    this.heldBlinkOverride = {
      expression,
      original: expression.overrideBlink
    }
    expression.overrideBlink = 'none'
  }

  private restoreHeldBlinkOverride(): void {
    if (!this.heldBlinkOverride) {
      return
    }

    this.heldBlinkOverride.expression.overrideBlink = this.heldBlinkOverride.original
    this.heldBlinkOverride = null
  }

  private pickEmotionExpression(emotion: Exclude<EmotionName, 'neutral'>): string | null {
    const candidates = this.resolveEmotionExpressions(emotion)
    if (candidates.length === 0) {
      return null
    }

    const index = Math.min(
      candidates.length - 1,
      Math.max(0, Math.floor(this.random() * candidates.length))
    )
    return candidates[index]
  }

  private resolveEmotionExpressions(emotion: Exclude<EmotionName, 'neutral'>): string[] {
    const expressionNames = Object.keys(this.expressionManager?.expressionMap ?? {})
    if (expressionNames.length === 0) {
      return this.expressionManager?.getExpression?.(emotion) ? [emotion] : []
    }

    const result: string[] = []
    for (const pattern of EMOTION_PATTERNS[emotion]) {
      for (const name of expressionNames) {
        if (pattern.test(name) && !result.includes(name)) {
          result.push(name)
        }
      }
    }
    return result
  }
}
