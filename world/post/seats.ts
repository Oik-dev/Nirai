// Holoの席（拡張とHoloにはRoomと見せる）。席は ChatGPT の会話1つで、同時に開くのは設定の数まで。開いている席の数が、Holoが同時に働ける数になる。
// 席の今は、Holoの生ログの seat 行だけから決める（帳簿を持たない）。
//   空き → open（MasterがRoom kを押す：作業場なし／郵便局が開く：作業場あり）→ bind（作業場に結ぶ・結び直す）→ close（空き）
// 1つの作業場に席は1つだけ。開くたびに札（tag）が変わり、閉じた会話の古い札では郵便受けにも手にも触れない。

import { trackKey, type Line } from "./letters.ts";

export type Seat = {
  seat: number;
  /** 席を開いた時刻（open行）。この席の今の居場所の印。空きなら無い */
  since?: string;
  /** 今の居場所の札「<席>-<その席を開いた回数>」。Holoは道具にこれを渡す。空きなら無い */
  tag?: string;
  work?: string;
  url?: string;
  /** Masterとの最後の返事の終わり */
  lastTalk?: string;
  /** 席でいちばん新しく何かが起きた時刻（開く・結ぶ・URL・Masterとの返事・起こす・止まる・読む） */
  lastActivity?: string;
};

export function seatsOf(lines: Line[], count: number): Seat[] {
  const seats = new Map<number, Seat>();
  const opened = new Map<number, number>();
  for (let n = 1; n <= count; n++) seats.set(n, { seat: n });
  for (const line of lines) {
    if (line.kind === "seat") {
      const seat = seats.get(line.seat) ?? { seat: line.seat };
      if (line.event === "open") {
        const times = (opened.get(line.seat) ?? 0) + 1;
        opened.set(line.seat, times);
        seats.set(line.seat, {
          seat: line.seat, since: line.ts, tag: `${line.seat}-${times}`, lastActivity: line.ts, ...(line.work ? { work: line.work } : {}),
        });
        continue;
      }
      if (line.event === "close") {
        seats.set(line.seat, { seat: line.seat });
        continue;
      }
      if (!seat.since) continue;
      if (line.event === "bind" && line.work) seat.work = line.work;
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

/** 札の席。「Room 1-3」「［1-3］」のような書き方の揺れは、ここで受け止める。閉じた居場所の札なら undefined。 */
export function seatOfTag(seats: Seat[], raw: string): Seat | undefined {
  const found = /(\d+)\s*-\s*(\d+)/.exec(raw);
  if (!found) return undefined;
  const tag = `${Number(found[1])}-${Number(found[2])}`;
  return seats.find(seat => seat.tag === tag);
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
