// 郵便受けの見え方。状態の正本は増やさず、生ログと「今起きているか」から、その場で表示用に組み立てる。

import { activeLimit, scopeLines, unreachable, workKey, type Line, unfinished } from "./letters.ts";
import { toldOf } from "./waker.ts";

export type ResidentPostState = "idle" | "working" | "waiting" | "stuck" | "unreachable" | "limited";

export type ResidentPostStatus = {
  name: string;
  state: ResidentPostState;
  unfinished: number;
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
 * - waiting: 未済手紙があり、次の起床を待っている
 * - idle: 未済手紙がない
 */
export function residentPostStatus(name: string, lines: Line[], awake: boolean, now: Date = new Date()): ResidentPostStatus {
  const open = unfinished(lines);
  const works = new Set(open.map(letter => workKey(letter.work ?? "")));
  const stuck = [...works].reduce((total, work) => {
    const scoped = scopeLines(lines, work);
    const told = toldOf(scoped);
    return total + unfinished(scoped).filter(letter => told.has(letter.id)).length;
  }, 0);
  const unreachableCount = [...works].filter(work => unreachable(scopeLines(lines, work))).length;
  const limit = activeLimit(lines, now);
  const state: ResidentPostState = stuck > 0 ? "stuck" : unreachableCount > 0 ? "unreachable" : awake ? "working" : limit ? "limited" : open.length > 0 ? "waiting" : "idle";
  return {
    name, state, unfinished: open.length, stuck, unreachable: unreachableCount,
    ...(limit?.until ? { limitUntil: limit.until, limitKnown: limit.untilKnown !== false } : {}),
  };
}
