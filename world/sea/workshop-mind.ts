// 願いの英語化とMasterの手元の確認は精神にだけ聞く。
// 願いの名前はHTTP本文だけで渡し、URL・エラー・海のログには残さない。
import { MIND_HOST } from './settings.ts';
import type { WorkshopDescribe } from './workshop-flow.ts';

export type WorkshopHands = { busy: boolean; away_seconds: number | null };

export async function workshopHands(port: number, signal: AbortSignal): Promise<WorkshopHands | null> {
  try {
    const response = await fetch(`http://${MIND_HOST}:${port}/api/hands`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(3000)]),
    });
    if (!response.ok) return null;
    const result: unknown = await response.json();
    if (!result || typeof result !== 'object') return null;
    const value = result as Record<string, unknown>;
    if (typeof value.busy !== 'boolean') return null;
    if (value.away_seconds !== null && (typeof value.away_seconds !== 'number'
      || !Number.isFinite(value.away_seconds) || value.away_seconds < 0)) return null;
    return { busy: value.busy, away_seconds: value.away_seconds as number | null };
  } catch { return null; }
}

export async function describeWorkshopWish(port: number, name: string,
  signal: AbortSignal): Promise<WorkshopDescribe> {
  if (signal.aborted) return { kind: 'unavailable' };
  try {
    const response = await fetch(`http://${MIND_HOST}:${port}/api/motion/describe`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ wish: name }),
      signal: AbortSignal.any([signal, AbortSignal.timeout(120_000)]),
    });
    if (response.status === 502) return { kind: 'invalid' };
    if (response.status !== 200) return { kind: 'unavailable' };
    const result: unknown = await response.json();
    if (!result || typeof result !== 'object') return { kind: 'invalid' };
    const value = result as Record<string, unknown>;
    if (typeof value.text !== 'string' || value.text.length < 1 || value.text.length > 200
      || !/^a person\b/.test(value.text) || !Number.isInteger(value.seconds)
      || (value.seconds as number) < 1 || (value.seconds as number) > 10) {
      return { kind: 'invalid' };
    }
    return { kind: 'ready', description: { text: value.text, seconds: value.seconds as number } };
  } catch { return { kind: 'unavailable' }; }
}
