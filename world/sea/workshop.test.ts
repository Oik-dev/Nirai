import assert from 'node:assert/strict';
import test from 'node:test';
import { seaSettings } from './settings.ts';
import { WorkshopDuty, workshopEligible, workshopMorning, type WorkshopConditions } from './workshop.ts';

const settings = seaSettings({ NIRAI_RESIDENTS: 'R:/Residents', NIRAI_SOURCE_REPO: 'R:/Nirai' }).workshop;
const morning = new Date('2026-10-09T20:00:00.000Z'); // 日本時間10月10日5時
const ready: WorkshopConditions = {
  residentReady: true, connected: true, mindAsleep: false, hasWishes: true,
  hands: { busy: false, away_seconds: 1800 },
};

test('工房の既定値と日本時間の朝を確認する', () => {
  assert.deepEqual([settings.startHour, settings.endHour, settings.awayMinutes, settings.seeds, settings.freeMemoryWaitSeconds], [4, 10, 30, 4, 180]);
  assert.equal(workshopMorning(morning), '2026-10-10');
  assert.equal(workshopMorning(new Date('2026-10-09T14:59:59Z')), '2026-10-09');
  assert.equal(workshopEligible(new Date('2026-10-09T19:00:00Z'), settings, ready), true); // 日本時間4時
  assert.equal(workshopEligible(new Date('2026-10-10T00:59:59Z'), settings, ready), true); // 日本時間9:59
  assert.equal(workshopEligible(new Date('2026-10-10T01:00:00Z'), settings, ready), false); // 10時
  assert.equal(workshopEligible(new Date('2026-10-09T18:59:59Z'), settings, ready), false); // 3:59
});

test('工房は条件が1つでも欠ければ開かず、開いた朝はもう開かない', () => {
  assert.equal(workshopEligible(morning, settings, ready), true);
  const absent: WorkshopConditions[] = [
    { ...ready, residentReady: false },
    { ...ready, connected: false },
    { ...ready, mindAsleep: true },
    { ...ready, hasWishes: false },
    { ...ready, lastOpenedMorning: '2026-10-10' },
    { ...ready, hands: null },
    { ...ready, hands: { busy: true, away_seconds: 5000 } },
    { ...ready, hands: { busy: false, away_seconds: null } },
    { ...ready, hands: { busy: false, away_seconds: 1799 } },
    { ...ready, hands: { busy: false, away_seconds: Number.NaN } },
  ];
  for (const state of absent) assert.equal(workshopEligible(morning, settings, state), false);
});

test('Masterとの会話は工房を止めてから進み、同じ朝に開き直さない', async () => {
  const duty = new WorkshopDuty();
  let release!: () => void;
  let aborted = false;
  const finished = new Promise<void>(resolve => { release = resolve; });
  const perform = async (signal: AbortSignal) => {
    signal.addEventListener('abort', () => { aborted = true; }, { once: true });
    await finished; // finallyの子プロセス片付けに相当する非同期処理
  };
  assert.equal(duty.open(morning, settings, ready, perform), true);
  assert.equal(duty.running, true);
  assert.equal(duty.open(morning, settings, ready, perform), false);
  let conversation = false;
  const chatting = duty.stop().then(() => { conversation = true; });
  await Promise.resolve();
  assert.equal(aborted, true);
  assert.equal(conversation, false);
  release();
  await chatting;
  assert.equal(conversation, true);
  assert.equal(duty.running, false);
  assert.equal(duty.open(morning, settings, ready, perform), false);
  assert.equal(duty.open(new Date('2026-10-10T20:00:00Z'), settings, ready, perform), true);
  await duty.stop();
});

test('工房の実行が失敗しても、その朝に再試行せず、停止は安全に完了する', async () => {
  const duty = new WorkshopDuty();
  assert.equal(duty.open(morning, settings, ready, async () => { throw new Error('private wish'); }), true);
  await duty.stop();
  assert.equal(duty.running, false);
  assert.equal(duty.open(morning, settings, ready, async () => {}), false);
});
