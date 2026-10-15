// 海の設定の正本。住人のイデアと精神のコードは、番人から渡された場所だけを見る。
import { join } from 'node:path';

export const SEA_HOST = '127.0.0.1';
export const SEA_PORT = 47810;
/** 海のポート。試しの海は NIRAI_SEA_PORT で別のポートにする（番人・郵便局・海が同じここから読む）。 */
export const seaPort = (env: NodeJS.ProcessEnv = process.env) => Number(env.NIRAI_SEA_PORT ?? SEA_PORT);
// 入れ替えのとき、海が返答の終わりを待つ最長。番人も、これだけ待ってから海を止める。
export const SEA_DRAIN_MS = 3 * 60_000;
// Pulseで窓辺へ訪問したあと、返事がなければこの時間で活動へ戻る。
export const APPROACH_DURATION_MS = 3 * 60_000;
export const MIND_HOST = '127.0.0.1';
export const minds: Record<string, { port: number }> = { Serina: { port: 8765 } };

export type SeaSettings = {
  residentsRoot: string;
  sourceRepo: string;
  port: number;
  mindPort?: number;
  python: string;
  script: string;
  workshop: {
    startHour: number;
    endHour: number;
    awayMinutes: number;
    seeds: number;
    freeMemoryWaitSeconds: number;
    python: string;
  };
};

export function seaSettings(env: NodeJS.ProcessEnv = process.env): SeaSettings {
  if (!env.NIRAI_RESIDENTS) throw new Error('NIRAI_RESIDENTSが指定されていません。');
  if (!env.NIRAI_SOURCE_REPO) throw new Error('NIRAI_SOURCE_REPOが指定されていません。');
  return {
    residentsRoot: env.NIRAI_RESIDENTS,
    sourceRepo: env.NIRAI_SOURCE_REPO,
    port: seaPort(env),
    mindPort: env.NIRAI_MIND_PORT ? Number(env.NIRAI_MIND_PORT) : undefined,
    // 精神は対話コンソールを使わない常駐HTTPサービスなので、
    // Windows Terminal を生やさない同じ venv の pythonw.exe で起こす。
    python: join(env.NIRAI_SOURCE_REPO, 'mind', '.venv', 'Scripts', 'pythonw.exe'),
    script: join(env.NIRAI_SOURCE_REPO, 'mind', 'app', 'server.py'),
    workshop: {
      startHour: 4,
      endHour: 10,
      awayMinutes: 30,
      seeds: 4,
      freeMemoryWaitSeconds: 180,
      // Kimodo PoCの既存venvのみ。モデルの取得や外のAPIは使わない。
      python: env.NIRAI_WORKSHOP_PYTHON ?? 'D:/Products/ResidentMotion-PoC/.venv/Scripts/python.exe',
    },
  };
}
