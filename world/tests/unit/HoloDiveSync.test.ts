import { describe, expect, it } from 'vitest'
import {
  HOLO_ATTACH_WINDOW_MS,
  createHoloAttachDeadlineMs,
  createHoloDiveStartedPayload,
  isHoloAttachDeadlineActive
} from '../../src/renderer/src/runtime/holoDiveSync'

describe('Holo Dive synchronization deadline', () => {
  it('anchors the absolute attach deadline to the Master Dive action', () => {
    const startedAt = 1_000_000
    expect(createHoloAttachDeadlineMs(startedAt)).toBe(startedAt + HOLO_ATTACH_WINDOW_MS)
  })

  it('allows retry immediately before five minutes but never after expiry', () => {
    const startedAt = 1_000_000
    const expiresAt = createHoloAttachDeadlineMs(startedAt)
    expect(isHoloAttachDeadlineActive(expiresAt, expiresAt - 1)).toBe(true)
    expect(isHoloAttachDeadlineActive(expiresAt, expiresAt)).toBe(false)
    expect(isHoloAttachDeadlineActive(expiresAt, expiresAt + 1)).toBe(false)
  })

  it('reuses the same absolute deadline in every Core delivery', () => {
    const expiresAt = 1_300_000
    expect(createHoloDiveStartedPayload('DIVE-1', expiresAt)).toEqual({
      dive_session_id: 'DIVE-1',
      attach_expires_at_ms: expiresAt
    })
  })
})
