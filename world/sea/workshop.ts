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

// 一度開いた朝は、中止・失敗しても開き直さない。実行が終わる前に
// stop() が戻らないため、Masterの会話を工房の後へ確実に並べられる。
export class WorkshopDuty {
  private openedMorning?: string;
  private controller?: AbortController;
  private active?: Promise<void>;

  get running(): boolean { return this.active !== undefined; }

  open(now: Date, settings: WorkshopSettings, conditions: Omit<WorkshopConditions, 'lastOpenedMorning'>,
    perform: (signal: AbortSignal) => Promise<void>): boolean {
    if (this.active || !workshopEligible(now, settings, { ...conditions, lastOpenedMorning: this.openedMorning })) {
      return false;
    }
    this.openedMorning = workshopMorning(now);
    const controller = new AbortController();
    this.controller = controller;
    const task = (async () => { await perform(controller.signal); })();
    const settled = task.catch(() => undefined).finally(() => {
      if (this.controller === controller) {
        this.controller = undefined;
        this.active = undefined;
      }
    });
    this.active = settled;
    return true;
  }

  async stop(): Promise<void> {
    this.controller?.abort();
    await this.active;
  }
}
