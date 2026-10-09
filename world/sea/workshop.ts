import type { SeaSettings } from './settings.ts';

type WorkshopSettings = SeaSettings['workshop'];

export type WorkshopConditions = {
  residentReady: boolean;
  connected: boolean;
  mindAsleep: boolean;
  hasWishes: boolean;
  lastOpenedMorning?: string;
  hands: { busy: boolean; away_seconds: number | null } | null;
};

// 日本時間は通年UTC+9（夏時間なし）。日付をまたぐUTC時刻もここで一意に決める。
export function workshopMorning(now: Date): string {
  return new Date(now.getTime() + 9 * 60 * 60_000).toISOString().slice(0, 10);
}

export function workshopEligible(now: Date, settings: WorkshopSettings, state: WorkshopConditions): boolean {
  const hour = new Date(now.getTime() + 9 * 60 * 60_000).getUTCHours();
  return hour >= settings.startHour && hour < settings.endHour
    && state.lastOpenedMorning !== workshopMorning(now)
    && state.residentReady && state.connected && !state.mindAsleep && state.hasWishes
    && state.hands !== null && !state.hands.busy
    && state.hands.away_seconds !== null
    && Number.isFinite(state.hands.away_seconds)
    && state.hands.away_seconds >= settings.awayMinutes * 60;
}
