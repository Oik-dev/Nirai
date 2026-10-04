// ChatGPT（Holo）から郵便局へのトンネル。OpenAIの tunnel-client を、保存された設定（~\.config\tunnel-client\nirai.yaml。
// 2026-10-04 に v2 の「Nirai」接続のトンネルを付け替えて作った）で動かす。
// 郵便局が聞き始めてから起こし、郵便局と一緒に1つだけ動かす（同じトンネルは1つしか動かせない）。
// 前の郵便局が残したトンネルは、起こす前に止める。止まったら1分後に起こし直す。

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const PROFILE = "nirai";
const PROFILE_DIR = join(homedir(), ".config", "tunnel-client");
const RESTART_MS = 60_000;

export function startTunnel(): void {
  const exe = join(process.env.LOCALAPPDATA ?? "", "Programs", "OpenAI", "tunnel-client", "tunnel-client.exe");
  if (!existsSync(exe)) {
    console.error("tunnel-client が見つからない。Holoは郵便局につながらない");
    return;
  }
  stopLeftovers();
  let child: ChildProcess | undefined;
  const run = () => {
    // 設定の置き場所ははっきり渡す（渡さないと、環境によって AppData\Roaming を探しに行く）
    child = spawn(exe, ["run", "--profile-dir", PROFILE_DIR, "--profile", PROFILE], { windowsHide: true, stdio: "ignore" });
    console.log(`${new Date().toISOString()} tunnel started pid=${child.pid}`);
    child.on("exit", code => {
      console.log(`${new Date().toISOString()} tunnel stopped code=${code}`);
      child = undefined;
      setTimeout(run, RESTART_MS).unref();
    });
  };
  run();
  process.on("exit", () => child?.kill());
}

/** この設定で動いている tunnel-client を止める（Master の local-files など、ほかの設定のものには触れない）。 */
function stopLeftovers(): void {
  const script = `Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" | ` +
    `Where-Object { $_.CommandLine -match '\\s--profile\\s+${PROFILE}(\\s|$)' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`;
  spawnSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], { windowsHide: true });
}
