import type { AnalyserListener } from './AudioService'
import type { SpeechTask } from './types'

export interface AudioPlaybackPort {
  play(bytes: Uint8Array, onAnalyser?: AnalyserListener): Promise<void>
  stop(): void
}

interface QueuedSpeech {
  readonly task: SpeechTask
  readonly generation: number
}

export class SpeechQueue {
  private queue: QueuedSpeech[] = []
  private processing = false
  private generation = 0
  private current: SpeechTask | null = null
  private readonly cancelledRequests = new Set<string>()

  constructor(
    private readonly audio: AudioPlaybackPort,
    private readonly onSpeakingChange?: (residentName: string | null, requestId: string | null) => void,
    private readonly onAnalyser?: (residentName: string, analyser: AnalyserNode | null) => void
  ) {}

  get generationToken(): number {
    return this.generation
  }

  enqueue(task: SpeechTask, generation = this.generation): void {
    if (generation !== this.generation || this.cancelledRequests.has(task.requestId)) return
    this.queue.push({ task, generation })
    void this.drain()
  }

  cancel(requestId: string): void {
    this.cancelledRequests.add(requestId)
    this.queue = this.queue.filter((item) => item.task.requestId !== requestId)
    if (this.current?.requestId === requestId) {
      this.audio.stop()
    }
  }

  stopAll(): void {
    this.generation += 1
    this.queue = []
    this.audio.stop()
  }

  get pendingCount(): number {
    return this.queue.length + (this.current ? 1 : 0)
  }

  private async drain(): Promise<void> {
    if (this.processing) return
    this.processing = true
    try {
      while (this.queue.length > 0) {
        const item = this.queue.shift()
        if (!item || item.generation !== this.generation || this.cancelledRequests.has(item.task.requestId)) continue

        this.current = item.task
        this.onSpeakingChange?.(item.task.residentName, item.task.requestId)
        try {
          await this.audio.play(
            item.task.audio,
            (analyser) => this.onAnalyser?.(item.task.residentName, analyser)
          )
        } catch {
          // Audio presentation failure must not break the text conversation or queue.
        } finally {
          if (this.current === item.task) {
            this.current = null
            this.onSpeakingChange?.(null, null)
          }
        }
      }
    } finally {
      this.processing = false
      if (this.queue.length > 0) {
        void this.drain()
      }
    }
  }
}
