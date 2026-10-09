import assert from 'node:assert/strict';
import test from 'node:test';
import { seaSettings } from './settings.ts';
import { workshopEligible, workshopMorning, type WorkshopConditions } from './workshop.ts';

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
