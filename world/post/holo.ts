// Holoの部屋（ChatGPTの専用の会話）と郵便局のあいだ。拡張が、会話の通信の始まりと終わりを知らせ、
// 起こす一言を取りに来る。Holoが起きているか（返事をしている最中か）は、画面ではなく通信で決める。

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { append, JST_DAY, scopeLines, workKey, unfinished, type Letter, type Line, newLetterId, readAll, type Room } from "./letters.ts";
import { POST_OFFICE, toWake } from "./waker.ts";

export type NetReport = { phase: "start" | "end" | "error"; id: string; method: string; path: string; status?: number; error?: string; work?: string };
export type HoloNext = { text: string; letters: string[]; url: string; createRoom: boolean; currentRoomUrl?: string; roomMarker?: string; work?: string };
export type HoloRoomState = "unregistered" | "ready" | "moving" | "new-room";
export type HoloRoomStatus = { state: HoloRoomState; url?: string; chars: number; limit: number; failure?: string };

type HoloSettings = {
  restMs: number;
  masterTurnMs: number;
  busyLimitMs: number;
  replyPath: RegExp;
  roomChars: number;
  projectId: string;
};

type Track = {
  inflight: Map<string, number>;
  lastReplyEndedAt?: number;
  lastMasterReplyEndedAt?: number;
  lastWakeOfferedAt?: number;
  postalReplyOffered: boolean;
  masterReply: boolean;
  roomFailure?: string;
};

export class HoloRoom {
  private tracks = new Map<string, Track>();
  private residentsRoot: string;
  private settings: HoloSettings;

  constructor(residentsRoot: string, settings: HoloSettings) {
    this.residentsRoot = residentsRoot;
    this.settings = settings;
  }

  private track(work?: string): Track {
    const key = workKey(work ?? "");
    if (!this.tracks.has(key)) this.tracks.set(key, {
      inflight: new Map(), postalReplyOffered: false, masterReply: false,
    });
    return this.tracks.get(key)!;
  }

  /**
   * 郵便局が起こす一言を渡した直後、conversation / resume が続いている間、
   * 最後の通信が終わって restMs の間は忙しい。
   * Masterとの会話も同じに数える。どの返事が郵便局起点かは見分けない。
   */
  awake(now: Date, work?: string): boolean {
    if (work === undefined) return [...this.tracks.keys()].some(key => this.awake(now, key));
    const state = this.track(work);
    for (const [id, since] of state.inflight) if (now.getTime() - since > this.settings.busyLimitMs) state.inflight.delete(id);
    if (state.inflight.size > 0) return true;
    const restingAfterOffer = state.lastWakeOfferedAt !== undefined && now.getTime() - state.lastWakeOfferedAt < this.settings.restMs;
    const restingAfterReply = state.lastReplyEndedAt !== undefined && now.getTime() - state.lastReplyEndedAt < this.settings.restMs;
    return restingAfterOffer || restingAfterReply;
  }

  net(report: NetReport, now: Date): void {
    if (report.method !== "POST" || !this.settings.replyPath.test(report.path)) return;
    const work = workKey(report.work ?? "");
    const state = this.track(work);
    if (report.phase === "start") {
      // next() は送信・会話開始より前に呼ばれる。sent の通知は開始後になることもある。
      // resume は同じ返事の続きなので、前の区別を引き継ぐ。
      if (!report.path.endsWith("/resume")) {
        state.masterReply = !state.postalReplyOffered;
        state.postalReplyOffered = false;
      }
      // ChatGPT側で終了通知を取りこぼした通信を、新しい返事まで「進行中」として
      // 抱え続けない。同じ部屋では新しい返事の開始が現在の通信の正本になる。
      state.inflight.clear();
      state.inflight.set(report.id, now.getTime());
      return;
    }
    if (!state.inflight.delete(report.id)) return;
    state.lastReplyEndedAt = now.getTime();
    if (state.masterReply) state.lastMasterReplyEndedAt = now.getTime();
    if (state.inflight.size > 0) return;
    // 郵便局が起こした後の返事が終わったときだけ、止まったと書く（Masterとの会話だけなら書かない）
    const lines = scopeLines(readAll(this.residentsRoot, "Holo"), work);
    const last = lines.findLast(l => l.kind === "wake" || l.kind === "stop");
    if (last?.kind !== "wake") return;
    // 止まった理由は見分けない。ChatGPTは返事を書き終えても、ページ自身が通信を閉じたり
    // （ERR_ABORTED・ERR_FAILED）、ふつうに終えたりする。どう終わったかは、記録として残すだけ
    const ended = report.error ?? (report.status && report.status !== 200 ? `HTTP ${report.status}` : undefined);
    append(this.residentsRoot, "Holo", { kind: "stop", ts: now.toISOString(), how: "exit", ...(work ? { work } : {}), ...(ended ? { detail: ended } : {}) });
  }

  /**
   * 拡張が取りに来る一言。URLは必ず郵便局から渡すので、拡張はURLを覚えない。
   * 引っ越しの手紙が未済の間は、その手紙だけを前の部屋へ届ける。済んだ後の次の起床は新しい部屋。
   */
  next(now: Date, busyRooms: ReadonlySet<string> = new Set()): HoloNext | undefined {
    const lines = readAll(this.residentsRoot, "Holo");
    const pending = unfinished(lines);
    const keys = [...new Set(pending.map(letter => workKey(letter.work ?? "")))];
    keys.sort((a, b) => (pending.find(l => workKey(l.work ?? "") === a)?.ts ?? "").localeCompare(pending.find(l => workKey(l.work ?? "") === b)?.ts ?? ""));
    for (const line of lines) if (line.kind === "room" && !keys.includes(workKey(line.work ?? ""))) keys.push(workKey(line.work ?? ""));
    for (const key of keys) {
      if (busyRooms.has(key)) continue;
      const next = this.nextFor(scopeLines(lines, key), now, key);
      if (next) return next;
    }
    return undefined;
  }

  private nextFor(lines: Line[], now: Date, work: string): HoloNext | undefined {
    const state = this.track(work);
    if (state.lastMasterReplyEndedAt !== undefined
        && now.getTime() - state.lastMasterReplyEndedAt < this.settings.masterTurnMs) return undefined;
    if (!this.awake(now, work) && [...this.tracks.keys()].filter(key => this.awake(now, key)).length >= 3) return undefined;
    const room = currentRoom(lines);
    // 既存会話から移行した直後など、roomがまだ正本に無いときは勝手に新部屋を作らない。
    // 先にMasterが今の会話をroomとして登録してから、自動引っ越しを使う。
    if (!room && !work) return undefined;
    // 新しい作業場の部屋へ送信済みならURLの確定前でも二度作らない。
    // wakeは送信成功時だけ書くので、部屋作成前の正本として使える。
    if (!room && work && lines.some(line => line.kind === "wake")) return undefined;
    let move = moveAfter(lines, room);
    if (room && !move && this.charsAfter(room) > this.settings.roomChars) {
      move = this.addMoveLetter(now, work);
      lines.push(move);
    }

    const available = toWake(lines, this.awake(now, work), now, this.settings.restMs);
    if (available.length === 0) return undefined;

    const moveDone = move ? lines.some(line => line.kind === "done" && line.letter === move!.id) : false;
    // move済み後のwakeは「新しい部屋へ送信できた」事実。URL未確定でも二つ目は作らない。
    if (move && moveDone && wakeAfterMoveDone(lines, move)) return undefined;
    const createRoom = !room || Boolean(move && moveDone);
    if (createRoom && state.roomFailure) return undefined;
    const letters = move && !moveDone
      ? available.includes(move.id) ? [move.id] : []
      : available;
    if (letters.length === 0) return undefined;

    // 一言を渡した直後から、実際のconversationが始まるまでの隙でも版替えさせない。
    // wake行はsent成功時だけなので、送信失敗を届き直し回数には数えない。
    state.lastWakeOfferedAt = now.getTime();
    state.postalReplyOffered = true;
    return {
      text: `ここは「${work || "受付"}」の部屋。Niraiの read_mailbox を room:"${work || "受付"}" で確認してね！`,
      letters,
      url: createRoom ? projectEntryUrl(this.settings.projectId) : room!.url,
      createRoom,
      ...(room ? { currentRoomUrl: room.url } : {}),
      ...(work ? { work } : {}),
      ...(createRoom ? { roomMarker: `[Nirai-room:${move?.id ?? letters[0]}]` } : {}),
    };
  }

  /** Masterが「今すぐ引っ越す」を押したとき。同じroomにつき1通だけ出す。 */
  move(now: Date, work = ""): boolean {
    const lines = scopeLines(readAll(this.residentsRoot, "Holo"), work);
    const room = currentRoom(lines);
    if (!room || moveAfter(lines, room)) return false;
    this.track(work).roomFailure = undefined;
    this.addMoveLetter(now, work);
    return true;
  }

  /**
   * Masterが開いているNirai Projectの会話を部屋として登録する。
   * 初回（S0）と、新部屋の自動作成を諦めた後（S3）だけ許す。
   */
  register(rawUrl: string, now: Date, work = ""): boolean {
    const lines = scopeLines(readAll(this.residentsRoot, "Holo"), work);
    const room = currentRoom(lines);
    const state = roomState(lines);
    if (state !== "unregistered" && state !== "new-room") return false;
    const url = projectConversationUrl(rawUrl, this.settings.projectId);
    if (!url) return false;
    if (room && conversationId(room.url) === conversationId(url)) return false;
    append(this.residentsRoot, "Holo", { kind: "room", ts: now.toISOString(), url, ...(work ? { work } : {}) });
    this.track(work).roomFailure = undefined;
    return true;
  }

  /** Masterがpopupから明示的に再試行したときだけ、自動作成の停止印を外す。 */
  retryRoom(work = ""): void {
    this.track(work).roomFailure = undefined;
  }

  /** ポップアップ表示用。表示は生ログと手のログからその場で作る。 */
  status(work = ""): HoloRoomStatus {
    const lines = scopeLines(readAll(this.residentsRoot, "Holo"), work);
    const room = currentRoom(lines);
    const state = roomState(lines);
    const move = moveAfter(lines, room);
    const sentWithoutRoom = Boolean(room && move && lines.some(line => line.kind === "done" && line.letter === move.id) && wakeAfterMoveDone(lines, move));
    const failure = this.track(work).roomFailure ?? (sentWithoutRoom ? "新しい部屋へ送信済みだが、部屋のURLをまだ確定できていない" : undefined);
    return {
      state,
      ...(room ? { url: room.url } : {}),
      chars: room ? this.charsAfter(room) : 0,
      limit: this.settings.roomChars,
      ...(failure ? { failure } : {}),
    };
  }

  /**
   * 拡張が一言を送れたら、まずwakeとして事実を残す。
   * 新しい部屋のURLも受け取れたときだけroomを続けて書く。URLがなくても同じ引っ越しを作り直さない。
   */
  sent(result: { ok: boolean; letters: string[]; url?: string; reason?: string; touched?: boolean }, now: Date): boolean {
    // 送信先の正本は手紙。古い拡張がworkを送らなくても、受付のwakeに混ぜない。
    const all = readAll(this.residentsRoot, "Holo");
    if (!Array.isArray(result.letters) || result.letters.length === 0
        || result.letters.some(id => typeof id !== "string" || !id)) return false;
    const letters = result.letters.map(id => all.find(line => line.kind === "letter" && line.id === id && line.to === "Holo"));
    if (letters.some(letter => !letter || letter.kind !== "letter")) return false;
    const work = workKey(letters[0]!.work ?? "");
    if (letters.some(letter => workKey(letter!.work ?? "") !== work)) return false;
    const state = this.track(work);
    if (!result.ok) {
      state.postalReplyOffered = false;
      if (result.touched) state.roomFailure = result.reason ?? "新しい部屋の自動作成を途中で止めた";
      return true;
    }
    const lines = scopeLines(all, work);
    const room = currentRoom(lines);
    if (!room && !work) return true;
    const move = moveAfter(lines, room);
    const needsNewRoom = !room || Boolean(move && lines.some(line => line.kind === "done" && line.letter === move.id));
    if (needsNewRoom) {
      if (!move || !wakeAfterMoveDone(lines, move)) append(this.residentsRoot, "Holo", { kind: "wake", ts: now.toISOString(), letters: result.letters, how: "holo tab", ...(work ? { work } : {}) });
      if (!result.url) {
        state.roomFailure = "新しい部屋へ送信済みだが、部屋のURLをまだ確定できていない";
        return true;
      }
      const url = projectConversationUrl(result.url, this.settings.projectId);
      if (!url || (room && conversationId(room.url) === conversationId(url))) {
        state.roomFailure = "新しい部屋へ送信済みだが、部屋のURLを安全に確定できていない";
        return true;
      }
      append(this.residentsRoot, "Holo", { kind: "room", ts: now.toISOString(), url, ...(work ? { work } : {}) });
      state.roomFailure = undefined;
      return true;
    }
    append(this.residentsRoot, "Holo", { kind: "wake", ts: now.toISOString(), letters: result.letters, how: "holo tab", ...(work ? { work } : {}) });
    return true;
  }

  private addMoveLetter(now: Date, work = ""): Letter {
    const letter: Letter = {
      kind: "letter",
      ts: now.toISOString(),
      id: newLetterId(now),
      from: POST_OFFICE,
      to: "Holo",
      move: true,
      ...(work ? { work } : {}),
      body: "Holoの部屋を引っ越す。ほかの手紙はこの部屋で進めない。新しい部屋のHoloが続けるのに要ること（Masterと話している途中のこと、決まったこと、Masterの好み、進めていた仕事の今）を自分宛ての手紙にしてから、この手紙をmark_doneで済みにして。郵便受けの手紙と書き残しは引き継がれるので、書き写さなくてよい。済みにしたあとは、この古い部屋では郵便受けに触らない。",
    };
    append(this.residentsRoot, "Holo", letter);
    return letter;
  }

  private charsAfter(room: Room): number {
    const dir = join(this.residentsRoot, "Holo", "lifelog", "hands");
    if (!existsSync(dir)) return 0;
    const roomDay = JST_DAY.format(new Date(room.ts));
    let total = 0;
    for (const name of readdirSync(dir).filter(file => file.endsWith(".jsonl") && file.slice(0, 10) >= roomDay).sort()) {
      for (const raw of readFileSync(join(dir, name), "utf8").split("\n")) {
        if (!raw.trim()) continue;
        try {
          const line = JSON.parse(raw) as { ts?: unknown; output?: unknown; room?: unknown };
          if (typeof line.ts === "string" && line.ts > room.ts && typeof line.output === "string"
              && workKey(typeof line.room === "string" ? line.room : "") === workKey(room.work ?? "")) total += line.output.length;
        } catch {
          // 壊れた1行があっても、ほかの生ログから数えられる分は数える。
        }
      }
    }
    return total;
  }
}

function currentRoom(lines: Line[]): Room | undefined {
  return lines.findLast((line): line is Room => line.kind === "room");
}

function moveAfter(lines: Line[], room: Room | undefined): Letter | undefined {
  if (!room) return undefined;
  const roomIndex = lines.lastIndexOf(room);
  return lines.slice(roomIndex + 1).find((line): line is Letter => line.kind === "letter" && line.move === true);
}

function wakeAfterMoveDone(lines: Line[], move: Letter): boolean {
  const doneIndex = lines.findIndex(line => line.kind === "done" && line.letter === move.id);
  return doneIndex >= 0 && lines.slice(doneIndex + 1).some(line => line.kind === "wake");
}

function roomState(lines: Line[]): HoloRoomState {
  const room = currentRoom(lines);
  if (!room) return "unregistered";
  const move = moveAfter(lines, room);
  if (!move) return "ready";
  return lines.some(line => line.kind === "done" && line.letter === move.id) ? "new-room" : "moving";
}

function projectEntryUrl(projectId: string): string {
  return `https://chatgpt.com/g/${projectId}/project`;
}

function conversationId(raw: string): string | undefined {
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.hostname !== "chatgpt.com") return undefined;
    return /^\/c\/([0-9a-f-]+)\/?$/i.exec(url.pathname)?.[1]
      ?? /^\/g\/[^/]+\/c\/([0-9a-f-]+)\/?$/i.exec(url.pathname)?.[1];
  } catch {
    return undefined;
  }
}

function projectConversationUrl(raw: string, projectId: string): string | undefined {
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.hostname !== "chatgpt.com") return undefined;
    const match = /^\/g\/([^/]+)\/c\/([0-9a-f-]+)\/?$/i.exec(url.pathname);
    if (!match || match[1] !== projectId) return undefined;
    return `${url.origin}/g/${match[1]}/c/${match[2]}`;
  } catch {
    return undefined;
  }
}
