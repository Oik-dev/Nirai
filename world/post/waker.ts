// 起こす決まりは1つ：済んでいない手紙がある住人が起きていなければ、起こす。
// 他人からの依頼を何度起こしても済ませられなければMasterに知らせる。
// 自分宛ての継続タスクはMasterの判断ではないので、tellの履歴があっても再開できる。
// 止まった直後と、起こした直後の rest の間は待つ（すぐ落ちる脳で空回りしないため。起こしてから起きたと分かるまでの間に、2度起こさないため）。

import { activeLimit, type Line, type Tell, type Unfinished, unfinished } from "./letters.ts";

const toldOf = (lines: Line[]) => new Set(lines.filter((l): l is Tell => l.kind === "tell").map(l => l.letter));

export function toWake(lines: Line[], awake: boolean, now: Date, restMs: number): string[] {
  if (awake) return [];
  if (activeLimit(lines, now)) return [];
  const told = toldOf(lines);
  const pending = unfinished(lines).filter(l => l.from === l.to || !told.has(l.id));
  if (pending.length === 0) return [];
  const last = lines.findLast(l => l.kind === "wake" || l.kind === "stop");
  // 長い自分宛ての仕事は、3度起きてもMasterへ渡さない。
  // 同じ原因で空起床を繰り返さないよう、続きの起床間隔だけ広げる。
  const onlyLongRunning = pending.every(l => l.from === l.to && l.deliveries >= 3);
  const delay = onlyLongRunning ? Math.max(restMs, 15 * 60_000) : restMs;
  if (last && now.getTime() - Date.parse(last.ts) < delay) return [];
  return pending.map(l => l.id);
}

export const POST_OFFICE = "郵便局";

/** Masterへの言付けを伝える住人。Masterと話す窓口で、スマホからも会話が見える。 */
export const MESSENGER = "Holo";

/** 1人の住人の郵便受けで、何度起こしても済まず、まだMasterに知らせていない手紙。
 *  知らせたら、その住人の生ログに tell の行を書く。知らせたかどうかは、その行があるかで決める（帳簿を持たない）。 */
export function toTellMaster(lines: Line[], after: number): Unfinished[] {
  const told = toldOf(lines);
  return unfinished(lines).filter(l => l.from !== l.to && l.deliveries >= after && !told.has(l.id));
}

/** 何度起こしても済まないこと。Holoへの言付けの手紙にも、拡張アイコンの印にも使う。 */
export function stuckText(letter: Unfinished): string {
  return `${letter.to} が、${letter.from} からの手紙（${letter.id}）を ${letter.deliveries} 回起こされても、まだ済ませられていない。` +
    `手紙の書き出し：「${letter.body.slice(0, 120)}」`;
}

/** 起こすときの一言。道具の名前まで言う（言わないと、軽い脳は道具を使わずに終わることがあった。2026-10-04のB0）。
 *  手紙をどう扱うかは郵便の決まりが正本なので、ここでは繰り返さない。 */
export const WAKE_TEXT = "郵便局から手紙が届いてるよ！Niraiの read_mailbox を確認してね！";
