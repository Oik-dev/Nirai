// 本番の郵便局の番人。タスクスケジューラ「Nirai Post」が、Masterのログオン時に起こす（register-tasks.ps1）。
// - 前の郵便局や番人が残っていたら止める（起こし直したとき、古い郵便局がポートを持ったままだと、新しい郵便局が起きられない）
// - 郵便局（server.ts --live）を子として動かし、出力とエラーを全部 world/runtime/post.log に残す
// - 郵便局が止まったら、1分後に起こし直す
// タスクスケジューラ自身の起こし直しは使えない。画面を出さないために挟む conhost --headless が、
// nodeが失敗しても成功（0）を返すので、落ちたと分からない（2026-10-04に確かめた）。

import { spawn, spawnSync } from "node:child_process";
import { createWriteStream, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";

const LOG_FILE = join(import.meta.dirname, "..", "runtime", "post.log");
const SERVER = join(import.meta.dirname, "server.ts");
const RESTART_MS = 60_000;

mkdirSync(dirname(LOG_FILE), { recursive: true });
const log = createWriteStream(LOG_FILE, { flags: "a" });
const note = (text: string) => log.write(`${new Date().toISOString()} keeper: ${text}\n`);

/** 前の番人と郵便局を止める（自分は除く）。 */
function stopLeftovers(): void {
  const script = `Get-CimInstance Win32_Process -Filter "Name='node.exe'" | ` +
    `Where-Object { $_.ProcessId -ne ${process.pid} -and $_.CommandLine -match 'post[\\\\/](keeper|server)\\.ts' } | ` +
    `ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }`;
  const result = spawnSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], { windowsHide: true, encoding: "utf8" });
  const stopped = result.stdout.trim().split(/\s+/).filter(Boolean);
  if (stopped.length) note(`stopped leftovers pid=${stopped.join(",")}`);
}

function run(): void {
  const child = spawn(process.execPath, ["--no-warnings", SERVER, "--live"], {
    cwd: join(import.meta.dirname, ".."), windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.pipe(log, { end: false });
  child.stderr.pipe(log, { end: false });
  note(`started post office pid=${child.pid}`);
  child.on("exit", code => {
    note(`post office stopped code=${code}; restart in ${RESTART_MS / 1000}s`);
    setTimeout(run, RESTART_MS);
  });
}

stopLeftovers();
run();
