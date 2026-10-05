// 郵便局の見回り。手紙が出たとき・止まった知らせのとき・一定の間隔で、全体を生ログから見直す。
// 見直すたびに決めること：Masterに知らせること、片付ける作業場、起こすCLIの住人、最後に本番版の入れ替え確認。

import type { CliResident } from "./cli.ts";
import { append, type Letter, newLetterId, readAll, type Stop, type Tell, type Unfinished, unfinished } from "./letters.ts";
import { MESSENGER, POST_OFFICE, stuckText, toTellMaster, toWake, wakeText } from "./waker.ts";
import { ensureWork, folders, recycle, toClean } from "./work.ts";

export type OfficeSettings = {
  residentsRoot: string; workRoot: string; team: string[]; tellMasterAfter: number; sweepMs: number; restMs: number; workKeepMs: number; limitWaitMs: number;
};

const LIMIT_TIME = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
});

export class PostOffice {
  private settings: OfficeSettings;
  private clis: CliResident[];
  private busyWork: () => ReadonlySet<string>;
  private afterSweep: (now: Date) => void;
  private pending = false;
  private timer: NodeJS.Timeout | undefined;
  private stopped = false;

  /** clis：郵便局がCLIで起こす住人（Holoは拡張が起こす）。
   *  busyWork：コマンドが動いている作業場（Holoの手。片付けない） */
  constructor(
    settings: OfficeSettings,
    clis: CliResident[] = [],
    busyWork: () => ReadonlySet<string> = () => new Set(),
    afterSweep: (now: Date) => void = () => {},
  ) {
    this.settings = settings;
    this.clis = clis;
    this.busyWork = busyWork;
    this.afterSweep = afterSweep;
  }

  /** 手紙が出たら：作業場の名前があれば作り、すぐに見直す。 */
  onSent(letter: Letter): void {
    if (letter.work) ensureWork(this.settings.workRoot, letter.work);
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

  sweep(now: Date): void {
    if (this.stopped) return;
    const { residentsRoot, workRoot, team, tellMasterAfter } = this.settings;
    const linesOf = Object.fromEntries(team.map(r => [r, readAll(residentsRoot, r)]));

    for (const [resident, lines] of Object.entries(linesOf)) {
      for (const stuck of toTellMaster(lines, tellMasterAfter)) {
        // 言付けはHoloが伝える。Holo自身が応えないときは、拡張アイコンの印でMasterに残す
        // Holo自身の手紙は中継できないので、tell の行がそのまま知らせになる。
        // /holo/status の stuck が増え、拡張アイコンの ! に出る。
        const how = resident === MESSENGER ? "拡張の印" : this.relay(stuck, now);
        const tell: Tell = { kind: "tell", ts: now.toISOString(), letter: stuck.id, how };
        append(residentsRoot, resident, tell);
        lines.push(tell); // Masterに回した手紙では、この見直しでも起こさない
        console.log(`${now.toISOString()} tell master about ${stuck.id} ${how}`);
      }
    }

    for (const name of toClean(folders(workRoot), Object.values(linesOf), this.busyWork(), now, this.settings.workKeepMs)) {
      console.log(`${now.toISOString()} recycle work ${name} ${recycle(workRoot, name)}`);
    }

    for (const cli of this.clis) {
      const letters = toWake(linesOf[cli.name] ?? [], cli.awake(), now, this.settings.restMs);
      if (letters.length === 0) continue;
      cli.wake(letters, wakeText(cli.name, letters.length), now);
      console.log(`${now.toISOString()} wake ${cli.name} for ${letters.join(",")}`);
    }
    this.afterSweep(now);
  }

  private relay(stuck: Unfinished, now: Date): string {
    append(this.settings.residentsRoot, MESSENGER, {
      kind: "letter", ts: now.toISOString(), id: newLetterId(now), from: POST_OFFICE, to: MESSENGER,
      body: `Masterに伝えて：${stuckText(stuck)}。どうするかはMasterに決めてもらって。`, based_on: stuck.id,
    });
    return `${MESSENGER}への手紙`;
  }

  private notifyLimit(resident: string, stop: Stop): void {
    const pending = unfinished(readAll(this.settings.residentsRoot, resident));
    for (const letter of pending) {
      if (letter.from === resident || !this.settings.team.includes(letter.from)) continue;
      const key = `limit:${resident}:${stop.ts}:${letter.id}`;
      const senderLines = readAll(this.settings.residentsRoot, letter.from);
      if (senderLines.some(line => line.kind === "letter" && line.from === POST_OFFICE && line.based_on === key)) continue;

      const when = stop.untilKnown === false || !stop.until
        ? `起きる時刻は分からない。郵便局は${Math.round(this.settings.limitWaitMs / 60_000)}分後にもう一度試す。`
        : `${LIMIT_TIME.format(new Date(stop.until))}まで眠っている。`;
      const choice = stop.untilKnown === false
        ? "起きる時刻が分からないので、決まりの順で代わりに頼んで。"
        : `起きるまでが${this.settings.limitWaitMs / 3_600_000}時間以内なら待ち、それより先なら決まりの順で代わりに頼んで。`;
      append(this.settings.residentsRoot, letter.from, {
        kind: "letter",
        ts: stop.ts,
        id: newLetterId(new Date(stop.ts)),
        from: POST_OFFICE,
        to: letter.from,
        body: `${resident}は上限で${when}あなたの手紙 ${letter.id} はそれまで届かない。${choice}`,
        based_on: key,
      });
    }
  }

}
