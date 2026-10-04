// 手紙は、受取人のイデアの lifelog/post/<日本時間の日付>.jsonl に追記だけで残す。
// 郵便局は帳簿を持たない。済んでいない手紙も、届き直した回数も、毎回この生ログを全部読んで決める
// （計画 world/docs/plans/仕事のチーム.md §1「生ログでの形」）。

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
};
export type Note = { kind: "note"; ts: string; letter: string; body: string };
export type Done = { kind: "done"; ts: string; letter: string; note?: string };
export type Wake = { kind: "wake"; ts: string; letters: string[]; how: string };
export type Stop = { kind: "stop"; ts: string; how: "exit" | "error" | "timeout"; detail?: string };
/** 何度起こしても済まない手紙（letter）を、Masterに知らせた。how は知らせ方 */
export type Tell = { kind: "tell"; ts: string; letter: string; how: string };
export type Line = Letter | Note | Done | Wake | Stop | Tell;

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

/** 済んでいない手紙を、届いた順に。書き残しと、届き直した回数（その手紙を含む wake の数）を付ける。 */
export function unfinished(lines: Line[]): Unfinished[] {
  const done = new Set(lines.filter(l => l.kind === "done").map(l => (l as Done).letter));
  return lines
    .filter((l): l is Letter => l.kind === "letter" && !done.has(l.id))
    .map(letter => ({
      ...letter,
      notes: lines.filter((l): l is Note => l.kind === "note" && l.letter === letter.id),
      deliveries: lines.filter(l => l.kind === "wake" && (l as Wake).letters.includes(letter.id)).length,
    }));
}

export function findLetter(lines: Line[], id: string): Letter | undefined {
  return lines.find((l): l is Letter => l.kind === "letter" && l.id === id);
}
