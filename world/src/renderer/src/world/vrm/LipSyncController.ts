export interface LipExpressionPort {
  setLipWeight(weight: number): void
}

export interface TimeDomainAnalyserPort {
  getFloatTimeDomainData(array: Float32Array): void
}

export class LipSyncController {
  private analyser: TimeDomainAnalyserPort | null = null
  private readonly samples = new Float32Array(2048)

  constructor(private readonly expression: LipExpressionPort) {}

  setAnalyser(analyser: TimeDomainAnalyserPort | null): void {
    this.analyser = analyser
    if (analyser === null) {
      this.expression.setLipWeight(0)
    }
  }

  update(): void {
    const analyser = this.analyser
    if (!analyser) {
      this.expression.setLipWeight(0)
      return
    }

    analyser.getFloatTimeDomainData(this.samples)
    let raw = 0
    for (const sample of this.samples) {
      raw = Math.max(raw, Math.abs(sample))
    }
    let weight = 1 / (1 + Math.exp(-45 * raw + 5))
    if (weight < 0.1) weight = 0
    this.expression.setLipWeight(weight)
  }

  dispose(): void {
    this.analyser = null
    this.expression.setLipWeight(0)
  }
}
