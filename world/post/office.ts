// 郵便局の見回り。手紙が出たとき・止まった知らせのとき・一定の間隔で、全体を生ログから見直す。
// 何をするかは見回りの決め方（plan.ts）が決め、ここはそれを生ログへ書き、CLIを起こし、作業場を片付ける。最後に本番版の入れ替え確認。
// 拡張が取りに来るHoloへの一言（holoNext）も、同じ決め方の結果を返すだけで、何も書かない。

import type { CliResident } from "./cli.ts";
import type { HoloSeats } from "./holo.ts";
import { append, type Letter, type Line, newLetterId, readAll, type Stop, trackKey, type Unfinished, unfinished } from "./letters.ts";
import { type HoloOffer, plan, type Plan, type PlanSettings } from "./plan.ts";
import { closeSeatLetter } from "./seats.ts";
import { MESSENGER, POST_OFFICE, stuckText, WAKE_TEXT } from "./waker.ts";
import { ensureWork, folders, removeWork } from "./work.ts";

export type OfficeSettings = PlanSettings & {
  residentsRoot: string; workRoot: string; sweepMs: number; limitWaitMs: number;
  repoRoot?: string;
};

/** Holoの席。awaiting：長いコマンドの結果の手紙を待っている作業場（trackKey） */
export type OfficeHolo = { seats: HoloSeats; awaiting: () => ReadonlySet<string> };

const LIMIT_TIME = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
});

export class PostOffice {
  private settings: OfficeSettings;
  private clis: CliResident[];
  private busyWork: () => ReadonlySet<string>;
  private reloadWaiting: () => boolean;
  private afterSweep: (now: Date) => void;
  private holo: OfficeHolo | undefined;
  private pending = false;
  private timer: NodeJS.Timeout | undefined;
  private stopped = false;
  private cleanupFailures = new Map<string, string>();

  /** clis：郵便局がCLIで起こす住人（Holoは拡張が起こす）。
   *  busyWork：コマンドが動いている作業場（Holoの手。片付けない） */
  constructor(
    settings: OfficeSettings,
    clis: CliResident[] = [],
    busyWork: () => ReadonlySet<string> = () => new Set(),
    afterSweep: (now: Date) => void = () => {},
    reloadWaiting: () => boolean = () => false,
    holo?: OfficeHolo,
  ) {
    this.settings = settings;
    this.clis = clis;
    this.busyWork = busyWork;
    this.afterSweep = afterSweep;
    this.reloadWaiting = reloadWaiting;
    this.holo = holo;
  }

  /** 手紙が出たら：作業場を作り、すぐに見直す。 */
  onSent(letter: Letter): void {
    ensureWork(this.settings.workRoot, letter.work);
    this.cleanupFailures.delete(trackKey(letter.work));
    this.soon();
  }

  /** CLIの住人が止まったとき。上限なら、頼んだ住人へその事実を1度だけ知らせる。 */
  onResidentStop(resident: string, stop: Stop): void {
    if (stop.how === "limit") this.notifyLimit(resident, stop);
    this.soon();
  }

  /** すぐに見直す（同じ瞬間に何度呼ばれても1回）。 */
  soon(): void {
    if (this.stopped || this.pending) return;
    this.pending = true;
    setImmediate(() => {
      this.pending = false;
      this.sweep(new Date());
    });
  }

  start(): void {
    this.soon();
    this.timer = setInterval(() => this.sweep(new Date()), this.settings.sweepMs);
    this.timer.unref();
  }

  /** 版替えを決めた後は、新しい見回りやCLI起床を始めない。 */
  stop(): void {
    this.stopped = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = undefined;
  }

  /** 今の生ログから、見回りで決めることを決める（何も書かない）。 */
  planNow(now: Date): Plan {
    const { residentsRoot, workRoot, team } = this.settings;
    const holo = this.holo;
    return plan(this.settings, {
      now,
      lines: Object.fromEntries(team.map(r => [r, readAll(residentsRoot, r)])),
      folders: folders(workRoot),
      cli: Object.fromEntries(this.clis.map(cli => [cli.name, cli.awakeWorks()])),
      ...(holo ? { holo: { inflight: holo.seats.inflightSeats(now), chars: seat => holo.seats.charsOf(seat) } } : {}),
      handsBusy: this.busyWork(),
      handsAwaiting: holo?.awaiting() ?? new Set(),
      reloadWaiting: this.reloadWaiting(),
    });
  }

  /** 拡張が取りに来る、Holoの席へ送る一言。 */
  holoNext(now: Date): HoloOffer | undefined {
    if (this.stopped) return undefined;
    return this.planNow(now).offers[0];
  }

  sweep(now: Date): void {
    if (this.stopped) return;
    const { residentsRoot, workRoot } = this.settings;
    const decided = this.planNow(now);
    const ts = now.toISOString();

    for (const { resident, letter, lines } of decided.tells) {
      // Holo自身の手紙は拡張の印で、ほかの住人の手紙はその作業場の席のHoloを通して、Masterに知らせる
      const how = resident === MESSENGER ? "拡張の印" : this.relay(letter, lines, now);
      append(residentsRoot, resident, { kind: "tell", ts, letter: letter.id, how });
      console.log(`${ts} tell master about ${letter.id} ${how}`);
    }

    for (const seat of decided.seatCloses) {
      append(residentsRoot, MESSENGER, { kind: "seat", ts, seat, event: "close" });
      console.log(`${ts} close seat ${seat}`);
    }
    for (const { seat, work, why } of decided.closeLetters) {
      const letter = closeSeatLetter({ seat, work }, why, now);
      append(residentsRoot, MESSENGER, letter);
      console.log(`${ts} ask seat ${seat} (${work}) to close: ${why}`);
    }
    for (const { seat, work } of decided.seatOpens) {
      append(residentsRoot, MESSENGER, { kind: "seat", ts, seat, event: "open", work });
      console.log(`${ts} open seat ${seat} for ${work}`);
    }

    for (const name of decided.cleanups) {
      const result = removeWork(workRoot, name, this.settings.repoRoot);
      const key = trackKey(name);
      // 保存失敗で毎分同じ行をpost.logへ書かず、変化したときだけ記録する。
      if (result === "removed" || this.cleanupFailures.get(key) !== result) {
        console.log(`${ts} remove work ${name} ${result}`);
      }
      if (result === "removed") this.cleanupFailures.delete(key);
      else this.cleanupFailures.set(key, result);
    }

    for (const { resident, work, letters } of decided.wakes) {
      this.clis.find(cli => cli.name === resident)?.wake(letters, WAKE_TEXT, now, work);
      console.log(`${ts} wake ${resident} ${work} for ${letters.join(",")}`);
    }
    this.afterSweep(now);
  }

  private relay(stuck: Unfinished, lines: Line[], now: Date): string {
    append(this.settings.residentsRoot, MESSENGER, {
      kind: "letter", ts: now.toISOString(), id: newLetterId(now), from: POST_OFFICE, to: MESSENGER, work: stuck.work,
      body: `Masterに伝えて：${stuckText(stuck, lines)}どうするかはMasterに決めてもらって。`, based_on: stuck.id,
    });
    return `${MESSENGER}への手紙`;
  }

  /** 上限の知らせは、出した人ではなく、その作業場のHoloへ出す。限りのある脳を、知らせのためだけに起こさない。 */
  private notifyLimit(resident: string, stop: Stop): void {
    const pending = unfinished(readAll(this.settings.residentsRoot, resident));
    const told = readAll(this.settings.residentsRoot, MESSENGER);
    for (const letter of pending) {
      if (letter.from === resident || !this.settings.team.includes(letter.from)) continue;
      const key = `limit:${resident}:${stop.ts}:${letter.id}`;
      if (told.some(line => line.kind === "letter" && line.from === POST_OFFICE && line.based_on === key)) continue;

      const when = stop.untilKnown === false || !stop.until
        ? `起きる時刻は分からない。郵便局は${Math.round(this.settings.limitWaitMs / 60_000)}分後にもう一度試す。`
        : `${LIMIT_TIME.format(new Date(stop.until))}まで眠っている。`;
      const choice = stop.untilKnown === false
        ? "起きる時刻が分からないので、決まりの順で代わりに頼んで。"
        : `起きるまでが${this.settings.limitWaitMs / 3_600_000}時間以内なら待ち、それより先なら決まりの順で代わりに頼んで。`;
      append(this.settings.residentsRoot, MESSENGER, {
        kind: "letter",
        ts: stop.ts,
        id: newLetterId(new Date(stop.ts)),
        from: POST_OFFICE,
        to: MESSENGER,
        work: letter.work,
        body: `${resident}は上限で${when}${letter.from}の手紙 ${letter.id} はそれまで届かない。${choice}`,
        based_on: key,
      });
    }
  }
}
