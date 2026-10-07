// 海の設定の正本。住人のイデアと精神のコードは、番人から渡された場所だけを見る。
import { join } from 'node:path';

export const SEA_HOST = '127.0.0.1';
export const SEA_PORT = 47810;
export const MIND_HOST = '127.0.0.1';
export const minds: Record<string, { port: number }> = { Serina: { port: 8765 } };

export type SeaSettings = {
  residentsRoot: string;
  sourceRepo: string;
  port: number;
  mindPort?: number;
  python: string;
  script: string;
};

export function seaSettings(env: NodeJS.ProcessEnv = process.env): SeaSettings {
  if (!env.NIRAI_RESIDENTS) throw new Error('NIRAI_RESIDENTSが指定されていません。');
  if (!env.NIRAI_SOURCE_REPO) throw new Error('NIRAI_SOURCE_REPOが指定されていません。');
  return {
    residentsRoot: env.NIRAI_RESIDENTS,
    sourceRepo: env.NIRAI_SOURCE_REPO,
    port: Number(env.NIRAI_SEA_PORT ?? SEA_PORT),
    mindPort: env.NIRAI_MIND_PORT ? Number(env.NIRAI_MIND_PORT) : undefined,
    // 精神は対話コンソールを使わない常駐HTTPサービスなので、
    // Windows Terminal を生やさない同じ venv の pythonw.exe で起こす。
    python: join(env.NIRAI_SOURCE_REPO, 'mind', '.venv', 'Scripts', 'pythonw.exe'),
    script: join(env.NIRAI_SOURCE_REPO, 'mind', 'app', 'server.py'),
  };
}
