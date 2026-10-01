import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import electronExe from 'electron';

// Own the profile and working directory until Electron has closed its handles.
// Windows spelling can create malformed relative paths in a sandbox; those
// writes must stay inside this smoke's disposable directory, not the checkout.
export async function runElectronSmoke({ name, appPath, timeoutMs, environment = {}, rootVariable = 'NIRAI_V2_SMOKE_DATA_ROOT' }) {
  const target = resolve(appPath);
  const dataRoot = mkdtempSync(join(tmpdir(), `nirai-v2-${name}-smoke-`));
  const logPath = join(dataRoot, 'smoke.log');
  const env = {
    ...process.env,
    ...environment,
    [rootVariable]: dataRoot,
    NIRAI_V2_SMOKE_LOG: logPath,
  };
  // Preserve caller-relative exports when the child changes working directory.
  if (env.NIRAI_V2_UI_CAPTURE_DIR) env.NIRAI_V2_UI_CAPTURE_DIR = resolve(env.NIRAI_V2_UI_CAPTURE_DIR);
  delete env.ELECTRON_RUN_AS_NODE;
  let timer;
  try {
    const child = spawn(electronExe, [target], { cwd: dataRoot, env, stdio: 'inherit', windowsHide: true });
    let timedOut = false;
    let failed = false;
    timer = setTimeout(() => {
      timedOut = true;
      console.error(`Electron ${name} smoke runner timeout`);
      child.kill();
    }, timeoutMs);
    process.exitCode = await new Promise(resolve => {
      child.once('error', error => {
        failed = true;
        console.error(error);
      });
      child.once('close', code => resolve(timedOut ? 2 : failed ? 1 : code ?? 1));
    });
  } finally {
    clearTimeout(timer);
    try {
      if (existsSync(logPath)) process.stdout.write(readFileSync(logPath, 'utf8'));
    } finally {
      rmSync(dataRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    }
  }
}
