// Holoの部屋（ChatGPTの専用の会話）と郵便局のあいだ。拡張が、会話の通信の始まりと終わりを知らせ、
// 起こす一言を取りに来る。Holoが起きているか（返事をしている最中か）は、画面ではなく通信で決める。

import { append, readAll } from "./letters.ts";
import { toWake, wakeText } from "./waker.ts";

export type NetReport = { phase: "start" | "end" | "error"; id: string; method: string; path: string; status?: number; error?: string };

export class HoloRoom {
  private inflight = new Map<string, number>(); // 返事の通信の id → 始まった時刻
  private residentsRoot: string;
  private settings: { restMs: number; busyLimitMs: number; replyPath: RegExp };

  constructor(residentsRoot: string, settings: { restMs: number; busyLimitMs: number; replyPath: RegExp }) {
    this.residentsRoot = residentsRoot;
    this.settings = settings;
  }

  /** 返事の通信が続いている間は起きている。知らせが途切れても、busyLimit を過ぎたら止まったとみなす。 */
  awake(now: Date): boolean {
    for (const [id, since] of this.inflight) if (now.getTime() - since > this.settings.busyLimitMs) this.inflight.delete(id);
    return this.inflight.size > 0;
  }

  net(report: NetReport, now: Date): void {
    if (report.method !== "POST" || !this.settings.replyPath.test(report.path)) return;
    if (report.phase === "start") {
      this.inflight.set(report.id, now.getTime());
      return;
    }
    if (!this.inflight.delete(report.id) || this.awake(now)) return;
    // 郵便局が起こした後の返事が終わったときだけ、止まったと書く（Masterとの会話だけなら書かない）
    const lines = readAll(this.residentsRoot, "Holo");
    const last = lines.findLast(l => l.kind === "wake" || l.kind === "stop");
    if (last?.kind !== "wake") return;
    // 止まった理由は見分けない。ChatGPTは返事を書き終えても、ページ自身が通信を閉じたり
    // （ERR_ABORTED・ERR_FAILED）、ふつうに終えたりする。どう終わったかは、記録として残すだけ
    const ended = report.error ?? (report.status && report.status !== 200 ? `HTTP ${report.status}` : undefined);
    append(this.residentsRoot, "Holo", { kind: "stop", ts: now.toISOString(), how: "exit", ...(ended ? { detail: ended } : {}) });
  }

  /** 拡張が取りに来る一言。起こさないときは undefined。 */
  next(now: Date): { text: string; letters: string[] } | undefined {
    const letters = toWake(readAll(this.residentsRoot, "Holo"), this.awake(now), now, this.settings.restMs);
    return letters.length ? { text: wakeText("Holo", letters.length), letters } : undefined;
  }

  /** 拡張が一言を送れたら、起こしたと書く。送れなかったら書かない（次の見直しでまた試す）。 */
  sent(result: { ok: boolean; letters: string[]; reason?: string }, now: Date): void {
    if (result.ok) append(this.residentsRoot, "Holo", { kind: "wake", ts: now.toISOString(), letters: result.letters, how: "holo tab" });
  }
}
