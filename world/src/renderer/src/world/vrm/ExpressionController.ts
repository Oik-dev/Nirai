export type EmotionName = 'neutral' | 'happy' | 'angry' | 'sad'

export interface ExpressionManagerPort {
  setValue(name: string, weight: number): void
  getExpression?(name: string): { overrideBlink?: string } | null
}

export class ExpressionController {
  currentEmotion: EmotionName = 'neutral'

  private blinkElapsed = 0
  private blinkRemaining = 0

  constructor(
    private readonly expressionManager: ExpressionManagerPort | undefined,
    private readonly blinkIntervalSec = 4,
    private readonly blinkDurationSec = 0.12
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

    if (this.currentEmotion !== 'neutral') {
      this.expressionManager?.setValue(this.currentEmotion, 0)
    }

    if (next !== 'neutral') {
      this.expressionManager?.setValue(next, 1)
    }

    this.currentEmotion = next
  }

  triggerBlink(): void {
    this.expressionManager?.setValue('blink', 1)
    this.blinkRemaining = this.blinkDurationSec
    this.blinkElapsed = 0
  }

  update(delta: number): void {
    const safeDelta = Math.max(0, delta)

    if (this.blinkRemaining > 0) {
      this.blinkRemaining = Math.max(0, this.blinkRemaining - safeDelta)

      if (this.blinkRemaining === 0) {
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
    if (this.currentEmotion !== 'neutral') {
      this.expressionManager?.setValue(this.currentEmotion, 0)
    }
    this.expressionManager?.setValue('blink', 0)
    this.currentEmotion = 'neutral'
    this.blinkElapsed = 0
    this.blinkRemaining = 0
  }
}
