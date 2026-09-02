export const HOLO_ATTACH_WINDOW_MS = 5 * 60 * 1000

export function createHoloAttachDeadlineMs(nowMs = Date.now()): number {
  return nowMs + HOLO_ATTACH_WINDOW_MS
}

export function isHoloAttachDeadlineActive(expiresAtMs: number, nowMs = Date.now()): boolean {
  return Number.isFinite(expiresAtMs) && nowMs < expiresAtMs
}

export function createHoloDiveStartedPayload(
  diveSessionId: string,
  attachExpiresAtMs: number
): { readonly dive_session_id: string; readonly attach_expires_at_ms: number } {
  return {
    dive_session_id: diveSessionId,
    attach_expires_at_ms: attachExpiresAtMs
  }
}
