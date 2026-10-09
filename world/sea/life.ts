// 住人の今の暮らし（どこで何をしているか・表情・身振り・眠っているか）を、体の記録と精神の様子から計算する。
// 世界の状態は持たない（計画書§2.5）。描画しなくても、海を起こし直しても、同じ記録からは同じ今になる。
// 時刻に沿った動き（道筋・泳ぐ輪・身振りの長さ）は、ここで決めた「いつから」を使って窓が描く。
import { ACTIVITIES, HOME_ACTIVITY } from '../window/body/activities.js';
import { activityAt } from '../window/body/activity-route.js';
import { APPROACH_DURATION_MS } from './settings.ts';
import type { BodyRecord } from './body.ts';

export type Activity = { name: string; since: string | null };
export type ActivityPath = Activity & {
  from: Activity | null;
  route?: { origin: Activity; changes: Activity[] };
};
export type Life = {
  activity: ActivityPath;
  expression: string | null;
  expressionAt: string | null;
  gesture: { name: string; at: string } | null;
  asleep: boolean;
};

const HOME: Activity = { name: HOME_ACTIVITY, since: null };
const VISIT = '窓辺にいる';

// The oldest known choice is the anchor; keep only later events. At most two
// choices are needed for the current route, so old lifelog files stay unread.
function visitRoute(recordsNewestFirst: BodyRecord[], activities: Activity[]): ActivityPath | null {
  const ordered = recordsNewestFirst.reverse();
  const before = activities[1] ?? HOME;
  const boundary = activities[1] && ordered.findIndex(r =>
    r.kind === 'activity' && r.value === before.name && r.ts === before.since);
  const events = ordered.slice(boundary === undefined || boundary < 0 ? 0 : boundary + 1);
  if (!events.some(r => r.kind === 'approach' && r.value === 'start')) return null;

  const changes: Activity[] = [];
  let chosen = before.name;
  let visiting: { ref: string; until: number } | null = null;
  const add = (name: string, time: number) => changes.push({ name, since: new Date(time).toISOString() });
  const returnFromVisit = (time: number) => {
    if (!visiting) return;
    add(chosen, time);
    visiting = null;
  };
  for (const record of events) {
    const time = Date.parse(record.ts);
    if (visiting && time >= visiting.until) returnFromVisit(visiting.until);
    if (record.kind === 'activity' && Object.hasOwn(ACTIVITIES, record.value)) {
      visiting = null;
      chosen = record.value;
      add(chosen, time);
    } else if (record.kind === 'approach' && record.value === 'start') {
      if (visiting) returnFromVisit(time);
      visiting = { ref: record.ref, until: time + APPROACH_DURATION_MS };
      add(VISIT, time);
    } else if (record.kind === 'approach' && record.value === 'failed'
      && visiting?.ref === record.ref) {
      returnFromVisit(time);
    }
  }
  if (visiting) returnFromVisit(visiting.until);
  return { ...before, from: null, route: { origin: before, changes } };
}

// 記録は新しい順に受け取り、要るものがそろったら読むのをやめる（古い日のファイルは開かない）。
// 今の世界にない活動の名前は飛ばす（活動の一覧が変わっても、古い選択で知らない場所へ行かない）。
export async function lifeOf(newestFirst: AsyncIterable<BodyRecord> | Iterable<BodyRecord>, asleep: boolean): Promise<Life> {
  const activities: Activity[] = [];
  const recent: BodyRecord[] = [];
  let expression: string | null | undefined;
  let expressionAt: string | null = null;
  let gesture: Life['gesture'] | undefined;
  for await (const record of newestFirst) {
    if (record.kind === 'activity' || record.kind === 'approach') recent.push(record);
    if (record.kind === 'activity' && activities.length < 2 && Object.hasOwn(ACTIVITIES, record.value)) {
      activities.push({ name: record.value, since: record.ts });
    } else if (record.kind === 'expression' && expression === undefined) {
      expression = record.value === 'なし' ? null : record.value;
      expressionAt = record.ts;
    } else if (record.kind === 'gesture' && gesture === undefined) {
      gesture = { name: record.value, at: record.ts };
    }
    if (activities.length === 2 && expression !== undefined && gesture !== undefined) break;
  }
  const [now, before] = activities;
  const visits = visitRoute(recent, activities);
  const activity = visits ? activityAt(visits, Date.now()) : now ? { ...now, from: before ?? HOME } : { ...HOME, from: null };
  return {
    // 初めて選んだ活動へは、居場所から移る。
    activity: visits ? { ...activity, route: visits.route } : activity,
    expression: expression ?? null,
    expressionAt,
    gesture: gesture ?? null,
    asleep,
  };
}
