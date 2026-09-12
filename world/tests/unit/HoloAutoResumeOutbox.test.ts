import { describe, expect, it } from 'vitest'
import type { HoloAutoResumeEnqueueResult, HoloAutoResumeTrigger } from '../../src/preload/api'
import {
  HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY,
  HoloAutoResumeOutbox,
  rendererHoloAutoResumeTriggerKey
} from '../../src/renderer/src/runtime/HoloAutoResumeOutbox'

class MemoryStorage {
  readonly values = new Map<string, string>()

  getItem(key: string): string | null {
    return this.values.get(key) ?? null
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value)
  }

  removeItem(key: string): void {
    this.values.delete(key)
  }
}

class ManualTimer {
  private nextId = 1
  private readonly callbacks = new Map<number, () => void>()

  setTimeout(callback: () => void): number {
    const id = this.nextId++
    this.callbacks.set(id, callback)
    return id
  }

  clearTimeout(timerId: number): void {
    this.callbacks.delete(timerId)
  }

  runNext(): void {
    const next = this.callbacks.entries().next().value as [number, () => void] | undefined
    if (!next) return
    this.callbacks.delete(next[0])
    next[1]()
  }

  pendingCount(): number {
    return this.callbacks.size
  }
}

async function settle(): Promise<void> {
  await new Promise<void>((resolve) => setTimeout(resolve, 0))
}

function result(accepted: boolean, duplicate = false): HoloAutoResumeEnqueueResult {
  return { accepted, duplicate, pending_count: accepted ? 1 : 0 }
}

describe('HoloAutoResumeOutbox', () => {
  it('keeps a rejected trigger durable and retries until the Host acknowledges persistence', async () => {
    const storage = new MemoryStorage()
    const timer = new ManualTimer()
    const attempts: HoloAutoResumeTrigger[] = []
    let accept = false
    const outbox = new HoloAutoResumeOutbox(
      storage,
      async (trigger) => {
        attempts.push(trigger)
        return result(accept)
      },
      timer
    )

    outbox.enqueue({ task_id: 'T-33', reason: 'done' })
    await settle()

    expect(attempts).toHaveLength(1)
    expect(outbox.pendingCount()).toBe(1)
    expect(JSON.parse(storage.getItem(HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY) ?? '[]')).toHaveLength(1)
    expect(timer.pendingCount()).toBe(1)

    accept = true
    timer.runNext()
    await settle()

    expect(attempts).toHaveLength(2)
    expect(outbox.pendingCount()).toBe(0)
    expect(storage.getItem(HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY)).toBeNull()
    outbox.dispose()
  })

  it('restores a persisted trigger after renderer restart and removes it only after duplicate/accepted acknowledgement', async () => {
    const storage = new MemoryStorage()
    const timer = new ManualTimer()
    storage.setItem(HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY, JSON.stringify([
      { task_id: 'T-RESTORE', agent_session_id: 'AS-RESTORE', reason: 'interrupted' }
    ]))
    const attempts: HoloAutoResumeTrigger[] = []
    const outbox = new HoloAutoResumeOutbox(
      storage,
      async (trigger) => {
        attempts.push(trigger)
        return result(false, true)
      },
      timer
    )

    outbox.start()
    await settle()

    expect(attempts).toEqual([
      { task_id: 'T-RESTORE', agent_session_id: 'AS-RESTORE', reason: 'interrupted' }
    ])
    expect(outbox.pendingCount()).toBe(0)
    expect(storage.getItem(HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY)).toBeNull()
    outbox.dispose()
  })

  it('namespaces Review triggers without changing legacy Task keys', () => {
    expect(rendererHoloAutoResumeTriggerKey({
      task_id: 'T-LEGACY', agent_session_id: 'AS-1', reason: 'done'
    })).toBe('T-LEGACY:AS-1:done:-')
    expect(rendererHoloAutoResumeTriggerKey({
      kind: 'review', task_id: 'HR-1', agent_session_id: 'AS-HR-1', reason: 'failed'
    })).toBe('review:HR-1:AS-HR-1:failed:-')
  })

  it('persists more triggers than the Host queue can hold without dropping overflow', async () => {
    const storage = new MemoryStorage()
    const timer = new ManualTimer()
    let release!: (value: HoloAutoResumeEnqueueResult) => void
    const pending = new Promise<HoloAutoResumeEnqueueResult>((resolve) => { release = resolve })
    const outbox = new HoloAutoResumeOutbox(storage, async () => pending, timer)

    for (let index = 0; index < 40; index += 1) {
      outbox.enqueue({ task_id: `T-OVERFLOW-${index}`, reason: 'done' })
    }
    await settle()

    expect(outbox.pendingCount()).toBe(40)
    expect(JSON.parse(storage.getItem(HOLO_AUTO_RESUME_OUTBOX_STORAGE_KEY) ?? '[]')).toHaveLength(40)
    release(result(false))
    await settle()
    expect(outbox.pendingCount()).toBe(40)
    outbox.dispose()
  })

  it('deduplicates repeated protocol events before sending them to the Host', async () => {
    const storage = new MemoryStorage()
    const timer = new ManualTimer()
    let release!: (value: HoloAutoResumeEnqueueResult) => void
    const pending = new Promise<HoloAutoResumeEnqueueResult>((resolve) => { release = resolve })
    const attempts: HoloAutoResumeTrigger[] = []
    const outbox = new HoloAutoResumeOutbox(
      storage,
      async (trigger) => {
        attempts.push(trigger)
        return pending
      },
      timer
    )
    const trigger = {
      task_id: 'T-WAIT',
      agent_session_id: 'AS-WAIT',
      reason: 'waiting_for_master' as const,
      request_id: 'REQ-1',
      request_kind: 'approval' as const
    }

    outbox.enqueue(trigger)
    outbox.enqueue(trigger)
    await settle()

    expect(attempts).toHaveLength(1)
    expect(outbox.pendingCount()).toBe(1)
    release(result(true))
    await settle()
    expect(outbox.pendingCount()).toBe(0)
    outbox.dispose()
  })
})
