// 住人の今の暮らし（どこで何をしているか・表情・身振り・眠っているか）を、体の記録と精神の様子から計算する。
// 世界の状態は持たない（計画書§2.5）。描画しなくても、海を起こし直しても、同じ記録からは同じ今になる。
// 時刻に沿った動き（道筋・泳ぐ輪・身振りの長さ）は、ここで決めた「いつから」を使って窓が描く。
import { ACTIVITIES, HOME_ACTIVITY } from '../window/body/activities.js';
import { activityAt } from '../window/body/activity-route.js';
import { APPROACH_DURATION_MS } from './settings.ts';
import { CAMERA_CENTER, ENCLOSURE_RADIUS, BODY_MARGIN } from '../window/sea/habitat.js';
import type { BodyCatalog, BodyRecord } from './body.ts';
import type { ResidentPostStatus } from '../post/status.ts';
import type { ResidentMarks } from './post-status.ts';

export type Activity = { name: string; since: string | null; at?: { x: number; y: number; z: number } };
export type ActivityPath = Activity & {
  from: Activity | null;
  route?: { origin: Activity; changes: Activity[] };
};
export type Life = {
  activity: ActivityPath;
  expression: string | null;
  expressionAt: string | null;
  gesture: { name: string; at: string } | null;
  appearance?: Record<string, string>;
  asleep: boolean;
  post?: ResidentMarks;
};

const PERIOD = 17 * 60_000;
const SWIM_STEP = 45_000;
const FREE_ACTIVITIES = Object.keys(ACTIVITIES).filter(name => name !== '窓辺にいる');

// 疑似乱数も区切りも人と時刻だけから決まる。保存する乱数状態はない。
function hash(input: string): number {
  let value = 2166136261;
  for (const code of input) value = Math.imul(value ^ code.codePointAt(0)!, 16777619);
  value ^= value >>> 16;
  value = Math.imul(value, 0x7feb352d);
  value ^= value >>> 15;
  value = Math.imul(value, 0x846ca68b);
  return (value ^ value >>> 16) >>> 0;
}

export function pastimePeriod(name: string, now: number) {
  const phase = hash(name) % PERIOD;
  const index = Math.floor((now - phase) / PERIOD);
  return { index, since: phase + index * PERIOD };
}

function pointFor(name: string, period: number, place: string, step = 0) {
  const unit = (suffix: string) => hash(`${name}:${period}:${step}:${suffix}`) / 0x1_0000_0000;
  const radius = (ENCLOSURE_RADIUS - BODY_MARGIN) * Math.sqrt(unit('radius'));
  const theta = unit('theta') * Math.PI * 2;
  let x = CAMERA_CENTER.x + radius * Math.cos(theta);
  let z = CAMERA_CENTER.z + radius * Math.sin(theta);
  if (place === '砂地で眠る') {
    // 家のカメラから見た奥だけ。Serinaの固定の砂地とも衝突しない配置は窓の責務。
    const r = (ENCLOSURE_RADIUS - 1) * Math.sqrt(unit('sleep-radius'));
    const a = Math.PI + unit('sleep-angle') * Math.PI;
    x = CAMERA_CENTER.x + r * Math.cos(a);
    z = CAMERA_CENTER.z + r * Math.sin(a);
  }
  const y = place === '水面の近く' ? 2.7 + .25 * unit('height')
    : place === '海の中' ? 1.2 + 1.2 * unit('height') : .85;
  return { x, y, z };
}

function activityFor(name: string, index: number, since: number, pastimes: string[], now: number): Activity {
  const choices = pastimes.length ? pastimes : FREE_ACTIVITIES;
  const chosen = choices[hash(`${name}:activity:${index}`) % choices.length];
  const place = ACTIVITIES[chosen as keyof typeof ACTIVITIES].place;
  // 考え始めた時刻は記録しない。awake で経路を変えると今いる点が跳ぶので、
  // 泳ぐ点と移動の時刻は仕事中も休み中も同じ暦から決める。
  const step = place === '海の中' ? Math.max(0, Math.floor((now - since) / SWIM_STEP)) : 0;
  return { name: chosen, since: new Date(since + step * SWIM_STEP).toISOString(),
    at: pointFor(name, index, place, step) };
}

// まだ郵便局から一度も届いていなければ、未知の稼働を眠りとして扱う。
// 精神のない住人は自分の暦で行き先を決める。精神を持つSerinaには一切使わない。
export function lifeWithoutMind(status?: Partial<Pick<ResidentPostStatus, 'limitUntil' | 'awake'>>, now = Date.now(),
  options?: { name: string; pastimes?: string[] }): Life {
  const limited = !!status?.limitUntil && Date.parse(status.limitUntil) > now;
  const name = options?.name;
  let activity: ActivityPath = { name: limited ? '砂地で休む' : HOME_ACTIVITY, since: null, from: null };
  if (name) {
    const preferences = options?.pastimes?.filter(choice => choice !== '窓辺にいる' && Object.hasOwn(ACTIVITIES, choice)) ?? [];
    if (limited) {
      activity = { name: '砂地で休む', since: null, from: null,
        at: pointFor(name, 0, '砂地で眠る') };
    } else if (status) {
      const period = pastimePeriod(name, now);
      const prev = activityFor(name, period.index - 1, period.since - PERIOD, preferences,
        period.since - 1);
      const current = activityFor(name, period.index, period.since, preferences, period.since);
      const changes: Activity[] = [current];
      if (ACTIVITIES[current.name as keyof typeof ACTIVITIES].place === '海の中') {
        for (let offset = SWIM_STEP; offset < PERIOD; offset += SWIM_STEP) {
          changes.push(activityFor(name, period.index, period.since, preferences, period.since + offset));
        }
      }
      activity = { ...current, from: prev, route: { origin: prev, changes } };
    }
  }
  return {
    activity,
    expression: null, expressionAt: null, gesture: null, asleep: !status || limited,
  };
}

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
export async function lifeOf(newestFirst: AsyncIterable<BodyRecord> | Iterable<BodyRecord>, asleep: boolean,
  catalog?: BodyCatalog): Promise<Life> {
  const activities: Activity[] = [];
  const recent: BodyRecord[] = [];
  const available = new Map((catalog?.appearance ?? []).map(item => [item.name, item.options]));
  const seenAppearance = new Set<string>();
  const appearance: Record<string, string> = {};
  let expression: string | null | undefined;
  let expressionAt: string | null = null;
  let gesture: Life['gesture'] | undefined;
  for await (const record of newestFirst) {
    if (record.kind === 'activity' || record.kind === 'approach') recent.push(record);
    if (record.kind === 'appearance' && record.control && available.has(record.control)
      && !seenAppearance.has(record.control)) {
      seenAppearance.add(record.control);
      if (available.get(record.control)?.includes(record.value)) appearance[record.control] = record.value;
    }
    if (record.kind === 'activity' && activities.length < 2 && Object.hasOwn(ACTIVITIES, record.value)) {
      activities.push({ name: record.value, since: record.ts });
    } else if (record.kind === 'expression' && expression === undefined) {
      expression = record.value === 'なし' ? null : record.value;
      expressionAt = record.ts;
    } else if (record.kind === 'gesture' && gesture === undefined) {
      gesture = { name: record.value, at: record.ts };
    }
    if (activities.length === 2 && expression !== undefined && gesture !== undefined
      && seenAppearance.size === available.size) break;
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
    ...(catalog ? { appearance } : {}),
  };
}
