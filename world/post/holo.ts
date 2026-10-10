// Holoの席（ChatGPTの会話）と郵便局のあいだ。拡張が、会話の通信の始まりと終わり、一言を送れたこと、会話のURLを知らせる。
// 席の今は生ログのseat行が正本（seats.ts）で、何をいつ送るかは見回り（plan.ts）が決める。
// ここが覚えるのは、返事の通信が続いている席と、最後に一言を渡した時刻だけ（郵便局が起き直せば消えてよい）。
// 郵便局が起こした返事か、Masterとの返事かは、返事が終わったときに生ログから決める（postalReply）。

import { closeSync, existsSync, openSync, readdirSync, readSync, statSync } from "node:fs";
import { join } from "node:path";
import { append, JST_DAY, readAll, track, trackKey, type Line, MASTER, unfinished } from "./letters.ts";
import { closeLetter, closeSeatLetter, freeSeats, postalReply, type Seat, seatsOf } from "./seats.ts";
import { MESSENGER } from "./waker.ts";
import "../holo-extension/chat-url.js";

/** 会話とProjectのURLの読み方は、拡張と同じchat-url.jsが正本。 */
const chatUrl = (globalThis as typeof globalThis & {
  NiraiChatUrl: {
    parse(raw: string): { url: string; id: string; projectId?: string } | undefined;
    isProjectConversation(raw: string, projectId: string): boolean;
  };
}).NiraiChatUrl;

export type NetReport = {
  phase: "start" | "end" | "error"; id: string; method: string; path: string; status?: number; error?: string;
  /** 席のタブからの通信だけに付く。今の居場所（since）と違えば、閉じた席の古いタブなので読まない */
  seat?: number; since?: string;
};

export type SeatStatus = {
  seat: number;
  state: "empty" | "entered" | "bound";
  since?: string;
  work?: string;
  url?: string;
  /** 返事の最中 */
  awake: boolean;
  /** 待っている手紙と、その相手（手紙の番号か Master） */
  waiting: { letter: string; for: string }[];
  masterWaiting: boolean;
  /** 閉じてほしいと手紙を出した */
  closing: boolean;
  lastNote?: string;
  chars: number;
  limit: number;
  problem?: string;
};

type HoloSettings = { restMs: number; busyLimitMs: number; replyPath: RegExp; seats: number; seatChars: number; projectId: string };

type HandsRow = { ts: string; seat: number; chars: number };

export class HoloSeats {
  private residentsRoot: string;
  private settings: HoloSettings;
  /** 席ごとの、続いている返事の通信（id → 始まった時刻） */
  private inflight = new Map<number, Map<string, number>>();
  /** 拡張へ一言を渡し、送れたかの知らせをまだ受けていない（その間は版替えしない） */
  private handedAt?: number;
  /** 手のログの、読み終えた大きさと字数の行（ファイルごと）。毎回全部を読み直さない */
  private hands = new Map<string, { size: number; rows: HandsRow[] }>();

  constructor(residentsRoot: string, settings: HoloSettings) {
    this.residentsRoot = residentsRoot;
    this.settings = settings;
  }

  private lines(): Line[] {
    return readAll(this.residentsRoot, MESSENGER);
  }

  seats(lines: Line[] = this.lines()): Seat[] {
    return seatsOf(lines, this.settings.seats);
  }

  /** 返事の通信が続いている席。終わりの知らせが途切れても、busyLimitMs を過ぎたら止まったとみなす。 */
  inflightSeats(now: Date): Set<number> {
    for (const [seat, ids] of this.inflight) {
      for (const [id, at] of ids) if (now.getTime() - at > this.settings.busyLimitMs) ids.delete(id);
      if (ids.size === 0) this.inflight.delete(seat);
    }
    return new Set(this.inflight.keys());
  }

  /** 版替えを待つときの「Holoが起きている」：どこかの席が返事の最中か、一言を渡したか、最後の起こす・止まる・Masterとの返事から restMs の間。 */
  awake(now: Date): boolean {
    if (this.inflightSeats(now).size > 0) return true;
    if (this.handedAt !== undefined && now.getTime() - this.handedAt < this.settings.restMs) return true;
    const last = this.lines().findLast(line => line.kind === "wake" || line.kind === "stop" || line.kind === "seat" && line.event === "talk");
    return last !== undefined && now.getTime() - Date.parse(last.ts) < this.settings.restMs;
  }

  /** 返事の通信の知らせ。席の返事が終わったら true（拡張はそのときだけ次の一言を取りに来る）。 */
  net(report: NetReport, now: Date): boolean {
    if (report.method !== "POST" || !this.settings.replyPath.test(report.path) || report.seat === undefined) return false;
    const lines = this.lines();
    const seat = this.seats(lines).find(s => s.seat === report.seat);
    if (!seat?.since || seat.since !== report.since) return false;
    const ids = this.inflight.get(seat.seat) ?? new Map<string, number>();
    if (report.phase === "start") {
      // 終わりの知らせを取りこぼした通信を抱え続けない。同じ会話では、新しい返事の始まりが今の通信の正本になる
      ids.clear();
      ids.set(report.id, now.getTime());
      this.inflight.set(seat.seat, ids);
      return false;
    }
    // 始まりを知らない終わり（郵便局が起き直した後など）も、ほかに続いている通信がなければ返事の終わりとして扱う
    ids.delete(report.id);
    if (ids.size > 0) return false;
    this.inflight.delete(seat.seat);
    if (postalReply(lines, seat)) {
      // どう終わったかは見分けない（ChatGPTはページ自身が通信を閉じることもある）。記録として残すだけ
      const ended = report.error ?? (report.status && report.status !== 200 ? `HTTP ${report.status}` : undefined);
      append(this.residentsRoot, MESSENGER, {
        kind: "stop", ts: now.toISOString(), how: "exit", work: seat.work ?? "", seat: seat.seat, ...(ended ? { detail: ended } : {}),
      });
    } else {
      append(this.residentsRoot, MESSENGER, { kind: "seat", ts: now.toISOString(), seat: seat.seat, event: "talk" });
    }
    return true;
  }

  /** 拡張が一言を送れたら、起こした事実を書く。新しい会話のURLも分かれば、席に結ぶ。送れなかったときは何も書かない。 */
  sent(result: { ok: boolean; seat: number; since: string; letters: string[]; url?: string }, now: Date): boolean {
    this.handedAt = undefined;
    if (!result.ok) return true;
    const lines = this.lines();
    const seat = this.seats(lines).find(s => s.seat === result.seat);
    if (!seat?.since || seat.since !== result.since || !seat.work) return false;
    if (!Array.isArray(result.letters) || result.letters.length === 0 || result.letters.some(id => typeof id !== "string" || !id)) return false;
    const key = trackKey(seat.work);
    const ours = result.letters.every(id => lines.some(line => line.kind === "letter" && line.id === id && line.to === MESSENGER && trackKey(line.work) === key));
    if (!ours) return false;
    append(this.residentsRoot, MESSENGER, { kind: "wake", ts: now.toISOString(), letters: result.letters, how: "holo tab", work: seat.work, seat: seat.seat });
    if (result.url) this.url(seat.seat, seat.since, result.url, now);
    return true;
  }

  /** 拡張へ一言を渡した。送れたかの知らせ（sent）が届くまで、版替えを待たせる。 */
  handed(now: Date): void {
    this.handedAt = now.getTime();
  }

  /** 席の会話のURLが分かった。1つの席に1つだけで、ほかの席の会話とは重ねない。 */
  url(seatNumber: number, since: string, raw: string, now: Date): boolean {
    const seats = this.seats();
    const seat = seats.find(s => s.seat === seatNumber);
    if (!seat?.since || seat.since !== since || seat.url) return false;
    const url = projectConversationUrl(raw, this.settings.projectId);
    if (!url) return false;
    if (seats.some(s => s.url && conversationId(s.url) === conversationId(url))) return false;
    append(this.residentsRoot, MESSENGER, { kind: "seat", ts: now.toISOString(), seat: seatNumber, event: "url", url });
    return true;
  }

  /** Masterが入る。空いた席を1つ開く（作業場はまだない）。満席なら undefined。 */
  enter(now: Date): { seat: number; since: string } | undefined {
    const free = freeSeats(this.seats(), this.settings.seats)[0];
    if (free === undefined) return undefined;
    const since = now.toISOString();
    append(this.residentsRoot, MESSENGER, { kind: "seat", ts: since, seat: free, event: "open" });
    return { seat: free, since };
  }

  /** Masterが席を空ける。入ったばかりの席はそのまま閉じ、結ばれた席にはHoloへ閉じてほしい手紙を出す（引き継ぎを書いて閉じる）。 */
  leave(seatNumber: number, now: Date): { closed: true } | { letter: string } | undefined {
    const lines = this.lines();
    const seat = this.seats(lines).find(s => s.seat === seatNumber);
    if (!seat?.since) return undefined;
    if (!seat.work) {
      append(this.residentsRoot, MESSENGER, { kind: "seat", ts: now.toISOString(), seat: seatNumber, event: "close" });
      return { closed: true };
    }
    const pending = closeLetter(track(lines, seat.work), seat);
    if (pending) return { letter: pending.id };
    const letter = closeSeatLetter(seat, "master", now);
    append(this.residentsRoot, MESSENGER, letter);
    return { letter: letter.id };
  }

  /** 席の会話が始まってから、手がこの席へ返した字数。 */
  charsOf(seat: Seat): number {
    if (!seat.started) return 0;
    const dir = join(this.residentsRoot, MESSENGER, "lifelog", "hands");
    if (!existsSync(dir)) return 0;
    const day = JST_DAY.format(new Date(seat.started));
    let total = 0;
    for (const name of readdirSync(dir).filter(file => file.endsWith(".jsonl") && file.slice(0, 10) >= day)) {
      for (const row of this.handsRows(join(dir, name))) if (row.seat === seat.seat && row.ts > seat.started) total += row.chars;
    }
    return total;
  }

  private handsRows(file: string): HandsRow[] {
    const size = statSync(file).size;
    let cached = this.hands.get(file);
    if (!cached || size < cached.size) cached = { size: 0, rows: [] };
    if (size > cached.size) {
      const buffer = Buffer.alloc(size - cached.size);
      const fd = openSync(file, "r");
      try {
        readSync(fd, buffer, 0, buffer.length, cached.size);
      } finally {
        closeSync(fd);
      }
      // 書きかけの行は、次に読む
      const end = buffer.lastIndexOf(0x0a) + 1;
      const rows = [...cached.rows];
      for (const raw of buffer.subarray(0, end).toString("utf8").split("\n")) {
        if (!raw.trim()) continue;
        try {
          const line = JSON.parse(raw) as { ts?: unknown; output?: unknown; seat?: unknown };
          if (typeof line.ts === "string" && typeof line.output === "string" && typeof line.seat === "number") {
            rows.push({ ts: line.ts, seat: line.seat, chars: line.output.length });
          }
        } catch {
          // 壊れた1行があっても、ほかの行から数えられる分は数える
        }
      }
      cached = { size: cached.size + end, rows };
    }
    this.hands.set(file, cached);
    return cached.rows;
  }

  /** ポップアップの席の一覧。生ログと手のログから、その場で組み立てる。 */
  status(now: Date, waits: ReadonlyMap<string, string>): SeatStatus[] {
    const lines = this.lines();
    const inflight = this.inflightSeats(now);
    return this.seats(lines).map((seat): SeatStatus => {
      const base = { seat: seat.seat, awake: inflight.has(seat.seat), waiting: [], masterWaiting: false, closing: false, chars: 0, limit: this.settings.seatChars };
      if (!seat.since) return { ...base, state: "empty" };
      const where = { since: seat.since, ...(seat.url ? { url: seat.url } : {}) };
      const lost = !seat.url && lines.some(line => line.kind === "wake" && line.seat === seat.seat && line.ts >= seat.since!);
      const problem = lost ? "新しい会話へ送ったが、会話のURLがまだ届いていない" : undefined;
      if (!seat.work) return { ...base, ...where, state: "entered", ...(problem ? { problem } : {}) };
      const scoped = track(lines, seat.work);
      const waiting = unfinished(scoped).filter(letter => waits.has(letter.id)).map(letter => ({ letter: letter.id, for: waits.get(letter.id)! }));
      const lastNote = scoped.findLast(line => line.kind === "note");
      return {
        ...base, ...where, state: "bound", work: seat.work, waiting,
        masterWaiting: waiting.some(wait => wait.for === MASTER),
        closing: Boolean(closeLetter(scoped, seat)),
        ...(lastNote?.kind === "note" ? { lastNote: lastNote.body.slice(0, 160) } : {}),
        chars: this.charsOf(seat),
        ...(problem ? { problem } : {}),
      };
    });
  }
}

export function projectEntryUrl(projectId: string): string {
  return `https://chatgpt.com/g/${projectId}/project`;
}

function conversationId(raw: string): string | undefined {
  return chatUrl.parse(raw)?.id;
}

/** Nirai Project の会話のURLだけを、余計な部分を落とした形で受け取る。 */
export function projectConversationUrl(raw: string, projectId: string): string | undefined {
  return chatUrl.isProjectConversation(raw, projectId) ? chatUrl.parse(raw)?.url : undefined;
}
