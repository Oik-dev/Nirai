// 郵便局の見回り。手紙が出たとき・止まった知らせのとき・一定の間隔で、全体を生ログから見直す。
// 見直すたびに決めることは3つ：Masterに知らせること、片付ける作業場、起こすCLIの住人。

import type { CliResident } from "./cli.ts";
import { append, type Letter, newLetterId, readAll, type Tell, type Unfinished } from "./letters.ts";
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

  /** clis：郵便局がCLIで起こす住人（Holoは拡張が起こす）。
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

}
