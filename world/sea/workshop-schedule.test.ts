import assert from 'node:assert/strict';
import test from 'node:test';
import { WorkshopSchedule, type WorkshopScheduleOptions } from './workshop-schedule.ts';
import { WorkshopDuty } from './workshop.ts';
import { seaSettings } from './settings.ts';

const settings = seaSettings({ NIRAI_RESIDENTS: 'R:/Residents', NIRAI_SOURCE_REPO: 'R:/Nirai' }).workshop;
const morning = () => new Date('2026-10-09T20:00:00.000Z');
const context = {
  resident: { name: 'Test', idea: 'R:/Residents/Test', port: 1234 },
  connected: true, mindAsleep: false,
  wishes: [{ name: '秘密の願い', ref: '1' }],
};

function setup(override: Partial<WorkshopScheduleOptions> = {}) {
  const duty = new WorkshopDuty();
  let performed = 0;
  const options: WorkshopScheduleOptions = {
    settings, duty, now: morning,
    context: async () => context,
    hands: async () => ({ busy: false, away_seconds: 1800 }),
    perform: async () => { performed++; },
    ...override,
  };
  const schedule = new WorkshopSchedule(options);
  return { schedule, duty, performed: () => performed };
}

test('工房の見回りは条件がそろった朝だけ開き、同じ朝は二度開かない', async () => {
  const f = setup();
  await f.schedule.tick();
  await f.duty.stop();
  await f.schedule.tick();
  assert.equal(f.performed(), 1);
  await f.schedule.stop();
  const busy = setup({ hands: async () => ({ busy: true, away_seconds: 4000 }) });
  await busy.schedule.tick();
  assert.equal(busy.performed(), 0);
  await busy.schedule.stop();
  const offline = setup({ context: async () => ({ ...context, connected: false }) });
  await offline.schedule.tick();
  assert.equal(offline.performed(), 0);
  await offline.schedule.stop();
});

test('会話は手元の問い合わせ待ちも止め、会話が終わるまで工房を開かない', async () => {
  let release!: (value: { busy: boolean; away_seconds: number | null }) => void;
  let queried!: () => void;
  const sent = new Promise<void>(resolve => { queried = resolve; });
  const f = setup({ hands: () => {
    queried();
    return new Promise(resolve => { release = resolve; });
  } });
  const tick = f.schedule.tick();
  await sent;
  const pause = f.schedule.pause();
  release({ busy: false, away_seconds: 4000 });
  await Promise.all([pause, tick]);
  assert.equal(f.performed(), 0);
  f.schedule.resume();
  await f.schedule.stop();
});

test('会話中は生成器が終わるまで待ち、工房を同じ朝に再開しない', async () => {
  let release!: () => void;
  let aborted = false;
  const finished = new Promise<void>(resolve => { release = resolve; });
  const f = setup({ perform: async (_ctx, signal) => {
    signal.addEventListener('abort', () => { aborted = true; });
    await finished;
  } });
  await f.schedule.tick();
  let conversation = false;
  const pause = f.schedule.pause().then(() => { conversation = true; });
  await Promise.resolve();
  assert.equal(aborted, true);
  assert.equal(conversation, false);
  release();
  await pause;
  f.schedule.resume();
  await f.schedule.tick();
  assert.equal(f.duty.running, false);
  await f.schedule.stop();
});
