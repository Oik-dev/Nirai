import type {
  HoloAutoResumeEnqueueResult,
  HoloAutoResumeTrigger
} from '../../../preload/api'

export const HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY = 'nirai:holo-auto-resume-outbox:v1'
const HOLO_AUTO_RESUME_OUTBOX_RETRY_MS = 2_000

interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

interface TimerLike {
  setTimeout(callback: () => void, delayMs: number): number
  clearTimeout(timerId: number): void
}

function isStringOrNullish(value: unknown): boolean {
  return value == null || typeof value === 'string'
}

export function isRendererHoloAutoResumeTrigger(value: unknown): value is HoloAutoResumeTrigger {
  if (!value || typeof value !== 'object') return false
  const trigger = value as Partial<HoloAutoResumeTrigger>
  return (trigger.kind == null || ['task', 'review'].includes(String(trigger.kind)))
    && typeof trigger.task_id === 'string'
    && trigger.task_id.trim().length > 0
    && ['done', 'failed', 'cancelled', 'interrupted', 'waiting_for_master', 'workflow_stalled']
      .includes(String(trigger.reason))
    && isStringOrNullish(trigger.agent_session_id)
    && isStringOrNullish(trigger.request_id)
    && (trigger.request_kind == null || ['approval', 'question', 'plan'].includes(String(trigger.request_kind)))
}

export function rendererHoloAutoResumeTriggerKey(trigger: HoloAutoResumeTrigger): string {
  const prefix = trigger.kind === 'review' ? 'review:' : ''
  return `${prefix}${[
    trigger.task_id.trim(),
    trigger.agent_session_id?.trim() || '-',
    trigger.reason,
    trigger.request_id?.trim() || '-'
  ].join(':')}`
}

export class HoloAutoResumeOutbox {
  private readonly entries: HoloAutoResumeTrigger[]
  private flushing = false
  private disposed = false
  private retryTimer: number | null = null

  constructor(
    private readonly storage: StorageLike,
    private readonly send: (trigger: HoloAutoResumeTrigger) => Promise<HoloAutoResumeEnqueueResult>,
    private readonly timer: TimerLike = {
      setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
      clearTimeout: (timerId) => window.clearTimeout(timerId)
    }
  ) {
    this.entries = this.readPersistedEntries()
  }

  start(): void {
    if (this.entries.length > 0) void this.flush()
  }

  enqueue(trigger: HoloAutoResumeTrigger): void {
    if (this.disposed || !isRendererHoloAutoResumeTrigger(trigger)) return
    const key = rendererHoloAutoResumeTriggerKey(trigger)
    if (this.entries.some((candidate) => rendererHoloAutoResumeTriggerKey(candidate) === key)) {
      return
    }
    this.entries.push({ ...trigger })
    this.persistEntries()
    void this.flush()
  }

  dispose(): void {
    this.disposed = true
    if (this.retryTimer !== null) {
      this.timer.clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
  }

  pendingCount(): number {
    return this.entries.length
  }

  private readPersistedEntries(): HoloAutoResumeTrigger[] {
    try {
      const raw = this.storage.getItem(HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY)
      if (!raw) return []
      const parsed = JSON.parse(raw)
      if (!Array.isArray(parsed)) return []
      const unique = new Map<string, HoloAutoResumeTrigger>()
      for (const candidate of parsed) {
        if (!isRendererHoloAutoResumeTrigger(candidate)) continue
        const trigger = { ...candidate }
        unique.set(rendererHoloAutoResumeTriggerKey(trigger), trigger)
      }
      return [...unique.values()]
    } catch {
      return []
    }
  }

  private persistEntries(): void {
    try {
      if (this.entries.length === 0) {
        this.storage.removeItem(HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY)
        return
      }
      this.storage.setItem(
        HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY,
        JSON.stringify(this.entries)
      )
    } catch {
      // Keep the in-memory copy. The next mutation/retry will attempt persistence again.
    }
  }

  private scheduleRetry(): void {
    if (this.disposed || this.retryTimer !== null || this.entries.length === 0) return
    this.retryTimer = this.timer.setTimeout(() => {
      this.retryTimer = null
      void this.flush()
    }, HOLO_AUTO_RESUME_OUTBOX_RETRY_MS)
  }

  private async flush(): Promise<void> {
    if (this.disposed || this.flushing || this.entries.length === 0) return
    this.flushing = true
    try {
      while (!this.disposed && this.entries.length > 0) {
        const trigger = this.entries[0]
        let result: HoloAutoResumeEnqueueResult
        try {
          result = await this.send(trigger)
        } catch {
          this.scheduleRetry()
          return
        }
        if (!result.accepted && !result.duplicate) {
          this.scheduleRetry()
          return
        }
        this.entries.shift()
        this.persistEntries()
      }
    } finally {
      this.flushing = false
    }
  }
}
