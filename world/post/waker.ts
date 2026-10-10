// 起こす決まりは1つ：済んでいない手紙がある住人が起きていなければ、起こす。
// 読めたのに進まないときはMasterへ知らせ、届かないときだけ間隔を広げて届け続ける。
// 止まった直後と、起こした直後の rest の間は待つ（すぐ落ちる脳で空回りしないため。起こしてから起きたと分かるまでの間に、2度起こさないため）。

import { activeLimit, afterProgress, attempts, unreachable, type Line, type Tell, type Unfinished, unfinished } from "./letters.ts";

export const toldOf = (lines: Line[]) => new Set(afterProgress(lines).filter((l): l is Tell => l.kind === "tell").map(l => l.letter));

export function toWake(lines: Line[], awake: boolean, now: Date, restMs: number): string[] {
  if (awake) return [];
  if (activeLimit(lines, now)) return [];
  const told = toldOf(lines);
  const pending = unfinished(lines).filter(l => !told.has(l.id));
  if (pending.length === 0) return [];
  const last = lines.findLast(l => l.kind === "wake" || l.kind === "stop");
  const delay = unreachable(lines) ? Math.max(restMs, 15 * 60_000) : restMs;
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
  // 終わらないうちのreadは回数表示には含めても、不可逆なtellの根拠にはしない。
  // 終了後にlimitと判明した目覚めを、すでに通知済みとして凍結させないため。
  const confirmed = attempts(lines).filter(attempt => attempt.completed && attempt.read && !attempt.limited);
  return unfinished(lines).filter(l => !told.has(l.id) &&
    confirmed.filter(attempt => attempt.wake.letters.includes(l.id)).length >= after);
}

/** 何度起こしても済まないこと。Holoへの言付けの手紙にも、拡張アイコンの印にも使う。 */
export function stuckText(letter: Unfinished, lines: Line[]): string {
  const last = lines.findLast(line => line.kind === "note" || line.kind === "done");
  const note = letter.notes.at(-1)?.body.slice(0, 160) ?? "書き残しなし";
  return `住人：${letter.to}。筋：${letter.work ?? "受付"}。手紙：${letter.id}。差出人：${letter.from}。` +
    `届き直し：${letter.deliveries}回。最後の進捗：${last?.ts ?? "なし"}。最後のnote：「${note}」。`;
}

/** 起こすときの一言。道具の名前まで言う（言わないと、軽い脳は道具を使わずに終わることがあった。2026-10-04のB0）。
 *  手紙をどう扱うかは郵便の決まりが正本なので、ここでは繰り返さない。 */
export const WAKE_TEXT = "郵便局から手紙が届いてるよ！Niraiの read_mailbox を確認してね！";
