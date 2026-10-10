// 郵便受けの見え方。状態の正本は増やさず、生ログと「今起きているか」から、その場で表示用に組み立てる。

import { activeLimit, track, tracksOf, unreachable, type Line, unfinished } from "./letters.ts";
import { toldOf } from "./waker.ts";

export type ResidentPostState = "idle" | "working" | "queued" | "waiting" | "stuck" | "unreachable" | "limited";

export type ResidentPostStatus = {
  name: string;
  state: ResidentPostState;
  unfinished: number;
  waiting: number;
  stuck: number;
  unreachable: number;
  limitUntil?: string;
  limitKnown?: boolean;
};

/**
 * 表示用の状態。
 * - stuck: 読んでも進まずMasterへ知らせた手紙
 * - unreachable: 3回続けて読まれなかった筋
 * - working: 脳が起きている
 * - queued: 起こせる未済手紙があり、次の起床を待っている
 * - waiting: 未済手紙は全部、ほかの手紙かMasterを待っている
 * - idle: 未済手紙がない
 */
export function residentPostStatus(
  name: string, lines: Line[], awake: boolean, now: Date = new Date(), waits: ReadonlyMap<string, string> = new Map(),
): ResidentPostStatus {
  const open = unfinished(lines);
  const works = tracksOf(open);
  const stuck = works.reduce((total, work) => {
    const scoped = track(lines, work);
    const told = toldOf(scoped);
    return total + unfinished(scoped).filter(letter => told.has(letter.id)).length;
  }, 0);
  const unreachableCount = works.filter(work => unreachable(track(lines, work))).length;
  const waiting = open.filter(letter => waits.has(letter.id)).length;
  const limit = activeLimit(lines, now);
  const state: ResidentPostState = stuck > 0 ? "stuck" : unreachableCount > 0 ? "unreachable" : awake ? "working" : limit ? "limited"
    : open.length > waiting ? "queued" : open.length > 0 ? "waiting" : "idle";
  return {
    name, state, unfinished: open.length, waiting, stuck, unreachable: unreachableCount,
    ...(limit?.until ? { limitUntil: limit.until, limitKnown: limit.untilKnown !== false } : {}),
  };
}
