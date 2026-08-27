export type FrameCallback = (delta: number) => void

export const DEFAULT_MAX_RENDER_FPS = 72
const FRAME_SCHEDULE_TOLERANCE_MS = 0.25

export class RenderLoop {
  private animationFrameId: number | null = null
  private previousTime = 0
  private nextFrameTime = 0
  private readonly frameIntervalMs: number

  constructor(
    private readonly onFrame: FrameCallback,
    maxFps = DEFAULT_MAX_RENDER_FPS
  ) {
    const safeMaxFps = Number.isFinite(maxFps) && maxFps > 0
      ? maxFps
      : DEFAULT_MAX_RENDER_FPS
    this.frameIntervalMs = 1000 / safeMaxFps
  }

  start(): void {
    if (this.animationFrameId !== null) {
      return
    }

    this.previousTime = performance.now()
    this.nextFrameTime = this.previousTime + this.frameIntervalMs
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
    if (time + FRAME_SCHEDULE_TOLERANCE_MS < this.nextFrameTime) {
      this.animationFrameId = requestAnimationFrame(this.tick)
      return
    }

    const delta = Math.min((time - this.previousTime) / 1000, 0.1)
    this.previousTime = time
    this.onFrame(delta)

    this.nextFrameTime += this.frameIntervalMs
    if (this.nextFrameTime <= time) {
      const skippedIntervals = Math.floor(
        (time - this.nextFrameTime) / this.frameIntervalMs
      ) + 1
      this.nextFrameTime += skippedIntervals * this.frameIntervalMs
    }
    this.animationFrameId = requestAnimationFrame(this.tick)
  }
}
