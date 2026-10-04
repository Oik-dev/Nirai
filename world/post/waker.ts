// 起こす決まりは1つ：済んでいない手紙がある住人が起きていなければ、起こす。
// 止まった直後と、起こした直後の rest の間は待つ（すぐ落ちる脳で空回りしないため。起こしてから起きたと分かるまでの間に、2度起こさないため）。

import { type Line, type Unfinished, unfinished } from "./letters.ts";

export function toWake(lines: Line[], awake: boolean, now: Date, restMs: number): string[] {
  if (awake) return [];
  const pending = unfinished(lines);
  if (pending.length === 0) return [];
  const last = lines.findLast(l => l.kind === "wake" || l.kind === "stop");
  if (last && now.getTime() - Date.parse(last.ts) < restMs) return [];
  return pending.map(l => l.id);
}

export const POST_OFFICE = "郵便局";

/** 何度起こしても済まない手紙。Holoに頼んでMasterに知らせる。知らせたかどうかは、
 *  Holoの生ログに、その手紙を based_on にした郵便局からの手紙があるかで決める（帳簿を持たない）。 */
export function toTellMaster(linesOf: Record<string, Line[]>, after: number): Unfinished[] {
  const told = new Set(
    (linesOf.Holo ?? []).filter(l => l.kind === "letter" && l.from === POST_OFFICE && l.based_on).map(l => (l as { based_on: string }).based_on),
  );
  return Object.values(linesOf).flatMap(lines => unfinished(lines)).filter(l => l.deliveries >= after && !told.has(l.id));
}

export function tellMasterText(letter: Unfinished): string {
  return `Masterに伝えて：${letter.to} が、${letter.from} からの手紙（${letter.id}）を ${letter.deliveries} 回起こされても、まだ済ませられていない。` +
    `手紙の書き出し：「${letter.body.slice(0, 120)}」。どうするかはMasterに決めてもらって。`;
}

/** 起こすときの一言。道具の名前まで言う（言わないと、軽い脳は道具を使わずに終わることがあった。2026-10-04のB0）。 */
export function wakeText(resident: string, count: number): string {
  return `${resident}、郵便局から：手紙が${count}通届いてるよ。Niraiの read_mailbox で郵便受けを見て、手紙のとおりにして。`;
}
