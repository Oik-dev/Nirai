// 起こす決まりは1つ：済んでいない手紙がある住人が起きていなければ、起こす。
// 止まった直後と、起こした直後の rest の間は待つ（すぐ落ちる脳で空回りしないため。起こしてから起きたと分かるまでの間に、2度起こさないため）。

import { type Line, unfinished } from "./letters.ts";

export function toWake(lines: Line[], awake: boolean, now: Date, restMs: number): string[] {
  if (awake) return [];
  const pending = unfinished(lines);
  if (pending.length === 0) return [];
  const last = lines.findLast(l => l.kind === "wake" || l.kind === "stop");
  if (last && now.getTime() - Date.parse(last.ts) < restMs) return [];
  return pending.map(l => l.id);
}

/** 起こすときの一言。道具の名前まで言う（言わないと、軽い脳は道具を使わずに終わることがあった。2026-10-04のB0）。 */
export function wakeText(resident: string, count: number): string {
  return `${resident}、郵便局から：手紙が${count}通届いてるよ。Niraiの read_mailbox で郵便受けを見て、手紙のとおりにして。`;
}
