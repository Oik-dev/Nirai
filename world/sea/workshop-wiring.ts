// 海にある唯一のイベント状態を工房の見回りと作業に渡す。
// Chromeの関門だけは別工程の確定まで入口から受け取る。
import type { SeaEvents } from './events.ts';
import type { SeaSettings } from './settings.ts';
import { workshopHands } from './workshop-mind.ts';
import { createWorkshopRun, type WorkshopRunOptions } from './workshop-run.ts';
import { WorkshopSchedule } from './workshop-schedule.ts';
import { WorkshopDuty } from './workshop.ts';

export function createWorkshopSchedule(settings: SeaSettings, events: SeaEvents,
  duty: WorkshopDuty, options: WorkshopRunOptions & { intervalMs?: number }) {
  return new WorkshopSchedule({
    settings: settings.workshop,
    duty,
    context: () => events.workshopContext(),
    hands: workshopHands,
    perform: createWorkshopRun(settings, options),
    now: options.now,
    intervalMs: options.intervalMs,
  });
}
