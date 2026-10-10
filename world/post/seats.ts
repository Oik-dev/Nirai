// Holoの席。席は ChatGPT の会話1つで、同時に開くのは設定の数まで。開いている席の数が、Holoが同時に働ける数になる。
// 席の今は、Holoの生ログの seat 行だけから決める（帳簿を持たない）。
//   空き → open（Masterが入る：作業場なし／郵便局が開く：作業場あり）→ bind（入ったばかりの席を作業場に結ぶ）→ close（空き）
// 1つの作業場に席は1つだけ。

import { newLetterId, trackKey, type Letter, type Line, type SeatLine, unfinished } from "./letters.ts";
import { MESSENGER, POST_OFFICE } from "./waker.ts";

export type Seat = {
  seat: number;
  /** 席に入った時刻（open行）。この席の今の居場所の印。空きなら無い */
  since?: string;
  /** 会話が始まった時刻。作業場を結び直しても会話は続くので、字数はここから数える */
  started?: string;
  work?: string;
  url?: string;
  /** Masterとの最後の返事の終わり */
  lastTalk?: string;
  /** 席でいちばん新しく何かが起きた時刻（入る・結ぶ・URL・Masterとの返事・起こす・止まる・読む） */
  lastActivity?: string;
};

export function seatsOf(lines: Line[], count: number): Seat[] {
  const seats = new Map<number, Seat>();
  for (let n = 1; n <= count; n++) seats.set(n, { seat: n });
  for (const line of lines) {
    if (line.kind === "seat") {
      const seat = seats.get(line.seat) ?? { seat: line.seat };
      if (line.event === "open") {
        seats.set(line.seat, {
          seat: line.seat, since: line.ts, started: line.started ?? line.ts, lastActivity: line.ts,
          ...(line.work ? { work: line.work } : {}), ...(line.url ? { url: line.url } : {}),
        });
        continue;
      }
      if (line.event === "close") {
        seats.set(line.seat, { seat: line.seat });
        continue;
      }
      if (!seat.since) continue;
      if (line.event === "bind" && !seat.work && line.work) seat.work = line.work;
      if (line.event === "url" && !seat.url && line.url) seat.url = line.url;
      if (line.event === "talk") seat.lastTalk = line.ts;
      seat.lastActivity = line.ts;
      seats.set(line.seat, seat);
    } else if ((line.kind === "wake" || line.kind === "stop" || line.kind === "read") && line.seat !== undefined) {
      const seat = seats.get(line.seat);
      if (seat?.since && line.ts >= seat.since) seat.lastActivity = line.ts;
    }
  }
  return [...seats.values()].sort((a, b) => a.seat - b.seat);
}

export const seatOf = (seats: Seat[], work: string): Seat | undefined =>
  seats.find(seat => seat.since && seat.work && trackKey(seat.work) === trackKey(work));

/** この席の今の居場所に宛てた、まだ済んでいない「席を閉じて」の手紙。 */
export function closeLetter(lines: Line[], seat: Seat): Letter | undefined {
  if (!seat.since) return undefined;
  return unfinished(lines).find(letter => letter.close === seat.seat && letter.ts >= seat.since!);
}

/** 作業場のいちばん新しい引き継ぎ（結ばれた席を閉じたときに残したもの）。 */
export function lastHandover(lines: Line[], work: string): SeatLine | undefined {
  return lines.findLast((line): line is SeatLine => line.kind === "seat" && line.event === "close"
    && Boolean(line.handover) && Boolean(line.work) && trackKey(line.work!) === trackKey(work));
}

/** この席の居場所で、まだ郵便受けを読んでいない（最初の read_mailbox に引き継ぎを添える）。 */
export function firstRead(lines: Line[], seat: Seat): boolean {
  return Boolean(seat.since) && !lines.some(line => line.kind === "read" && line.seat === seat.seat && line.ts >= seat.since!);
}

/** この席の居場所で、郵便局が起こした返事の後にまだ止まりもMasterとの返事も書いていない（今の返事は郵便のもの）。 */
export function postalReply(lines: Line[], seat: Seat): boolean {
  if (!seat.since) return false;
  const last = lines.findLast(line => line.ts >= seat.since! && (
    (line.kind === "wake" || line.kind === "stop") && line.seat === seat.seat
    || line.kind === "seat" && line.seat === seat.seat && (line.event === "talk" || line.event === "open")));
  return last?.kind === "wake";
}

/** 空いている席（小さい番号から）。 */
export const freeSeats = (seats: Seat[], count: number) => seats.filter(seat => !seat.since && seat.seat <= count).map(seat => seat.seat);

const CLOSE_WHY = {
  idle: "動きがなく、起こせる手紙もない",
  full: "会話が長くなった",
  master: "Masterが空けたいと言っている",
};

/** 結ばれた席を閉じてほしいと、その席のHoloへ出す手紙。leave_seat でだけ済む。 */
export function closeSeatLetter(seat: Seat, why: keyof typeof CLOSE_WHY, now: Date): Letter {
  return {
    kind: "letter", ts: now.toISOString(), id: newLetterId(now), from: POST_OFFICE, to: MESSENGER, work: seat.work!, close: seat.seat,
    body: `席${seat.seat}（作業場：${seat.work}）は${CLOSE_WHY[why]}。この席を閉じて。`
      + "次にこの作業場で起きるHoloが続けるのに要ること（Masterと話している途中のこと、決まったこと、Masterの好み、進めていた仕事の今）を、"
      + "leave_seat の handover に書いて閉じる。Masterと話している途中なら、その続きを先に自分宛ての手紙にする。"
      + "郵便受けの手紙と書き残しは引き継がれるので、書き写さなくてよい。閉じたあとは、この会話では郵便受けに触らない。",
  };
}
