// 郵便局の見回り。手紙が出たとき・止まった知らせのとき・一定の間隔で、全体を生ログから見直す。
// 見直すたびに決めることは3つ：Masterに知らせること、片付ける作業場、起こすCLIの住人。

import type { CliResident } from "./cli.ts";
import { append, type Letter, newLetterId, readAll, type Unfinished } from "./letters.ts";
import { notifyMaster } from "./notify.ts";
import { MESSENGER, POST_OFFICE, stuckText, toTellMaster, toWake, wakeText } from "./waker.ts";
import { ensureWork, folders, recycle, toClean } from "./work.ts";

export type OfficeSettings = {
  residentsRoot: string; workRoot: string; team: string[]; tellMasterAfter: number; sweepMs: number; restMs: number;
};

export class PostOffice {
  private settings: OfficeSettings;
  private clis: CliResident[];
  private busyWork: () => ReadonlySet<string>;
  private pending = false;

  /** clis：郵便局がCLIで起こす住人（Holoは拡張が、Claudeはセッションの始めに自分で見る）。
   *  busyWork：コマンドが動いている作業場（Holoの手。片付けない） */
  constructor(settings: OfficeSettings, clis: CliResident[] = [], busyWork: () => ReadonlySet<string> = () => new Set()) {
    this.settings = settings;
    this.clis = clis;
    this.busyWork = busyWork;
  }

  /** 手紙が出たら：作業場の名前があれば作り、すぐに見直す。 */
  onSent(letter: Letter): void {
    if (letter.work) ensureWork(this.settings.workRoot, letter.work);
    this.soon();
  }

  /** すぐに見直す（同じ瞬間に何度呼ばれても1回）。 */
  soon(): void {
    if (this.pending) return;
    this.pending = true;
    setImmediate(() => {
      this.pending = false;
      this.sweep(new Date());
    });
  }

  start(): void {
    this.soon();
    setInterval(() => this.sweep(new Date()), this.settings.sweepMs).unref();
  }

  sweep(now: Date): void {
    const { residentsRoot, workRoot, team, tellMasterAfter } = this.settings;
    const linesOf = Object.fromEntries(team.map(r => [r, readAll(residentsRoot, r)]));

    for (const [resident, lines] of Object.entries(linesOf)) {
      for (const stuck of toTellMaster(lines, tellMasterAfter)) {
        // 言付けはHoloが伝える。Holo自身が応えないときは、中継できないので、郵便局がWindowsの通知で直接伝える
        const how = resident === MESSENGER ? this.notice(stuck) : this.relay(stuck, now);
        if (how) append(residentsRoot, resident, { kind: "tell", ts: now.toISOString(), letter: stuck.id, how });
        console.log(`${now.toISOString()} tell master about ${stuck.id} ${how ?? "failed"}`);
      }
    }

    for (const name of toClean(folders(workRoot), Object.values(linesOf), this.busyWork())) {
      console.log(`${now.toISOString()} recycle work ${name} ${recycle(workRoot, name)}`);
    }

    for (const cli of this.clis) {
      const letters = toWake(linesOf[cli.name] ?? [], cli.awake(), now, this.settings.restMs);
      if (letters.length === 0) continue;
      cli.wake(letters, wakeText(cli.name, letters.length), now);
      console.log(`${now.toISOString()} wake ${cli.name} for ${letters.join(",")}`);
    }
  }

  private relay(stuck: Unfinished, now: Date): string {
    append(this.settings.residentsRoot, MESSENGER, {
      kind: "letter", ts: now.toISOString(), id: newLetterId(now), from: POST_OFFICE, to: MESSENGER,
      body: `Masterに伝えて：${stuckText(stuck)}。どうするかはMasterに決めてもらって。`, based_on: stuck.id,
    });
    return `${MESSENGER}への手紙`;
  }

  private notice(stuck: Unfinished): string | undefined {
    return notifyMaster("Nirai 郵便局", `${stuckText(stuck)}。Holoの部屋を見てあげて。`) ? "Windowsの通知" : undefined;
  }
}
