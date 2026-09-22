export type ErrorCode = "unauthorized" | "stale" | "conflict" | "blocked" | "invalid" | "unavailable";
export class HubError extends Error {
  constructor(readonly code: ErrorCode, message: string, readonly current?: unknown) {
    super(message);
  }
}
export function commandError(error: unknown): { code: ErrorCode; message: string; current?: unknown } {
  if (error instanceof HubError) return { code: error.code, message: error.message, current: error.current };
  const message = error instanceof Error ? error.message : String(error);
  const code = /stale|expired/.test(message) ? "stale"
    : /conflict|already registered|UNIQUE constraint/.test(message) ? "conflict"
    : /unavailable|not found:.*capability/.test(message) ? "unavailable"
    : /invalid|required|unsupported|not found|must|mismatch/.test(message) ? "invalid" : "blocked";
  return { code, message };
}
