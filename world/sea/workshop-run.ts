// 海の工房の一朝。本人の言葉は精神へのHTTP本文とイデアの記録にだけ渡す。
// 実Chrome関門の起こし方は別工程なので、関門は入口で受け取る。
import { join } from 'node:path';
import {
  appendWorkshopResult, installWorkshopMotion, readIdeaAvatar,
} from './body.ts';
import {
  runWorkshopWishes, type WorkshopSession, type WorkshopWish,
} from './workshop-flow.ts';
import { openWorkshopGenerator, type WorkshopGenerator } from './workshop-generator.ts';
import { describeWorkshopWish, workshopHands } from './workshop-mind.ts';
import type { Resident } from './mind.ts';
import type { SeaSettings } from './settings.ts';

export type WorkshopRunContext = {
  resident: Resident | undefined;
  connected: boolean;
  mindAsleep: boolean;
  wishes: WorkshopWish[];
};

export type WorkshopRunGate = {
  check(candidate: Buffer, signal: AbortSignal): Promise<boolean>;
  close(): Promise<void>;
};

export type WorkshopRunOptions = {
  openGate: (avatar: Buffer, signal: AbortSignal) => Promise<WorkshopRunGate>;
  openGenerator?: (settings: SeaSettings, signal: AbortSignal) => Promise<WorkshopGenerator>;
  now?: () => Date;
};

const jstHour = (now: Date) => new Date(now.getTime() + 9 * 60 * 60_000).getUTCHours();

export function createWorkshopRun(settings: SeaSettings, options: WorkshopRunOptions) {
  const now = options.now ?? (() => new Date());
  return async (context: WorkshopRunContext, signal: AbortSignal): Promise<void> => {
    const resident = context.resident;
    if (!resident || !context.connected || context.mindAsleep || !context.wishes.length || signal.aborted) return;
    const canContinue = async () => {
      if (signal.aborted) return false;
      const hour = jstHour(now());
      if (hour < settings.workshop.startHour || hour >= settings.workshop.endHour) return false;
      const hands = await workshopHands(resident.port, signal);
      return !signal.aborted && hands !== null && hands.busy === false
        && hands.away_seconds !== null
        && hands.away_seconds >= settings.workshop.awayMinutes * 60;
    };

    await runWorkshopWishes(context.wishes, settings.workshop.seeds, {
      describe: wish => describeWorkshopWish(resident.port, wish.name, signal),
      continueWork: canContinue,
      open: async () => {
        // 説明に時間がかかることがある。生成器に脳を譲る直前にもMasterの手元を確認する。
        if (!await canContinue()) throw new Error('workshop unavailable');
        const generator = await (options.openGenerator ?? ((source, abort) =>
          openWorkshopGenerator({
            python: source.workshop.python,
            script: join(source.sourceRepo, 'world', 'workshop', 'generator.py'),
            waitCapacitySeconds: source.workshop.freeMemoryWaitSeconds,
          }, abort)))(settings, signal);
        let gate: WorkshopRunGate | undefined;
        try {
          if (signal.aborted) throw new Error('workshop stopped');
          const avatar = await readIdeaAvatar(resident.idea);
          if (signal.aborted) throw new Error('workshop stopped');
          gate = await options.openGate(avatar.bytes, signal);
          const session: WorkshopSession = {
            generate: (description, seed, abort) => generator.generate(description, seed, abort),
            gate: (candidate, abort) => gate.check(candidate, abort),
            close: async () => {
              // 片方が片付けに失敗しても、もう一方を残さない。
              await Promise.allSettled([gate.close(), generator.close()]);
            },
          };
          if (signal.aborted) throw new Error('workshop stopped');
          return session;
        } catch {
          // 起動途中の中断でも、Chromeと生成器をそれぞれ一度だけ片付ける。
          await Promise.allSettled([gate?.close(), generator.close()]);
          throw new Error('workshop unavailable');
        }
      },
      install: (wish, bytes) => installWorkshopMotion(resident.idea, wish.name, bytes),
      record: (wish, kind) => appendWorkshopResult(resident.idea, {
        kind, value: wish.name, ref: wish.ref,
      }),
    }, signal);
  };
}
