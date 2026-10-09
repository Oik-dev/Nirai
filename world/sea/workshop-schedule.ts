// 朝の工房を1分ごとに調べる。願いの名前は海の記録にしかなく、
// この見回りは秘密をログにも子プロセスの引数にも渡さない。
import type { SeaEvents } from './events.ts';
import type { SeaSettings } from './settings.ts';
import type { WorkshopHands } from './workshop-mind.ts';
import { WorkshopDuty } from './workshop.ts';

type Context = Awaited<ReturnType<SeaEvents['workshopContext']>>;

export type WorkshopScheduleOptions = {
  settings: SeaSettings['workshop'];
  duty: WorkshopDuty;
  context: () => Promise<Context>;
  hands: (port: number, signal: AbortSignal) => Promise<WorkshopHands | null>;
  perform: (context: Context, signal: AbortSignal) => Promise<void>;
  now?: () => Date;
  intervalMs?: number;
};

export class WorkshopSchedule {
  private readonly options: WorkshopScheduleOptions;
  private timer?: ReturnType<typeof setInterval>;
  private checking?: Promise<void>;
  private probe?: AbortController;
  private paused = false;
  private stopped = false;

  constructor(options: WorkshopScheduleOptions) { this.options = options; }

  start() {
    if (this.timer || this.stopped) return;
    const interval = this.options.intervalMs ?? 60_000;
    this.timer = setInterval(() => { void this.tick(); }, interval);
    this.timer.unref();
    void this.tick();
  }

  tick(): Promise<void> {
    if (this.stopped || this.paused || this.options.duty.running) return Promise.resolve();
    if (this.checking) return this.checking;
    const probe = new AbortController();
    this.probe = probe;
    const task = (async () => {
      const now = this.options.now ?? (() => new Date());
      const hour = new Date(now().getTime() + 9 * 60 * 60_000).getUTCHours();
      if (hour < this.options.settings.startHour || hour >= this.options.settings.endHour) return;
      const context = await this.options.context();
      if (probe.signal.aborted || !context.resident || !context.connected
        || context.mindAsleep || !context.wishes.length) return;
      const hands = await this.options.hands(context.resident.port, probe.signal);
      if (probe.signal.aborted || this.stopped || this.paused) return;
      // 手元の問い合わせ中に精神のSSEが切れたり、住人が替わった場合は
      // 最初の状態を正本として使わない。脳を譲る直前にもう一度確認する。
      const latest = await this.options.context();
      if (probe.signal.aborted || this.stopped || this.paused
        || !latest.resident || !latest.connected || latest.mindAsleep || !latest.wishes.length
        || latest.resident.idea !== context.resident.idea
        || latest.resident.port !== context.resident.port) return;
      this.options.duty.open(now(), this.options.settings, {
        residentReady: true,
        connected: latest.connected,
        mindAsleep: latest.mindAsleep,
        hasWishes: latest.wishes.length > 0,
        hands,
      }, signal => this.options.perform(latest, signal));
    })().catch(() => undefined).finally(() => {
      if (this.probe === probe) this.probe = undefined;
      this.checking = undefined;
    });
    this.checking = task;
    return task;
  }

  // Masterの会話が先に来たら、問い合わせ中の工房も開かせない。
  async pause(): Promise<void> {
    this.paused = true;
    this.probe?.abort();
    await this.checking;
    await this.options.duty.stop();
  }

  resume() {
    if (!this.stopped) this.paused = false;
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = undefined;
    await this.pause();
  }
}
