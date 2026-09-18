import { spawn } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const appPath = resolve(here, "..", "smoke");
const electronExe = resolve(here, "..", "..", "world", "node_modules", "electron", "dist", "electron.exe");
const logPath = resolve(tmpdir(), `nirai-v2-smoke-${randomUUID()}.log`);

function dumpLog() {
  if (!existsSync(logPath)) return;
  process.stdout.write(readFileSync(logPath, "utf8"));
  rmSync(logPath, { force: true });
}

const child = spawn(electronExe, [appPath], {
  env: {
    ...process.env,
    NIRAI_V2_SMOKE: "1",
    NIRAI_V2_SMOKE_LOG: logPath,
  },
  stdio: "inherit",
  windowsHide: true,
});

const timeout = setTimeout(() => {
  console.error("Electron smoke runner timeout");
  dumpLog();
  child.kill();
  process.exitCode = 2;
}, 15_000);

child.once("error", (error) => {
  clearTimeout(timeout);
  console.error(error);
  dumpLog();
  process.exitCode = 1;
});

child.once("exit", (code) => {
  clearTimeout(timeout);
  dumpLog();
  process.exitCode = code ?? 1;
});
