import { spawn } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync } from 'node:fs';
import { readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { minds, MIND_HOST, type SeaSettings } from './settings.ts';

export type Resident = { name: string; idea: string; port: number };

export async function seaResident(settings: SeaSettings): Promise<Resident | undefined> {
  const entries = await readdir(settings.residentsRoot, { withFileTypes: true });
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    if (!entry.isDirectory()) continue;
    const idea = join(settings.residentsRoot, entry.name);
    if (!(await stat(join(idea, 'body', 'avatar.vrm')).catch(() => undefined))?.isFile()) continue;
    const port = settings.mindPort ?? minds[entry.name]?.port;
    if (!port) throw new Error(`精神への道が設定されていません：${entry.name}`);
    return { name: entry.name, idea, port };
  }
}

export async function mindState(resident: Resident): Promise<'up' | 'down'> {
  try {
    const response = await fetch(`http://${MIND_HOST}:${resident.port}/api/state`, { signal: AbortSignal.timeout(1000) });
    await response.body?.cancel();
    return response.ok ? 'up' : 'down';
  } catch { return 'down'; }
}

async function waitUntilUp(resident: Resident, timeoutMs: number): Promise<void> {
  const until = Date.now() + timeoutMs;
  do {
    if (await mindState(resident) === 'up') return;
    await new Promise(resolve => setTimeout(resolve, 100));
  } while (Date.now() < until);
  throw new Error('精神が起動を完了しませんでした。');
}

export async function wakeMind(settings: SeaSettings, resident: Resident, timeoutMs = 10_000): Promise<void> {
  if (await mindState(resident) === 'up') return;
  if (!existsSync(settings.python) || !existsSync(settings.script)) {
    throw new Error('精神の起動に必要なpython.exeまたはmind/app/server.pyがありません。');
  }
  const logs = join(resident.idea, 'data', 'logs');
  mkdirSync(logs, { recursive: true });
  const fd = openSync(join(logs, 'mind.log'), 'a');
  try {
    await new Promise<void>((resolve, reject) => {
      const child = spawn(settings.python, [settings.script], {
        cwd: join(settings.sourceRepo, 'mind'), detached: true, windowsHide: true,
        stdio: ['ignore', fd, fd],
        env: { ...process.env, NIRAI_IDEA: resident.idea, NIRAI_MIND_PORT: String(resident.port), PYTHONUTF8: '1' },
      });
      child.once('error', reject);
      child.once('spawn', () => { child.unref(); resolve(); });
    });
  } finally { closeSync(fd); }
  await waitUntilUp(resident, timeoutMs);
}