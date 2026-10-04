// CLIの脳を持つ住人（CodexとClaude）を、郵便局が裏で起こす。1人につき、同時に動くのは1つだけ。
// プロセスが終われば止まったと分かる。脳の出力（--json）は、その住人の生ログに残す。

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

/** roots の下の決まった深さのフォルダーにある実行ファイルのうち、いちばん新しいもの。
 *  CLIはデスクトップアプリに同梱されていて、更新のたびにフォルダー名が変わる。郵便局が動いている間にも更新されるので、
 *  起こすたびに探す（2026-10-04、更新で古い場所が消え、起こすたびに失敗した）。 */
function newest(roots: string[], depth: number, exe: string): string | undefined {
  let folders = roots;
  for (let i = 0; i < depth; i++) {
    folders = folders.flatMap(f => (existsSync(f) ? readdirSync(f, { withFileTypes: true }).filter(d => d.isDirectory()).map(d => join(f, d.name)) : []));
  }
  return folders.map(f => join(f, exe)).filter(existsSync).sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)[0];
}

/** Codexの実行ファイル（%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe） */
export function findCodex(): string {
  return newest([join(process.env.LOCALAPPDATA ?? "", "OpenAI", "Codex", "bin")], 1, "codex.exe") ?? "codex";
}

/** Claude Codeの実行ファイル。デスクトップアプリはパッケージのアプリで、AppData\Roaming への書き込みは
 *  パッケージの中（%LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming）へ移されている。%APPDATA%\Claude\claude-code に
 *  見えるのはアプリから起こしたプロセスだけで、タスクスケジューラから動く郵便局には見えない（2026-10-04）。だから本当の場所を探す。 */
export function findClaude(): string {
  const packages = join(process.env.LOCALAPPDATA ?? "", "Packages");
  const roots = existsSync(packages)
    ? readdirSync(packages).filter(name => name.startsWith("Claude_")).map(name => join(packages, name, "LocalCache", "Roaming", "Claude", "claude-code"))
    : [];
  return newest(roots, 2, "claude.exe") ?? "claude";
}

/** 起こしたCodexには要らない機能。画面やブラウザの操作、画像づくり、手下のエージェント、プラグインや技能の一覧、
 *  自分で次に起きる道具（起こすのは郵便局）、Codex自身の記憶（住人の記憶はイデアにある）。
 *  Codexには道具を名指しで選ぶ指定がないので、切るものを並べる。最初に読む量が約1.46万から1.28万トークンになった
 *  （2026-10-04。測ったときは、ここにない workspace_dependencies・worktrees も切っていた）。 */
const CODEX_OFF = [
  "browser_use", "browser_use_external", "in_app_browser", "computer_use", "image_generation", "view_image",
  "multi_agent", "apps", "plugins", "remote_plugin", "skill_search", "tool_suggest", "sleep_tool", "goals", "memories",
];

/** 囲い（サンドボックス）は使わず、Master がふだん使う Codex と同じ設定で動かす（2026-10-04、Master。計画 §2）。 */
export function codexCommand(options: { model: string; effort: string; port: number; workRoot: string }, codex = findCodex) {
  return (text: string): Command => ({
    file: codex(),
    cwd: options.workRoot,
    args: [
      "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
      "--dangerously-bypass-approvals-and-sandbox",
      "-m", options.model, "-c", `model_reasoning_effort="${options.effort}"`,
      "-C", options.workRoot,
      "-c", `mcp_servers.nirai.url="http://127.0.0.1:${options.port}/mcp/codex"`,
      ...CODEX_OFF.flatMap(feature => ["--disable", feature]),
      text,
    ],
  });
}

/** 起こしたClaudeに持たせる道具。起きるたびに道具の説明書を全部読むので、手紙の仕事に要るものだけにする。
 *  全部持たせると、最初に読む量が約3.7万トークンで、その7割が道具の説明書だった（いちばん大きいのはWebページを
 *  公開する道具）。これと技能の一覧を外して約1.5万になった（2026-10-04）。郵便の道具は、探さずにそのまま使える。 */
const CLAUDE_TOOLS = "Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch";

/** Claudeは、Masterとのセッションと同じ家（Niraiのリポジトリ）で起こす。CLAUDE.md・記憶・生ログの写しが同じになる。
 *  見張りは、Masterとのセッションと同じ自動モード。つなぐのは郵便局だけ。
 *  --tools・--mcp-config・--add-dir は値をいくつも取るので、起こす一言はその前に置く。 */
export function claudeCommand(options: { port: number; home: string; workRoot: string }, claude = findClaude) {
  const nirai = { mcpServers: { nirai: { type: "http", url: `http://127.0.0.1:${options.port}/mcp/claude` } } };
  return (text: string): Command => ({
    file: claude(),
    cwd: options.home,
    args: [
      "-p", text, "--output-format", "json", "--permission-mode", "auto",
      "--tools", CLAUDE_TOOLS, "--disable-slash-commands",
      "--strict-mcp-config", "--mcp-config", JSON.stringify(nirai),
      "--add-dir", options.workRoot,
    ],
  });
}
