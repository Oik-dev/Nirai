// 郵便受けの見え方。状態の正本は増やさず、生ログと「今起きているか」から、その場で表示用に組み立てる。

import { activeLimit, type Line, type Tell, unfinished } from "./letters.ts";

export type ResidentPostState = "idle" | "working" | "waiting" | "stuck" | "limited";

export type ResidentPostStatus = {
  name: string;
  state: ResidentPostState;
  unfinished: number;
  stuck: number;
  limitUntil?: string;
  limitKnown?: boolean;
};

/**
 * 表示用の状態。
 * - stuck: Masterへ知らせ済みで、判断待ちの未済手紙がある
 * - working: 脳が起きている
 * - waiting: 未済手紙があり、次の起床を待っている
 * - idle: 未済手紙がない
 */
export function residentPostStatus(name: string, lines: Line[], awake: boolean, now: Date = new Date()): ResidentPostStatus {
  const open = unfinished(lines);
  const told = new Set(lines.filter((line): line is Tell => line.kind === "tell").map(line => line.letter));
  const stuck = open.filter(letter => told.has(letter.id)).length;
  const limit = activeLimit(lines, now);
  const state: ResidentPostState = stuck > 0 ? "stuck" : awake ? "working" : limit ? "limited" : open.length > 0 ? "waiting" : "idle";
  return {
    name, state, unfinished: open.length, stuck,
    ...(limit?.until ? { limitUntil: limit.until, limitKnown: limit.untilKnown !== false } : {}),
  };
}
