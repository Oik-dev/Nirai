import { spawn } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync } from 'node:fs';
import { readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { minds, MIND_HOST, type SeaSettings } from './settings.ts';

export type Resident = { name: string; idea: string; port: number };

export type BodyResident = { name: string; idea: string };

// 体のある全員。名前を登録する帳簿は置かず、各イデアのbodyから調べる。
export async function bodyResidents(settings: SeaSettings): Promise<BodyResident[]> {
  const entries = await readdir(settings.residentsRoot, { withFileTypes: true });
  const bodies = await Promise.all(entries.filter(entry => entry.isDirectory()).map(async entry => {
    const idea = join(settings.residentsRoot, entry.name);
    const file = await stat(join(idea, 'body', 'avatar.vrm')).catch(() => undefined);
    return file?.isFile() ? { name: entry.name, idea } : null;
  }));
  return bodies.filter((body): body is BodyResident => body !== null).sort((a, b) => a.name.localeCompare(b.name));
}

// 会話相手は体の名前順ではなく、精神の設定に載っている住人だけ。
export async function seaResident(settings: SeaSettings): Promise<Resident | undefined> {
  const bodies = await bodyResidents(settings);
  for (const [name, mind] of Object.entries(minds)) {
    const body = bodies.find(candidate => candidate.name === name);
    if (body) return { ...body, port: settings.mindPort ?? mind.port };
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
    throw new Error('精神の起動に必要なPythonまたはmind/app/server.pyがありません。');
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