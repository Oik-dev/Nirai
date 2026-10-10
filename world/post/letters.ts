// 手紙は、受取人のイデアの lifelog/post/<日本時間の日付>.jsonl に追記だけで残す。
// 郵便局は帳簿を持たない。済んでいない手紙も、届き直した回数も、席の様子も、毎回この生ログを全部読んで決める
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
  /** 作業場の名前。手紙はかならずどこかの作業場の筋に属する。 */
  work: string;
  reply_to?: string;
  based_on?: string;
  /** Holoの席を閉じてほしいと郵便局が出した手紙。値は席の番号。leave_seat でだけ済む。 */
  close?: number;
};
/** waiting_for：この手紙は、その番号の手紙が済むまで（"Master"ならMasterの返事まで）待つ。 */
export type Note = { kind: "note"; ts: string; letter: string; body: string; waiting_for?: string };
export type Done = { kind: "done"; ts: string; letter: string; note?: string };
/** 郵便受けを読めた事実。郵便局が読み取り時に書く（進捗ではない）。 */
export type Read = { kind: "read"; ts: string; work: string; seat?: number };
export type Wake = { kind: "wake"; ts: string; letters: string[]; how: string; work: string; seat?: number };
export type Stop = {
  kind: "stop";
  ts: string;
  work: string;
  seat?: number;
  how: "exit" | "error" | "timeout" | "limit";
  detail?: string;
  /** limit のとき、郵便局が次にこの住人を起こしてよい時刻。 */
  until?: string;
  /** false は、CLIから時刻を読めず、設定の待ち時間を仮に使ったことを表す。 */
  untilKnown?: boolean;
};
/** 何度起こしても済まない手紙（letter）を、Masterに知らせた。how は知らせ方 */
export type Tell = { kind: "tell"; ts: string; letter: string; how: string };
/**
 * Holoの席（ChatGPTの会話1つ）の出来事。Holoの生ログに残し、席の今はこの行だけから決める（seats.ts）。
 * open：席に入った（work があれば作業場の席、なければ入ったばかり）。started は会話が始まった時刻（席を結び直しても会話は続く）
 * bind：入ったばかりの席を作業場に結んだ。url：席の会話のURLが分かった。talk：Masterとの返事が終わった
 * close：席を空けた。結ばれた席なら handover に引き継ぎを残す
 */
export type SeatLine = {
  kind: "seat";
  ts: string;
  seat: number;
  event: "open" | "bind" | "url" | "talk" | "close";
  work?: string;
  url?: string;
  started?: string;
  handover?: string;
};
export type Line = Letter | Note | Done | Read | Wake | Stop | Tell | SeatLine;

export type Unfinished = Letter & { notes: Note[]; deliveries: number };

/** 筋の鍵。Windowsの同じ作業場（workとWORK）を一つの筋として扱う。 */
export const trackKey = (work: string) => work.toLowerCase();

/** 作業場を持たない古い行（v3の受付）が属する筋。新しい手紙には使えない。 */
export const LEGACY_TRACK = "受付";

/** 同じ筋の行だけ。手紙・目覚め・止まり・読んだ事実は筋で、書き残し・済み・知らせは手紙で決まる。 */
export function track(lines: Line[], work: string): Line[] {
  const key = trackKey(work);
  const belongs = new Set(lines.filter((line): line is Letter => line.kind === "letter" && trackKey(line.work) === key).map(l => l.id));
  return lines.filter(line => {
    if (line.kind === "note" || line.kind === "done" || line.kind === "tell") return belongs.has(line.letter);
    if (line.kind === "seat") return false;
    return trackKey(line.work) === key;
  });
}

/** 済んでいない手紙のある筋を、いちばん古い手紙の順に。 */
export function tracksOf(letters: Letter[]): string[] {
  const first = new Map<string, Letter>();
  for (const letter of letters) {
    const key = trackKey(letter.work);
    if (!first.has(key) || letter.ts < first.get(key)!.ts) first.set(key, letter);
  }
  return [...first.values()].sort((a, b) => a.ts.localeCompare(b.ts)).map(letter => letter.work);
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

const KINDS = new Set(["letter", "note", "done", "read", "wake", "stop", "tell", "seat"]);
const TRACKED = new Set(["letter", "read", "wake", "stop"]);

/** 生ログを全部、時刻の順に。古い形の行はここで今の形にそろえる（作業場のない行は受付の筋、部屋の行は読まない）。 */
export function readAll(residentsRoot: string, resident: string): Line[] {
  const dir = postDir(residentsRoot, resident);
  if (!existsSync(dir)) return [];
  const lines: Line[] = [];
  for (const name of readdirSync(dir).filter(n => n.endsWith(".jsonl")).sort()) {
    for (const raw of readFileSync(join(dir, name), "utf8").split("\n")) {
      if (!raw.trim()) continue;
      const line = JSON.parse(raw) as Record<string, unknown>;
      if (!KINDS.has(line.kind as string)) continue;
      if (TRACKED.has(line.kind as string) && !line.work) line.work = LEGACY_TRACK;
      lines.push(line as Line);
    }
  }
  return lines.sort((a, b) => a.ts.localeCompare(b.ts));
}

/**
 * 最後の進捗より後の行。
 * 住人が手紙へのnoteかdoneを書いたら、仕事が進んだと数える。
 * 最後の進捗より後のwakeだけを、届き直しの回数にする。
 */
export function afterProgress(lines: Line[]): Line[] {
  const last = lines.findLastIndex(l => l.kind === "note" || l.kind === "done");
  return last >= 0 ? lines.slice(last + 1) : lines;
}

/** 最後の進捗の後の目覚め。終了未確定と利用上限を区別し、読めた事実はどちらにも残す。 */
export type Attempt = { wake: Wake; read: boolean; completed: boolean; limited: boolean };
export function attempts(lines: Line[]): Attempt[] {
  const found: Attempt[] = [];
  let current: Attempt | undefined;
  for (const line of afterProgress(lines)) {
    if (line.kind === "wake") {
      if (current) found.push({ ...current, completed: true });
      current = { wake: line, read: false, completed: false, limited: false };
    } else if (line.kind === "read" && current) {
      current.read = true;
    } else if (line.kind === "stop" && current) {
      found.push({ ...current, completed: true, limited: line.how === "limit" });
      current = undefined;
    }
  }
  if (current) found.push(current);
  return found;
}

/** 配送そのものが届かなかった目覚めが3回続いた筋。上限でも読めた事実は回復に使う。 */
export function unreachable(lines: Line[]): boolean {
  const wakes = attempts(lines);
  if (wakes.at(-1)?.completed === false && wakes.at(-1)?.read) return false;
  const completed = wakes.filter(attempt => attempt.completed && (!attempt.limited || attempt.read));
  return completed.length >= 3 && completed.slice(-3).every(attempt => !attempt.read);
}

export function unfinished(lines: Line[]): Unfinished[] {
  const done = new Set(lines.filter(l => l.kind === "done").map(l => (l as Done).letter));
  const deliveryWakes = attempts(lines).filter(attempt => attempt.read && !attempt.limited).map(attempt => attempt.wake);
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

/** Masterの返事を待つ印（waiting_for の値）。 */
export const MASTER = "Master";

/**
 * 今待っている手紙 → 待っている相手（手紙の番号か "Master"）。チーム全員の生ログから決める。
 * 待つのは、最後の書き残しに waiting_for があり、その手紙がまだ誰の生ログでも済んでいない間だけ。
 */
export function waits(team: Line[][]): Map<string, string> {
  const done = new Set<string>();
  const last = new Map<string, Note>();
  for (const lines of team) for (const line of lines) {
    if (line.kind === "done") done.add(line.letter);
    else if (line.kind === "note" && (!last.has(line.letter) || line.ts >= last.get(line.letter)!.ts)) last.set(line.letter, line);
  }
  const found = new Map<string, string>();
  for (const [letter, note] of last) {
    if (done.has(letter) || !note.waiting_for) continue;
    if (note.waiting_for === MASTER || !done.has(note.waiting_for)) found.set(letter, note.waiting_for);
  }
  return found;
}

/** waiting_for に書けない理由。書けるなら undefined。 */
export function waitRefusal(team: Line[][], letter: string, target: string): string | undefined {
  if (target === MASTER) return undefined;
  const known = team.some(lines => lines.some(line => line.kind === "letter" && line.id === target));
  if (!known) return `${target} という手紙はない。待つ手紙は、先に send_letter で出す。`;
  if (team.some(lines => lines.some(line => line.kind === "done" && line.letter === target))) return `${target} はもう済んでいる。待たずに続けられる。`;
  const current = waits(team);
  for (let at: string | undefined = target, steps = 0; at && steps <= current.size; at = current.get(at), steps++) {
    if (at === letter) return `${target} を待つと、待ちが輪になる（${target} は巡って ${letter} を待っている）。`;
  }
  return undefined;
}
