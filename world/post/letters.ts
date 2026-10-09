// 手紙は、受取人のイデアの lifelog/post/<日本時間の日付>.jsonl に追記だけで残す。
// 郵便局は帳簿を持たない。済んでいない手紙も、届き直した回数も、毎回この生ログを全部読んで決める
// （設計 world/docs/郵便局.md §4「生ログでの形」）。

import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

export type Letter = {
  kind: "letter";
  ts: string;
  id: string;
  from: string;
  to: string;
  body: string;
  work?: string;
  reply_to?: string;
  based_on?: string;
  /** Holoの部屋を引っ越すために郵便局が出した手紙。本文を読まずに見分ける印。 */
  move?: true;
};
export type Note = { kind: "note"; ts: string; letter: string; body: string };
export type Done = { kind: "done"; ts: string; letter: string; note?: string };
export type Wake = { kind: "wake"; ts: string; letters: string[]; how: string };
export type Stop = {
  kind: "stop";
  ts: string;
  how: "exit" | "error" | "timeout" | "limit";
  detail?: string;
  /** limit のとき、郵便局が次にこの住人を起こしてよい時刻。 */
  until?: string;
  /** false は、CLIから時刻を読めず、設定の待ち時間を仮に使ったことを表す。 */
  untilKnown?: boolean;
};
/** 何度起こしても済まない手紙（letter）を、Masterに知らせた。how は知らせ方 */
export type Tell = { kind: "tell"; ts: string; letter: string; how: string };
/** Holoが今使うChatGPTの部屋。最後のroom行だけが現在の部屋。 */
export type Room = { kind: "room"; ts: string; url: string };
export type Line = Letter | Note | Done | Wake | Stop | Tell | Room;

export type Unfinished = Letter & { notes: Note[]; deliveries: number };

export const JST_DAY = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Tokyo" });

export function postDir(residentsRoot: string, resident: string): string {
  return join(residentsRoot, resident, "lifelog", "post");
}

export function newLetterId(now: Date = new Date()): string {
  const stamp = now.toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
  return `L${stamp}-${randomBytes(3).toString("hex")}`;
}

export function append(residentsRoot: string, resident: string, line: Line): void {
  const dir = postDir(residentsRoot, resident);
  mkdirSync(dir, { recursive: true });
  const day = JST_DAY.format(new Date(line.ts));
  appendFileSync(join(dir, `${day}.jsonl`), JSON.stringify(line) + "\n", "utf8");
}

export function readAll(residentsRoot: string, resident: string): Line[] {
  const dir = postDir(residentsRoot, resident);
  if (!existsSync(dir)) return [];
  const lines: Line[] = [];
  for (const name of readdirSync(dir).filter(n => n.endsWith(".jsonl")).sort()) {
    for (const raw of readFileSync(join(dir, name), "utf8").split("\n")) {
      if (raw.trim()) lines.push(JSON.parse(raw) as Line);
    }
  }
  return lines.sort((a, b) => a.ts.localeCompare(b.ts));
}

/**
 * 済んでいない手紙を、届いた順に。
 * 住人が手紙へのnoteかdoneを書いたら、仕事が進んだと数える。
 * 最後の進捗より後のwakeだけを、届き直しの回数にする。
 */
export function afterProgress(lines: Line[]): Line[] {
  const last = lines.findLastIndex(l => l.kind === "note" || l.kind === "done");
  return last >= 0 ? lines.slice(last + 1) : lines;
}

export function unfinished(lines: Line[]): Unfinished[] {
  const done = new Set(lines.filter(l => l.kind === "done").map(l => (l as Done).letter));
  const sinceProgress = afterProgress(lines);
  const deliveryWakes = sinceProgress.filter((line, index): line is Wake => {
    if (line.kind !== "wake") return false;
    const nextEnd = sinceProgress.slice(index + 1).find(next => next.kind === "wake" || next.kind === "stop");
    return !(nextEnd?.kind === "stop" && nextEnd.how === "limit");
  });
  return lines
    .filter((l): l is Letter => l.kind === "letter" && !done.has(l.id))
    .map(letter => ({
      ...letter,
      notes: lines.filter((l): l is Note => l.kind === "note" && l.letter === letter.id),
      deliveries: deliveryWakes.filter(wake => wake.letters.includes(letter.id)).length,
    }));
}

/** 今も効いている、いちばん新しい上限の眠り。 */
export function activeLimit(lines: Line[], now: Date): Stop | undefined {
  const stop = lines.findLast((line): line is Stop => line.kind === "stop" && line.how === "limit" && typeof line.until === "string");
  if (!stop?.until || Date.parse(stop.until) <= now.getTime()) return undefined;
  const index = lines.lastIndexOf(stop);
  if (lines.slice(index + 1).some(line => line.kind === "wake")) return undefined;
  return stop;
}

export function findLetter(lines: Line[], id: string): Letter | undefined {
  return lines.find((l): l is Letter => l.kind === "letter" && l.id === id);
}
