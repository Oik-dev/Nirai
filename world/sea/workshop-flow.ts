import { randomInt } from 'node:crypto';

export type WorkshopWish = { name: string; ref: string };
export type WorkshopDescription = { text: string; seconds: number };
export type WorkshopDescribe =
  | { kind: 'ready'; description: WorkshopDescription }
  | { kind: 'invalid' } // 脳の形が違う（502）。この願いだけfailed。
  | { kind: 'unavailable' }; // 精神への道がない（503）。朝を終える。
export type WorkshopGenerate =
  | { kind: 'candidate'; bytes: Buffer }
  | { kind: 'rejected' } // このseedで作れなかった。次のseedへ。
  | { kind: 'unavailable' }; // 子プロセス/モデルが使えない。failedにしない。

export type WorkshopSession = {
  generate(description: WorkshopDescription, seed: number, signal: AbortSignal): Promise<WorkshopGenerate>;
  gate(bytes: Buffer, signal: AbortSignal): Promise<boolean>;
  close(): Promise<void>; // generatorとChromeの両方が終わってから戻る
};

export type WorkshopFlow = {
  describe(wish: WorkshopWish, signal: AbortSignal): Promise<WorkshopDescribe>;
  open(signal: AbortSignal): Promise<WorkshopSession>;
  continueWork(signal: AbortSignal): Promise<boolean>; // 時間とMasterの手元
  install(wish: WorkshopWish, bytes: Buffer): Promise<void>;
  record(wish: WorkshopWish, kind: 'learned' | 'failed'): Promise<void>;
};

export type WorkshopCounts = { learned: number; failed: number };

// 本人の言葉と英語の説明は、この呼び出しのメモリとイデアの記録にだけ置く。
// ここから例外をログに出さず、作れなかった本人固有の願いだけfailedとする。
export async function runWorkshopWishes(wishes: WorkshopWish[], seeds: number,
  flow: WorkshopFlow, signal: AbortSignal): Promise<WorkshopCounts> {
  const counts: WorkshopCounts = { learned: 0, failed: 0 };
  if (signal.aborted || !Number.isSafeInteger(seeds) || seeds < 1 || seeds > 32) return counts;

  const ready: { wish: WorkshopWish; description: WorkshopDescription }[] = [];
  for (const wish of wishes) {
    if (signal.aborted) return counts;
    let result: WorkshopDescribe;
    try { result = await flow.describe(wish, signal); }
    catch { return counts; }
    if (signal.aborted) return counts;
    if (result.kind === 'unavailable') return counts;
    if (result.kind === 'invalid') {
      if (!await flow.continueWork(signal)) return counts;
      await flow.record(wish, 'failed');
      counts.failed++;
    } else ready.push({ wish, description: result.description });
  }
  if (!ready.length || signal.aborted) return counts;

  let session: WorkshopSession;
  try { session = await flow.open(signal); }
  catch { return counts; } // 起動、空き待ち、載せる段の失敗は願いに数えない。
  try {
    for (const { wish, description } of ready) {
      if (signal.aborted || !await flow.continueWork(signal)) return counts;
      let accepted = false;
      const usedSeeds = new Set<number>();
      for (let i = 0; i < seeds; i++) {
        if (signal.aborted || !await flow.continueWork(signal)) return counts;
        let seed: number;
        do { seed = randomInt(0, 0x80000000); } while (usedSeeds.has(seed));
        usedSeeds.add(seed);
        let generated: WorkshopGenerate;
        try { generated = await session.generate(description, seed, signal); }
        catch { return counts; } // 子が死んだ/通信が切れたか判別できないので安全側へ。
        if (signal.aborted) return counts;
        if (generated.kind === 'unavailable') return counts;
        if (generated.kind === 'rejected') continue;
        let pass: boolean;
        try { pass = await session.gate(generated.bytes, signal); }
        catch { return counts; } // 判定不能は不合格と区別する。
        if (signal.aborted) return counts;
        if (!pass) continue;
        if (!await flow.continueWork(signal) || signal.aborted) return counts;
        await flow.install(wish, generated.bytes);
        if (signal.aborted) return counts;
        await flow.record(wish, 'learned');
        counts.learned++;
        accepted = true;
        break;
      }
      if (!accepted && !signal.aborted) {
        if (!await flow.continueWork(signal) || signal.aborted) return counts;
        await flow.record(wish, 'failed');
        counts.failed++;
      }
    }
  } finally {
    await session.close();
  }
  return counts;
}
