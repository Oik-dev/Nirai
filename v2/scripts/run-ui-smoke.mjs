import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import electronExe from "electron";

const here = dirname(fileURLToPath(import.meta.url));
const appPath = resolve(here, "..", "ui-smoke");
const logPath = resolve(tmpdir(), `nirai-v2-ui-smoke-${randomUUID()}.log`);
const dataRoot = mkdtempSync(resolve(tmpdir(), "nirai-v2-ui-smoke-"));

function dumpLog() {
  if (!existsSync(logPath)) return;
  process.stdout.write(readFileSync(logPath, "utf8"));
  rmSync(logPath, { force: true });
}

const env = {
  ...process.env,
  NIRAI_V2_SMOKE: "",
  NIRAI_V2_UI_SMOKE: "1",
  NIRAI_V2_SMOKE_LOG: logPath,
  NIRAI_V2_SMOKE_DATA_ROOT: dataRoot,
};
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(electronExe, [appPath], {
  env,
  stdio: "inherit",
  windowsHide: true,
});

let timedOut = false;
const timeout = setTimeout(() => {
  timedOut = true;
  console.error("Electron UI smoke runner timeout");
  dumpLog();
  child.kill();
  process.exitCode = 2;
}, 45_000);

child.once("error", (error) => {
  clearTimeout(timeout);
  console.error(error);
  dumpLog();
  process.exitCode = 1;
});

child.once("exit", (code) => {
  clearTimeout(timeout);
  dumpLog();
  rmSync(dataRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  process.exitCode = timedOut ? 2 : code ?? 1;
});
