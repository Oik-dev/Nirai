// CLIの脳を持つ住人（CodexとClaude）を、郵便局が裏で起こす。1人につき、同時に動くのは1つだけ。
// プロセスが終われば止まったと分かる。脳の出力（--json）は、その住人の生ログに残す。

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { append, JST_DAY, type Stop } from "./letters.ts";
import { instructions } from "./mcp.ts";

export type Command = { file: string; args: string[]; cwd: string };
export type UsageLimit = { until: Date; known: boolean };

export class CliResident {
  readonly name: string;
  private residentsRoot: string;
  private command: (text: string) => Command;
  private limitMs: number;
  private unknownLimitMs: number;
  private onStop: (stop: Stop) => void;
  private running: ChildProcess | undefined;

  constructor(
    name: string,
    residentsRoot: string,
    command: (text: string) => Command,
    limitMs: number,
    onStop: (stop: Stop) => void,
    unknownLimitMs = 60 * 60_000,
  ) {
    this.name = name;
    this.residentsRoot = residentsRoot;
    this.command = command;
    this.limitMs = limitMs;
    this.unknownLimitMs = unknownLimitMs;
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
    let stdout = "";
    child.stdout.on("data", chunk => {
      log.write(chunk);
      stdout = (stdout + chunk).slice(-64_000);
    });
    let stderr = "";
    child.stderr.on("data", chunk => (stderr = (stderr + chunk).slice(-16_000)));
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
      const stoppedAt = new Date();
      const output = `${stdout}\n${stderr}`;
      const failedOutput = code !== 0 || /"is_error"\s*:\s*true|"type"\s*:\s*"(?:error|turn\.failed)"/i.test(output);
      const limit = !timedOut && failedOutput ? parseUsageLimit(output, stoppedAt, this.unknownLimitMs) : undefined;
      const how = limit ? "limit" : timedOut ? "timeout" : code === 0 ? "exit" : "error";
      const detail = error?.message ?? (code === 0 ? undefined : `code ${code} ${stderr.trim().split("\n").at(-1) ?? ""}`.trim());
      const stop: Stop = {
        kind: "stop",
        ts: stoppedAt.toISOString(),
        how,
        ...(detail ? { detail } : {}),
        ...(limit ? { until: limit.until.toISOString(), untilKnown: limit.known } : {}),
      };
      append(this.residentsRoot, this.name, stop);
      this.onStop(stop);
    };
    child.on("close", code => finish(code));
    child.on("error", error => finish(null, error));
  }
}

/** Codex/Claudeの上限の知らせを読み、次に起きられる時刻へそろえる。 */
export function parseUsageLimit(text: string, now: Date, unknownMs = 60 * 60_000): UsageLimit | undefined {
  if (!/(?:you(?:['’]ve| have) (?:hit|reached) your.{0,50}limit|usage limit|out of usage credits|limit resets)/is.test(text)) return undefined;

  const epoch = /["']?(?:resetsAt|resets_at)["']?\s*[:=]\s*(\d{10,13})/i.exec(text)?.[1];
  if (epoch) {
    const n = Number(epoch);
    const until = new Date(epoch.length <= 10 ? n * 1000 : n);
    if (!Number.isNaN(until.getTime()) && until > now) return { until, known: true };
  }

  const phrase = /(?:try again|limit resets?|resets?)\s+(?:at\s+)?([^\r\n"}]{1,100})/i.exec(text)?.[1] ?? "";
  const cleaned = phrase.replace(/\([^)]*\)/g, " ").replace(/[.;,]+\s*$/, "").trim();
  const clock = /(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\b/i.exec(cleaned);
  if (clock) {
    const hour12 = Number(clock[1]);
    const minute = Number(clock[2] ?? 0);
    const hour = (hour12 % 12) + (/PM/i.test(clock[3]) ? 12 : 0);
    const datePart = cleaned
      .replace(clock[0], " ")
      .replace(/^\s*(?:on|at)\s+/i, "")
      .replace(/\s+(?:on|at)\s*$/i, "")
      .trim();
    let until: Date;
    if (datePart && /(?:\d{4}-\d{1,2}-\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9})/.test(datePart)) {
      const year = /\b\d{4}\b/.test(datePart) ? "" : ` ${now.getFullYear()}`;
      const parsed = new Date(`${datePart}${year} ${clock[1]}:${String(minute).padStart(2, "0")} ${clock[3]}`);
      until = parsed;
      if (!/\b\d{4}\b/.test(datePart) && until <= now) until.setFullYear(until.getFullYear() + 1);
    } else {
      until = new Date(now);
      until.setHours(hour, minute, 0, 0);
      if (until <= now) until.setDate(until.getDate() + 1);
    }
    if (!Number.isNaN(until.getTime())) return { until, known: true };
  }

  return { until: new Date(now.getTime() + unknownMs), known: false };
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
 *  Claude CodeはMCPのサーバー説明を2,048字で切るので、決まりと人格の全文は、起こすたびにファイルへ書いてsystemに足す
 *  （2026-10-09。切れていたころは、起こしたClaudeの半分以上が決まりを自分で読み直していた）。
 *  ファイルにするのは、Windowsのコマンド行の長さ（32,767字）に縛られないため。置き場（scratch）は郵便局ごとの使い捨て。
 *  --tools・--mcp-config・--add-dir は値をいくつも取るので、起こす一言はその前に置く。 */
export function claudeCommand(
  options: {
    model: string; effort: string; autoCompact: string; port: number; home: string; workRoot: string;
    residentsRoot: string; scratch: string;
  },
  claude = findClaude,
) {
  const nirai = { mcpServers: { nirai: { type: "http", url: `http://127.0.0.1:${options.port}/mcp/claude` } } };
  return (text: string): Command => {
    mkdirSync(options.scratch, { recursive: true });
    const system = join(options.scratch, "claude-instructions.md");
    writeFileSync(system, instructions("Claude", options.residentsRoot));
    return {
      file: claude(),
      cwd: options.home,
      args: [
        "-p", text, "--output-format", "json", "--permission-mode", "auto",
        "--model", options.model, "--effort", options.effort, "--autocompact", options.autoCompact,
        "--append-system-prompt-file", system,
        // 機械ごとに変わる部分（作業フォルダー・git status など）を最初の頼みへ移し、決まりと人格までの頭を起きるたびにキャッシュから読む
        "--exclude-dynamic-system-prompt-sections",
        "--tools", CLAUDE_TOOLS, "--disable-slash-commands",
        "--strict-mcp-config", "--mcp-config", JSON.stringify(nirai),
        "--add-dir", options.workRoot,
      ],
    };
  };
}
