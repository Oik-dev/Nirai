export type FrameCallback = (delta: number) => void

export class RenderLoop {
  private animationFrameId: number | null = null
  private previousTime = 0

  constructor(private readonly onFrame: FrameCallback) {}

  start(): void {
    if (this.animationFrameId !== null) {
      return
    }

    this.previousTime = performance.now()
    this.animationFrameId = requestAnimationFrame(this.tick)
  }

  stop(): void {
    if (this.animationFrameId === null) {
      return
    }

    cancelAnimationFrame(this.animationFrameId)
    this.animationFrameId = null
  }

  private readonly tick = (time: number): void => {
    const delta = Math.min((time - this.previousTime) / 1000, 0.1)
    this.previousTime = time
    this.onFrame(delta)
    this.animationFrameId = requestAnimationFrame(this.tick)
  }
}
