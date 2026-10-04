// CLIの脳を持つ住人（今はCodex）を、郵便局が裏で起こす。1人につき、同時に動くのは1つだけ。
// プロセスが終われば止まったと分かる。脳の出力（--json の出来事）は、その住人の生ログに残す。
// 囲い（サンドボックス）は使わず、Master がふだん使う Codex と同じ設定で動かす（2026-10-04、Master。計画 §2）。

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { append, JST_DAY } from "./letters.ts";

export type Command = { file: string; args: string[]; cwd: string };

export class CliResident {
  readonly name: string;
  private residentsRoot: string;
  private command: (text: string) => Command;
  private limitMs: number;
  private onStop: () => void;
  private running: ChildProcess | undefined;

  constructor(name: string, residentsRoot: string, command: (text: string) => Command, limitMs: number, onStop: () => void) {
    this.name = name;
    this.residentsRoot = residentsRoot;
    this.command = command;
    this.limitMs = limitMs;
    this.onStop = onStop;
  }

  awake(): boolean {
    return this.running !== undefined;
  }

  /** 起こしたと生ログに書いてから起こす。止まったら、止まったと書いて知らせる。 */
  wake(letters: string[], text: string, now: Date): void {
    const { file, args, cwd } = this.command(text);
    append(this.residentsRoot, this.name, { kind: "wake", ts: now.toISOString(), letters, how: `${this.name.toLowerCase()} cli` });
    const logDir = join(this.residentsRoot, this.name, "lifelog", `${this.name.toLowerCase()}-cli`);
    mkdirSync(logDir, { recursive: true });
    const log = createWriteStream(join(logDir, `${JST_DAY.format(now)}.jsonl`), { flags: "a" });
    const child = spawn(file, args, { cwd, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    this.running = child;
    child.stdout.pipe(log, { end: false });
    let stderr = "";
    child.stderr.on("data", chunk => (stderr = (stderr + chunk).slice(-2000)));
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      killTree(child.pid);
    }, this.limitMs);
    const finish = (code: number | null, error?: Error) => {
      if (this.running !== child) return;
      clearTimeout(timer);
      this.running = undefined;
      log.end();
      const how = timedOut ? "timeout" : code === 0 ? "exit" : "error";
      const detail = error?.message ?? (code === 0 ? undefined : `code ${code} ${stderr.trim().split("\n").at(-1) ?? ""}`.trim());
      append(this.residentsRoot, this.name, { kind: "stop", ts: new Date().toISOString(), how, ...(detail ? { detail } : {}) });
      this.onStop();
    };
    child.on("exit", code => finish(code));
    child.on("error", error => finish(null, error));
  }
}

/** プロセスを、それが起こした子プロセスごと止める。 */
export function killTree(pid: number | undefined): void {
  if (pid) spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
}

/** Codexの実行ファイル。デスクトップアプリに同梱された新しいもの（更新のたびにフォルダー名が変わる）を探す。 */
export function findCodex(): string {
  const bin = join(process.env.LOCALAPPDATA ?? "", "OpenAI", "Codex", "bin");
  const found = existsSync(bin)
    ? readdirSync(bin).map(dir => join(bin, dir, "codex.exe")).filter(existsSync).sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)
    : [];
  return found[0] ?? "codex";
}

export function codexCommand(options: { codex: string; model: string; effort: string; port: number; workRoot: string }) {
  return (text: string): Command => ({
    file: options.codex,
    cwd: options.workRoot,
    args: [
      "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
      "--dangerously-bypass-approvals-and-sandbox",
      "-m", options.model, "-c", `model_reasoning_effort="${options.effort}"`,
      "-C", options.workRoot,
      "-c", `mcp_servers.nirai.url="http://127.0.0.1:${options.port}/mcp/codex"`,
      text,
    ],
  });
}
