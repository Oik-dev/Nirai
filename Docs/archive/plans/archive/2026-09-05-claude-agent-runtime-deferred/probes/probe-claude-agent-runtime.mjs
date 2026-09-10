import { spawn } from 'node:child_process';

import { existsSync } from 'node:fs';
import { join } from 'node:path';

const home = process.env.USERPROFILE || process.env.HOME || '';
const local = process.env.LOCALAPPDATA || '';
const appdata = process.env.APPDATA || '';
const candidates = [
  join(home, '.local', 'bin', 'claude.exe'),
  join(local, 'Programs', 'claude', 'claude.exe'),
  join(local, 'Claude', 'claude.exe'),
  join(appdata, 'npm', 'claude.cmd'),
  join(appdata, 'npm', 'claude.exe'),
].filter(Boolean);
console.log(JSON.stringify({ candidates: candidates.map((path) => ({ path, exists: existsSync(path) })) }));

const commands = [
  ['--version'],
  ['--help'],
];

function run(args) {
  return new Promise((resolve) => {
    const child = spawn('claude', args, { windowsHide: true, shell: false });
    let out = '';
    let err = '';
    child.stdout?.on('data', (c) => { out += c.toString(); });
    child.stderr?.on('data', (c) => { err += c.toString(); });
    child.on('error', (error) => resolve({ args, error: String(error) }));
    child.on('close', (code) => resolve({ args, code, stdout: out.slice(0, 40000), stderr: err.slice(0, 10000) }));
  });
}

for (const args of commands) {
  const result = await run(args);
  console.log(JSON.stringify(result));
}
