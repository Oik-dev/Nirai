// 郵便局の見回り。手紙が出たとき・止まった知らせのとき・一定の間隔で、全体を生ログから見直す。
// 見直すたびに決めること：Masterに知らせること、片付ける作業場、起こすCLIの住人、最後に本番版の入れ替え確認。

import type { CliResident } from "./cli.ts";
import { append, scopeLines, workKey, type Letter, newLetterId, readAll, type Stop, type Tell, type Unfinished, unfinished, activeLimit } from "./letters.ts";
import { MESSENGER, POST_OFFICE, stuckText, toTellMaster, toWake, WAKE_TEXT } from "./waker.ts";
import { ensureWork, folders, removeWork, toClean } from "./work.ts";

export type OfficeSettings = {
  residentsRoot: string; workRoot: string; team: string[]; tellMasterAfter: number; sweepMs: number; restMs: number; workKeepMs: number; limitWaitMs: number;
  repoRoot?: string;
  maxConcurrent?: Record<string, number>;
};

const LIMIT_TIME = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
});

export class PostOffice {
  private settings: OfficeSettings;
  private clis: CliResident[];
  private busyWork: () => ReadonlySet<string>;
  private waiting: () => boolean;
  private afterSweep: (now: Date) => void;
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
    waiting: () => boolean = () => false,
  ) {
    this.settings = settings;
    this.clis = clis;
    this.busyWork = busyWork;
    this.afterSweep = afterSweep;
    this.waiting = waiting;
  }

  /** 手紙が出たら：作業場の名前があれば作り、すぐに見直す。 */
  onSent(letter: Letter): void {
    if (letter.work) {
      ensureWork(this.settings.workRoot, letter.work);
      this.cleanupFailures.delete(workKey(letter.work));
    }
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
      // 進捗と滞留は住人全体でなく部屋単位。別室のnote/doneで帳消しにしない。
      const groups = [...new Set(unfinished(lines).map(letter => workKey(letter.work ?? "")))]
        .map(work => scopeLines(lines, work || undefined));
      for (const group of groups) for (const stuck of toTellMaster(group, tellMasterAfter)) {
        // 受付自身だけは中継先がない。作業場のHoloは受付への手紙で知らせる。
        const how = resident === MESSENGER && !stuck.work ? "拡張の印" : this.relay(stuck, group, now);
        const tell: Tell = { kind: "tell", ts: now.toISOString(), letter: stuck.id, how };
        append(residentsRoot, resident, tell);
        lines.push(tell); // Masterに回した手紙では、この見直しでも起こさない
        console.log(`${now.toISOString()} tell master about ${stuck.id} ${how}`);
      }
    }

    for (const name of toClean(folders(workRoot), Object.values(linesOf), this.busyWork(), now, this.settings.workKeepMs)) {
      const result = removeWork(workRoot, name, this.settings.repoRoot);
      const key = workKey(name);
      // 保存失敗で毎分同じ行をpost.logへ書かず、変化したときだけ記録する。
      if (result === "removed" || this.cleanupFailures.get(key) !== result) {
        console.log(`${now.toISOString()} remove work ${name} ${result}`);
      }
      if (result === "removed") this.cleanupFailures.delete(key);
      else this.cleanupFailures.set(key, result);
    }

    for (const cli of this.clis) {
      const lines = linesOf[cli.name] ?? [];
      if (this.waiting() || activeLimit(lines, now)) continue; // 版替えと使用上限は住人全体に効く
      const pending = unfinished(lines);
      const workKeys = [...new Set(pending.map(letter => workKey(letter.work ?? "")))].sort((a, b) => {
        const earliest = (work: string) => pending.find(letter => workKey(letter.work ?? "") === work)?.ts ?? "";
        return earliest(a).localeCompare(earliest(b));
      });
      const max = this.settings.maxConcurrent?.[cli.name] ?? 1;
      let inFlight = cli.awakeCount?.() ?? (cli.awake() ? 1 : 0);
      for (const key of workKeys) {
        if (inFlight >= max) break;
        const work = key || undefined;
        if (cli.awakeWork?.(work) ?? cli.awake()) continue;
        const letters = toWake(scopeLines(lines, work), false, now, this.settings.restMs);
        if (!letters.length) continue;
        cli.wake(letters, WAKE_TEXT, now, work);
        inFlight++;
        console.log(`${now.toISOString()} wake ${cli.name} ${work ?? "受付"} for ${letters.join(",")}`);
      }
    }
    this.afterSweep(now);
  }

  private relay(stuck: Unfinished, lines: ReturnType<typeof scopeLines>, now: Date): string {
    append(this.settings.residentsRoot, MESSENGER, {
      kind: "letter", ts: now.toISOString(), id: newLetterId(now), from: POST_OFFICE, to: MESSENGER,
      body: `Masterに伝えて：${stuckText(stuck, lines)}どうするかはMasterに決めてもらって。`, based_on: stuck.id,
    });
    return `${MESSENGER}への手紙`;
  }

  /** 上限の知らせは、出した人ではなく受付のHoloへ出す。限りのある脳を、知らせのためだけに起こさない。 */
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
        body: `${resident}は上限で${when}${letter.from}の手紙 ${letter.id} はそれまで届かない。${choice}`,
        based_on: key,
      });
    }
  }

}
