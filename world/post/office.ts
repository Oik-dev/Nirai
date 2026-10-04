// 郵便局の見回り。手紙が出たとき・止まった知らせのとき・一定の間隔で、全体を生ログから見直す。
// 見直すたびに決めることは3つ：Masterに知らせる手紙、片付ける作業場、（CLIの住人を起こすこと）。

import { append, type Letter, newLetterId, readAll } from "./letters.ts";
import { POST_OFFICE, tellMasterText, toTellMaster } from "./waker.ts";
import { ensureWork, folders, recycle, toClean } from "./work.ts";

export type OfficeSettings = { residentsRoot: string; workRoot: string; team: string[]; tellMasterAfter: number; sweepMs: number };

export class PostOffice {
  private settings: OfficeSettings;
  private pending = false;

  constructor(settings: OfficeSettings) {
    this.settings = settings;
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

    for (const stuck of toTellMaster(linesOf, tellMasterAfter)) {
      append(residentsRoot, "Holo", {
        kind: "letter", ts: now.toISOString(), id: newLetterId(now), from: POST_OFFICE, to: "Holo",
        body: tellMasterText(stuck), based_on: stuck.id,
      });
      console.log(`${now.toISOString()} tell master about ${stuck.id}`);
    }

    for (const name of toClean(folders(workRoot), Object.values(linesOf))) {
      console.log(`${now.toISOString()} recycle work ${name} ${recycle(workRoot, name) ? "ok" : "failed"}`);
    }
  }
}
