// 郵便局の状態だけを読む。本文を読まず、止まっている間は最後に読めた状態を使う。
import { postPort } from '../post/settings.ts';

export type ResidentMarks = {
  awake: boolean;
  unread: number;
  stuck: boolean;
  limitUntil?: string;
  limitKnown?: boolean;
};

function validMarks(value: unknown): ResidentMarks | undefined {
  if (!value || typeof value !== 'object') return;
  const row = value as Record<string, unknown>;
  if (typeof row.awake !== 'boolean' ||
      typeof row.unread !== 'number' || !Number.isSafeInteger(row.unread) || row.unread < 0 ||
      typeof row.stuck !== 'number' || !Number.isSafeInteger(row.stuck) || row.stuck < 0 ||
      typeof row.unreachable !== 'number' || !Number.isSafeInteger(row.unreachable) || row.unreachable < 0) return;
  const limitUntil = typeof row.limitUntil === 'string' && Number.isFinite(Date.parse(row.limitUntil))
    ? row.limitUntil : undefined;
  return {
    awake: row.awake, unread: row.unread,
    stuck: row.stuck + row.unreachable > 0,
    ...(limitUntil ? { limitUntil, limitKnown: row.limitKnown !== false } : {}),
  };
}

export class PostStatusCache {
  private latest = new Map<string, ResidentMarks>();
  private received = false;

  get(name: string): ResidentMarks | undefined {
    return this.received ? this.latest.get(name) : undefined;
  }

  async refresh(port = postPort()): Promise<boolean> {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/holo/residents`, {
        signal: AbortSignal.timeout(800),
      });
      if (!response.ok) return false;
      const payload: unknown = await response.json();
      if (!payload || typeof payload !== 'object') return false;
      const residents = (payload as { residents?: unknown }).residents;
      if (!Array.isArray(residents)) return false;
      const fresh = new Map<string, ResidentMarks>();
      for (const item of residents) {
        if (!item || typeof item !== 'object' || typeof item.name !== 'string') continue;
        const marks = validMarks(item);
        if (marks) fresh.set(item.name, marks);
      }
      const changed = !this.received ||
        JSON.stringify([...this.latest]) !== JSON.stringify([...fresh]);
      this.latest = fresh;
      this.received = true;
      return changed;
    } catch {
      return false; // 郵便局に届かなくても、海は止めない。
    }
  }
}
