// 住人の今の暮らし（どこで何をしているか・表情・身振り・眠っているか）を、体の記録と精神の様子から計算する。
// 世界の状態は持たない（計画書§2.5）。描画しなくても、海を起こし直しても、同じ記録からは同じ今になる。
// 時刻に沿った動き（道筋・泳ぐ輪・身振りの長さ）は、ここで決めた「いつから」を使って窓が描く。
import { ACTIVITIES, HOME_ACTIVITY } from '../window/body/activities.js';
import type { BodyRecord } from './body.ts';

export type Activity = { name: string; since: string | null };
export type Life = {
  activity: Activity & { from: Activity | null };
  expression: string | null;
  gesture: { name: string; at: string } | null;
  asleep: boolean;
};

const HOME: Activity = { name: HOME_ACTIVITY, since: null };

// 記録は新しい順に受け取り、要るものがそろったら読むのをやめる（古い日のファイルは開かない）。
// 今の世界にない活動の名前は飛ばす（活動の一覧が変わっても、古い選択で知らない場所へ行かない）。
export async function lifeOf(newestFirst: AsyncIterable<BodyRecord> | Iterable<BodyRecord>, asleep: boolean): Promise<Life> {
  const activities: Activity[] = [];
  let expression: string | null | undefined;
  let gesture: Life['gesture'] | undefined;
  for await (const record of newestFirst) {
    if (record.kind === 'activity' && activities.length < 2 && Object.hasOwn(ACTIVITIES, record.value)) {
      activities.push({ name: record.value, since: record.ts });
    } else if (record.kind === 'expression' && expression === undefined) {
      expression = record.value === 'なし' ? null : record.value;
    } else if (record.kind === 'gesture' && gesture === undefined) {
      gesture = { name: record.value, at: record.ts };
    }
    if (activities.length === 2 && expression !== undefined && gesture !== undefined) break;
  }
  const [now, before] = activities;
  return {
    // 初めて選んだ活動へは、居場所から移る。
    activity: now ? { ...now, from: before ?? HOME } : { ...HOME, from: null },
    expression: expression ?? null,
    gesture: gesture ?? null,
    asleep,
  };
}
