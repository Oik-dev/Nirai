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
/** 郵便受けを読めた事実。郵便局が読み取り時に書く（進捗ではない）。 */
export type Read = { kind: "read"; ts: string; work?: string };
export type Wake = { kind: "wake"; ts: string; letters: string[]; how: string; work?: string };
export type Stop = {
  kind: "stop";
  ts: string;
  work?: string;
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
export type Room = { kind: "room"; ts: string; url: string; work?: string };
export type Line = Letter | Note | Done | Read | Wake | Stop | Tell | Room;

export type Unfinished = Letter & { notes: Note[]; deliveries: number };

/** Windowsの同じ作業場（workとWORK）を一つの筋として扱う。 */
export const workKey = (name: string) => name.toLowerCase();

/** 作業場がなければ受付。同じ筋の行だけを既存の起床規則へ渡す。 */
export function scopeLines(lines: Line[], work?: string): Line[] {
  const key = workKey(work ?? "");
  const belongs = new Set(lines.filter((line): line is Letter => line.kind === "letter" && workKey(line.work ?? "") === key).map(l => l.id));
  return lines.filter(line => {
    if (line.kind === "letter" || line.kind === "wake" || line.kind === "stop" || line.kind === "read") return workKey(line.work ?? "") === key;
    if (line.kind === "note" || line.kind === "done" || line.kind === "tell") return belongs.has(line.letter);
    return workKey(line.work ?? "") === key;
  });
}

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

/** 最後の進捗の後の目覚め。readを確認し、上限終了の回を除外する。1回の走査で数える。 */
export function attempts(lines: Line[]): { wake: Wake; read: boolean; completed: boolean }[] {
  const found: { wake: Wake; read: boolean; completed: boolean }[] = [];
  let current: { wake: Wake; read: boolean; completed: boolean } | undefined;
  for (const line of afterProgress(lines)) {
    if (line.kind === "wake") {
      if (current) found.push({ ...current, completed: true });
      current = { wake: line, read: false, completed: false };
    } else if (line.kind === "read" && current) {
      current.read = true;
    } else if (line.kind === "stop" && current) {
      if (line.how !== "limit") found.push({ ...current, completed: true });
      current = undefined;
    }
  }
  if (current) found.push(current);
  return found;
}

/** 配送そのものが届かなかった目覚めが3回続いた筋。進行中は前の不通表示を保つ。 */
export function unreachable(lines: Line[]): boolean {
  const wakes = attempts(lines);
  if (wakes.at(-1)?.completed === false && wakes.at(-1)?.read) return false;
  const completed = wakes.filter(attempt => attempt.completed);
  return completed.length >= 3 && completed.slice(-3).every(attempt => !attempt.read);
}

export function unfinished(lines: Line[]): Unfinished[] {
  const done = new Set(lines.filter(l => l.kind === "done").map(l => (l as Done).letter));
  const deliveryWakes = attempts(lines).filter(attempt => attempt.read).map(attempt => attempt.wake);
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
  return stop;
}

export function findLetter(lines: Line[], id: string): Letter | undefined {
  return lines.find((l): l is Letter => l.kind === "letter" && l.id === id);
}
