// 起こす決まりは1つ：済んでいない手紙がある住人が起きていなければ、起こす。
// 何度起こしても済まずにMasterへ回した手紙（tell の行がある）は、Masterが決めるものなので、それでは起こさない。これが届き直しの上限になる。
// 止まった直後と、起こした直後の rest の間は待つ（すぐ落ちる脳で空回りしないため。起こしてから起きたと分かるまでの間に、2度起こさないため）。

import { activeLimit, type Line, type Tell, type Unfinished, unfinished } from "./letters.ts";

const toldOf = (lines: Line[]) => new Set(lines.filter((l): l is Tell => l.kind === "tell").map(l => l.letter));

export function toWake(lines: Line[], awake: boolean, now: Date, restMs: number): string[] {
  if (awake) return [];
  if (activeLimit(lines, now)) return [];
  const told = toldOf(lines);
  const pending = unfinished(lines).filter(l => !told.has(l.id));
  if (pending.length === 0) return [];
  const last = lines.findLast(l => l.kind === "wake" || l.kind === "stop");
  if (last && now.getTime() - Date.parse(last.ts) < restMs) return [];
  return pending.map(l => l.id);
}

export const POST_OFFICE = "郵便局";

/** Masterへの言付けを伝える住人。Masterと話す窓口で、スマホからも会話が見える。 */
export const MESSENGER = "Holo";

/** 1人の住人の郵便受けで、何度起こしても済まず、まだMasterに知らせていない手紙。
 *  知らせたら、その住人の生ログに tell の行を書く。知らせたかどうかは、その行があるかで決める（帳簿を持たない）。 */
export function toTellMaster(lines: Line[], after: number): Unfinished[] {
  const told = toldOf(lines);
  return unfinished(lines).filter(l => l.deliveries >= after && !told.has(l.id));
}

/** 何度起こしても済まないこと。Holoへの言付けの手紙にも、拡張アイコンの印にも使う。 */
export function stuckText(letter: Unfinished): string {
  return `${letter.to} が、${letter.from} からの手紙（${letter.id}）を ${letter.deliveries} 回起こされても、まだ済ませられていない。` +
    `手紙の書き出し：「${letter.body.slice(0, 120)}」`;
}

/** 起こすときの一言。道具の名前まで言う（言わないと、軽い脳は道具を使わずに終わることがあった。2026-10-04のB0）。 */
export function wakeText(resident: string, count: number): string {
  return `${resident}、郵便局から：手紙が${count}通届いてるよ。Niraiの read_mailbox で郵便受けを見て、手紙のとおりにして。`;
}
