import type {
  HoloAutoResumeEnqueueResult,
  HoloAutoResumeTrigger
} from '../../../preload/api'

export const HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY = 'nirai:holo-auto-resume-outbox:v1'
export const HOLO_AUTO_RESUME_CANCELLED_STORAGE_KEY = 'nirai:holo-auto-resume-cancelled:v1'
const HOLO_AUTO_RESUME_OUTBOX_RETRY_MS = 2_000
const HOLO_AUTO_RESUME_CANCELLED_LIMIT = 512

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
  private cancelledTaskIds: string[]
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
    this.cancelledTaskIds = this.readCancelledTaskIds()
    const cancelled = new Set(this.cancelledTaskIds)
    this.entries = this.readPersistedEntries().filter((entry) => !cancelled.has(entry.task_id))
    this.persistEntries()
  }

  start(): void {
    if (this.entries.length > 0) void this.flush()
  }

  enqueue(trigger: HoloAutoResumeTrigger): void {
    if (this.disposed || !isRendererHoloAutoResumeTrigger(trigger)) return
    if (this.cancelledTaskIds.includes(trigger.task_id)) return
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

  snapshot(): readonly HoloAutoResumeTrigger[] {
    return this.entries.map((entry) => ({ ...entry }))
  }

  cancelledTaskIdsSnapshot(): readonly string[] {
    return [...this.cancelledTaskIds]
  }

  cancelTask(taskId: string): void {
    if (!taskId.trim()) return
    for (let index = this.entries.length - 1; index >= 0; index -= 1) {
      if (this.entries[index].task_id === taskId) this.entries.splice(index, 1)
    }
    this.cancelledTaskIds = [
      ...this.cancelledTaskIds.filter((candidate) => candidate !== taskId),
      taskId
    ].slice(-HOLO_AUTO_RESUME_CANCELLED_LIMIT)
    this.persistEntries()
    this.persistCancelledTaskIds()
  }

  private readCancelledTaskIds(): string[] {
    try {
      const raw = this.storage.getItem(HOLO_AUTO_RESUME_CANCELLED_STORAGE_KEY)
      if (!raw) return []
      const parsed = JSON.parse(raw)
      if (!Array.isArray(parsed)) return []
      return [...new Set(parsed.filter((value): value is string => (
        typeof value === 'string' && /^(?:T|HR|IA|WF)-[A-Za-z0-9-]+$/.test(value)
      )))].slice(-HOLO_AUTO_RESUME_CANCELLED_LIMIT)
    } catch {
      return []
    }
  }

  private persistCancelledTaskIds(): void {
    try {
      if (this.cancelledTaskIds.length === 0) {
        this.storage.removeItem(HOLO_AUTO_RESUME_CANCELLED_STORAGE_KEY)
        return
      }
      this.storage.setItem(
        HOLO_AUTO_RESUME_CANCELLED_STORAGE_KEY,
        JSON.stringify(this.cancelledTaskIds)
      )
    } catch {
      // Host-side cancellation is still authoritative when available. Keep the
      // in-memory guard for this renderer lifetime and retry on the next cancel.
    }
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
      this.persistEntries()
      let index = 0
      while (!this.disposed && index < this.entries.length) {
        const trigger = this.entries[index]
        let result: HoloAutoResumeEnqueueResult
        try {
          result = await this.send(trigger)
        } catch {
          const currentIndex = this.entries.indexOf(trigger)
          if (currentIndex >= 0) index = currentIndex + 1
          continue
        }
        const currentIndex = this.entries.indexOf(trigger)
        if (currentIndex < 0) continue
        if (!result.accepted && !result.duplicate && !result.discarded) {
          // Unowned events and queue pressure must not block other Conversations.
          // Keep this entry durable and try each later entry once this pass.
          index = currentIndex + 1
          continue
        }
        this.entries.splice(currentIndex, 1)
        index = currentIndex
        this.persistEntries()
      }
    } finally {
      this.flushing = false
      this.scheduleRetry()
    }
  }
}
