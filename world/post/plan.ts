// 郵便局の見回りで決めることを、1か所で決める。生ログと「今起きているか」だけを見て、何も書かない。
// 決めたことを実際に行うのは見回り（office.ts）で、拡張が取りに来る一言（/holo/next）も、この結果の先頭を返すだけ。
// 決めること：Masterに知らせる手紙、閉じる席、開く席、片付ける作業場、起こすCLI、Holoの席へ送る一言。

import { activeLimit, track, trackKey, tracksOf, type Line, type Unfinished, unfinished, waits } from "./letters.ts";
import { freeSeats, type Seat, seatOf, seatsOf } from "./seats.ts";
import { MESSENGER, ready, roomWakeText, toTellMaster, toWake } from "./waker.ts";
import { toClean } from "./work.ts";

export type PlanSettings = {
  team: string[];
  restMs: number;
  tellMasterAfter: number;
  workKeepMs: number;
  /** CLIの住人ごとの、作業場をまたいだ同時起床の上限 */
  maxConcurrent: Record<string, number>;
  holo: { seats: number; seatChars: number; seatIdleMs: number; masterTurnMs: number };
};

export type PlanInput = {
  now: Date;
  lines: Record<string, Line[]>;
  folders: string[];
  /** CLIで起こす住人の、いま起きている作業場（trackKey） */
  cli: Record<string, ReadonlySet<string>>;
  /** Holoの返事の通信が続いている席と、席ごとの会話の字数 */
  holo?: { inflight: ReadonlySet<number>; chars: (seat: Seat) => number };
  /** コマンドが動いている作業場と、長いコマンドの結果の手紙を待っている作業場（trackKey） */
  handsBusy: ReadonlySet<string>;
  handsAwaiting: ReadonlySet<string>;
  /** 郵便局の版替えを待っている間は、誰も新しく起こさない */
  reloadWaiting: boolean;
};

export type HoloOffer = { seat: number; since: string; tag: string; work: string; letters: string[]; text: string; url?: string };

export type Plan = {
  waits: Map<string, string>;
  seats: Seat[];
  tells: { resident: string; letter: Unfinished; lines: Line[] }[];
  /** 動きのない席と、会話が長くなった席。その場で閉じる（続きは郵便受けの手紙と書き残しにある） */
  seatCloses: number[];
  /** 起こせる手紙があるのに席のない作業場へ、空いた席を開く */
  seatOpens: { seat: number; work: string }[];
  cleanups: string[];
  wakes: { resident: string; work: string; letters: string[] }[];
  offers: HoloOffer[];
};

export function plan(settings: PlanSettings, input: PlanInput): Plan {
  const { now } = input;
  const lines = Object.fromEntries(settings.team.map(r => [r, [...(input.lines[r] ?? [])]]));
  const waiting = waits(Object.values(lines));
  const result: Plan = { waits: waiting, seats: [], tells: [], seatCloses: [], seatOpens: [], cleanups: [], wakes: [], offers: [] };

  for (const [resident, own] of Object.entries(lines)) {
    for (const work of tracksOf(unfinished(own))) {
      const scoped = track(own, work);
      for (const letter of toTellMaster(scoped, settings.tellMasterAfter, waiting)) {
        result.tells.push({ resident, letter, lines: scoped });
        // Masterに回した手紙では、この見回りでも起こさない
        own.push({ kind: "tell", ts: now.toISOString(), letter: letter.id, how: "" });
      }
    }
  }

  result.cleanups = toClean(input.folders, Object.values(lines), input.handsBusy, now, settings.workKeepMs);

  for (const [resident, awake] of Object.entries(input.cli)) {
    const own = lines[resident] ?? [];
    if (input.reloadWaiting || activeLimit(own, now)) continue; // 版替えと使用上限は住人全体に効く
    let inFlight = awake.size;
    for (const work of tracksOf(unfinished(own))) {
      if (inFlight >= (settings.maxConcurrent[resident] ?? 1)) break;
      if (awake.has(trackKey(work))) continue;
      const letters = toWake(track(own, work), false, now, settings.restMs, waiting);
      if (!letters.length) continue;
      result.wakes.push({ resident, work, letters });
      inFlight++;
    }
  }

  if (input.holo && lines[MESSENGER]) planSeats(settings, input, lines[MESSENGER], waiting, result);
  return result;
}

function planSeats(settings: PlanSettings, input: PlanInput, holo: Line[], waiting: Map<string, string>, result: Plan): void {
  const { now } = input;
  const { inflight, chars } = input.holo!;
  const { seats: count, seatChars, seatIdleMs, masterTurnMs } = settings.holo;
  const seats = seatsOf(holo, count);
  result.seats = seats;
  const quiet = (at: string | undefined, ms: number) => !at || now.getTime() - Date.parse(at) >= ms;

  for (const seat of seats) {
    if (!seat.since) continue;
    const awake = inflight.has(seat.seat);
    const key = seat.work ? trackKey(seat.work) : undefined;
    const busy = awake || key !== undefined && (input.handsBusy.has(key) || input.handsAwaiting.has(key));
    const scoped = seat.work ? track(holo, seat.work) : [];
    // 新しい会話へ送ったのにURLが分からないままの席は、もう一度新しい会話を作らない。静かになったら閉じる
    const lost = !seat.url && holo.some(line => line.kind === "wake" && line.seat === seat.seat && line.ts >= seat.since!);
    const idle = !busy && quiet(seat.lastActivity, seatIdleMs) && (lost || ready(scoped, waiting).length === 0);
    const full = !awake && chars(seat) > seatChars;
    if (idle || full) {
      result.seatCloses.push(seat.seat);
      continue;
    }
    if (!seat.work || lost) continue;
    if (input.reloadWaiting || awake || input.handsAwaiting.has(key!) || !quiet(seat.lastTalk, masterTurnMs)) continue;
    const letters = toWake(scoped, false, now, settings.restMs, waiting);
    if (!letters.length) continue;
    result.offers.push({
      seat: seat.seat, since: seat.since, tag: seat.tag!, work: seat.work, letters, text: roomWakeText(seat.tag!, seat.work),
      ...(seat.url ? { url: seat.url } : {}),
    });
  }

  if (input.reloadWaiting) return;
  const free = [...freeSeats(seats, count), ...result.seatCloses].sort((a, b) => a - b);
  for (const work of tracksOf(unfinished(holo))) {
    if (!free.length) break;
    if (seatOf(seats, work)) continue;
    if (toWake(track(holo, work), false, now, settings.restMs, waiting).length === 0) continue;
    result.seatOpens.push({ seat: free.shift()!, work });
  }
}
