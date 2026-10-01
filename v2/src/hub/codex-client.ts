import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { access, readdir, stat } from "node:fs/promises";
import { constants } from "node:fs";
import { delimiter, isAbsolute, join } from "node:path";
import { HubError } from "../shared/errors.js";

export interface CodexClient {
  readonly serverVersion: string;
  request(method: string, params: unknown, signal?: AbortSignal): Promise<unknown>;
  notify(method: string, params?: unknown): void;
  respond(id: string | number, result: unknown): void;
  rejectRequest(id: string | number): void;
  onNotification: (method: string, params: unknown) => void;
  onServerRequest: (id: string | number, method: string, params: unknown) => void;
  onFailure: (error: Error) => void;
  close(): Promise<void>;
}

export interface CodexClientOptions {
  executable?: string;
  requestTimeoutMs?: number;
  signal?: AbortSignal;
  disabledMcpServers?: readonly string[];
  dynamicToolsHost?: boolean;
  spawnProcess?: (executable: string, args: readonly string[], cwd: string, env: NodeJS.ProcessEnv) => ChildProcessWithoutNullStreams;
}

// These switches belong to this child process. The user's Codex configuration is never edited.
export const CODEX_DISABLED_FEATURES = [
  "shell_tool", "unified_exec", "apps", "plugins", "hooks", "multi_agent", "multi_agent_v2",
  "browser_use", "browser_use_external", "computer_use", "code_mode", "code_mode_only", "code_mode_prewarm",
  "image_generation", "view_image", "memories", "goals", "sleep_tool", "skill_search",
  "skill_mcp_dependency_install", "tool_suggest", "remote_plugin", "workspace_dependencies",
  "request_permissions_tool", "default_mode_request_user_input",
] as const;

const MAX_WIRE_BYTES = 1024 * 1024;
const unavailable = () => new HubError("unavailable", "Codex CLIとの接続が終了しました。自動で再送はしません。");

export function codexProcessArgs(disabledMcpServers: readonly string[] = [], dynamicToolsHost = false): string[] {
  const args = ["app-server", "--listen", "stdio://", "-c", 'model_provider="openai"',
    "-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0",
    "-c", "skills.include_instructions=false", "-c", "agents.enabled=false",
    "-c", "memories.generate_memories=false"];
  for (const feature of CODEX_DISABLED_FEATURES) args.push("--disable", feature);
  // The stable host also transports direct dynamic tools; enabling it does not expose Code Mode exec.
  // It is needed only by the Task process. Keep the model-facing Code Mode features disabled above.
  if (dynamicToolsHost) args.push("-c", "features.code_mode_host={enabled=true,disable_in_process_fallback=true}");
  else args.push("--disable", "code_mode_host");
  for (const id of disabledMcpServers) {
    // CLI dotted overrides split on '.', rather than parsing quoted TOML path segments.
    if (!/^[A-Za-z0-9_-]+$/.test(id)) throw new HubError("unavailable", "Codex CLIの外部Tool設定を安全に無効化できません。生成を開始しません。");
    args.push("-c", `mcp_servers.${id}.enabled=false`);
  }
  return args;
}

export async function resolveCodexExecutable(explicit?: string): Promise<string> {
  const selected = explicit ?? process.env.NIRAI_V2_CODEX_COMMAND;
  if (selected) {
    if (!isAbsolute(selected) || !/\.exe$/i.test(selected) || /^[\\/]{2}/.test(selected)) {
      throw new HubError("invalid", "Codex CLIにはローカルの実行ファイルを指定してください。");
    }
    await access(selected, constants.F_OK);
    return selected;
  }
  const filename = process.platform === "win32" ? "codex.exe" : "codex";
  for (const directory of (process.env.PATH ?? "").split(delimiter).filter(Boolean)) {
    const candidate = join(directory.replace(/^"|"$/g, ""), filename);
    try { if ((await stat(candidate)).isFile()) return candidate; } catch { /* Try the next installed location. */ }
  }
  if (process.platform === "win32" && process.env.LOCALAPPDATA) {
    const bin = join(process.env.LOCALAPPDATA, "OpenAI", "Codex", "bin");
    try {
      const candidates = await Promise.all((await readdir(bin, { withFileTypes: true }))
        .filter(entry => entry.isDirectory()).map(async entry => {
          const path = join(bin, entry.name, "codex.exe");
          try { const info = await stat(path); return info.isFile() ? { path, modified: info.mtimeMs } : null; }
          catch { return null; }
        }));
      const latest = candidates.filter((item): item is NonNullable<typeof item> => item !== null)
        .sort((a, b) => b.modified - a.modified)[0];
      if (latest) return latest.path;
    } catch { /* No desktop CLI installation. */ }
  }
  throw new HubError("unavailable", "Codex CLIが見つかりません。Codex CLIをインストールし、ChatGPTでログインしてください。");
}

/** A bounded JSON-lines transport. Failed or uncertain requests are never replayed. */
export class CodexAppServerClient implements CodexClient {
  serverVersion = "";
  onNotification: CodexClient["onNotification"] = () => {};
  onServerRequest: CodexClient["onServerRequest"] = id => this.rejectRequest(id);
  onFailure: CodexClient["onFailure"] = () => {};
  private readonly pending = new Map<number, { resolve: (value: unknown) => void; reject: (error: Error) => void; clean: () => void }>();
  private sequence = 0;
  private buffer = Buffer.alloc(0);
  private failed: Error | null = null;
  private closing: Promise<void> | null = null;
  private readonly exited: Promise<void>;

  private constructor(private readonly child: ChildProcessWithoutNullStreams, private readonly timeout: number) {
    this.exited = new Promise(resolve => child.once("close", () => resolve()));
    child.stdout.on("data", (chunk: Buffer) => this.read(chunk));
    child.stdout.once("end", () => this.fail(unavailable()));
    child.once("error", () => this.fail(unavailable()));
    child.once("close", () => this.fail(unavailable()));
    child.stdin.on("error", () => this.fail(unavailable()));
    // Drain diagnostics without exposing credentials, prompts, or Provider error payloads to logs/UI.
    child.stderr.resume();
  }

  static async open(cwd: string, options: CodexClientOptions = {}): Promise<CodexAppServerClient> {
    options.signal?.throwIfAborted();
    const executable = await resolveCodexExecutable(options.executable);
    options.signal?.throwIfAborted();
    const env = { ...process.env };
    for (const key of Object.keys(env)) {
      if (/^(OPENAI_API_KEY|CODEX_ACCESS_TOKEN|CODEX_AUTH_JSON|ELECTRON_RUN_AS_NODE)$/i.test(key)) delete env[key];
    }
    const args = codexProcessArgs(options.disabledMcpServers, options.dynamicToolsHost);
    const child = options.spawnProcess ? options.spawnProcess(executable, args, cwd, env)
      : spawn(executable, args, { cwd, env, shell: false, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
    const client = new CodexAppServerClient(child, options.requestTimeoutMs ?? 15_000);
    try {
      const initialized = await client.request("initialize", { clientInfo: { name: "nirai_v2", title: "Nirai v2", version: "0.1.0" },
        capabilities: { experimentalApi: true, requestAttestation: false } }, options.signal);
      const agent = (initialized as { userAgent?: unknown } | null)?.userAgent;
      const version = typeof agent === "string" ? agent.match(/\/(\d+)\.(\d+)\.(\d+)(?:[\s(-]|$)/) : null;
      if (!version || !(Number(version[1]) > 0 || Number(version[2]) > 159 || (Number(version[2]) === 159 && Number(version[3]) >= 2))) {
        throw new HubError("unavailable", "Codex CLI 0.159.2以降が必要です。CLIを更新してから接続を確認してください。");
      }
      client.serverVersion = `${version[1]}.${version[2]}.${version[3]}`;
      client.notify("initialized");
      return client;
    } catch (error) {
      await client.close();
      throw error;
    }
  }

  request(method: string, params: unknown, signal?: AbortSignal): Promise<unknown> {
    if (this.failed || this.closing) return Promise.reject(this.failed ?? unavailable());
    if (signal?.aborted) return Promise.reject(unavailable());
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      const abort = () => { this.remove(id); reject(unavailable()); };
      const timer = setTimeout(() => {
        this.remove(id);
        const error = new HubError("unavailable", "Codex CLIの応答が時間内に届きませんでした。自動で再送はしません。");
        reject(error);
        this.fail(error);
      }, this.timeout);
      const clean = () => { clearTimeout(timer); signal?.removeEventListener("abort", abort); };
      this.pending.set(id, { resolve, reject, clean });
      signal?.addEventListener("abort", abort, { once: true });
      this.write({ id, method, params });
    });
  }

  notify(method: string, params?: unknown): void { this.write({ method, ...(params === undefined ? {} : { params }) }); }
  respond(id: string | number, result: unknown): void { this.write({ id, result }); }
  rejectRequest(id: string | number): void { this.write({ id, error: { code: -32601, message: "Nirai has not authorized this operation" } }); }

  private write(message: unknown): void {
    if (this.failed || this.closing) { this.fail(this.failed ?? unavailable()); return; }
    const bytes = Buffer.from(`${JSON.stringify(message)}\n`);
    if (bytes.length > MAX_WIRE_BYTES) { this.fail(new HubError("invalid", "Codex CLIへ渡す会話のサイズが上限を超えました。")); return; }
    try { this.child.stdin.write(bytes); } catch { this.fail(unavailable()); }
  }

  private remove(id: number) {
    const item = this.pending.get(id);
    this.pending.delete(id);
    item?.clean();
    return item;
  }

  private read(chunk: Buffer): void {
    if (this.failed) return;
    this.buffer = Buffer.concat([this.buffer, chunk]);
    let end: number;
    while ((end = this.buffer.indexOf(10)) !== -1) {
      if (end > MAX_WIRE_BYTES) { this.fail(unavailable()); return; }
      const line = this.buffer.subarray(0, end);
      this.buffer = this.buffer.subarray(end + 1);
      if (!line.length) continue;
      try {
        const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(line)) as Record<string, unknown>;
        if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid message");
        if (typeof value.method === "string") {
          if (typeof value.id === "string" || typeof value.id === "number") this.onServerRequest(value.id, value.method, value.params);
          else this.onNotification(value.method, value.params);
        } else if (typeof value.id === "number") {
          const pending = this.remove(value.id);
          if (pending) {
            if (value.error) pending.reject(new HubError("unavailable", "Codex CLIが要求を受け付けませんでした。自動で再送はしません。"));
            else if ("result" in value) pending.resolve(value.result);
            else pending.reject(unavailable());
          }
        }
      } catch { this.fail(unavailable()); return; }
    }
    if (this.buffer.length > MAX_WIRE_BYTES) this.fail(unavailable());
  }

  private fail(error: Error): void {
    if (this.failed) return;
    this.failed = error;
    for (const id of [...this.pending.keys()]) this.remove(id)?.reject(error);
    this.onFailure(error);
  }

  close(): Promise<void> {
    if (!this.closing) this.closing = this.closeOnce();
    return this.closing;
  }

  private async closeOnce(): Promise<void> {
    this.fail(unavailable());
    this.child.stdin.end();
    let timer: ReturnType<typeof setTimeout> | undefined;
    await Promise.race([this.exited, new Promise<void>(resolve => { timer = setTimeout(resolve, 1_000); })]);
    clearTimeout(timer);
    if (this.child.exitCode === null && this.child.signalCode === null) this.child.kill("SIGKILL");
    await this.exited;
  }
}
