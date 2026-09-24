import { realpath, readdir, lstat } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fingerprint } from "../shared/stable.js";
import { DEFAULT_SETTINGS } from "../shared/settings.js";
import { HubError } from "../shared/errors.js";
import type { CapabilityContext, CapabilityResult } from "./capability.js";
import type { LocalFilePolicy } from "./local-files.js";
import { LocalWorker, fileHash } from "./local-worker.js";

// Registered by trusted Hub code, never by an MCP payload or project script.
export interface CommandProfile {
  id: string;
  workspace: string;
  executable: string;
  argv: readonly string[];
  sources: readonly string[];
  output_dirs: readonly string[];
  description: string;
  verified_source_fingerprint?: string;
  verification_kind?: string;
}
interface CommandInput { profile: string; source_fingerprint: string; timeout_ms?: number; output_bytes?: number }
interface Snapshot { profile: CommandProfile; root: string; executable: string; executable_sha256: string; sources: Array<{ path: string; sha256: string }>; source_fingerprint: string; execution_fingerprint: string }

export class LocalCommands {
  private readonly profiles: Map<string, CommandProfile>;
  constructor(private readonly policy: LocalFilePolicy, private readonly worker: LocalWorker | undefined, profiles: readonly CommandProfile[]) {
    this.profiles = new Map(profiles.map(profile => [profile.id, Object.freeze({ ...profile, argv: [...profile.argv], sources: [...profile.sources], output_dirs: [...profile.output_dirs] })]));
  }
  validate(value: unknown): asserts value is CommandInput {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new HubError("invalid", "command requires a fixed profile input");
    const input = value as Record<string, unknown>;
    if (Object.keys(input).some(key => !["profile", "source_fingerprint", "timeout_ms", "output_bytes"].includes(key))
      || typeof input.profile !== "string" || !this.profiles.has(input.profile) || typeof input.source_fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(input.source_fingerprint)) {
      throw new HubError("blocked", "unknown or unfixed command profile; inspect a registered profile first");
    }
    for (const [key, max] of [["timeout_ms", DEFAULT_SETTINGS.command_max_timeout_ms], ["output_bytes", DEFAULT_SETTINGS.command_output_bytes]] as const) {
      if (input[key] !== undefined && (!Number.isSafeInteger(input[key]) || Number(input[key]) < 1 || Number(input[key]) > max)) throw new HubError("invalid", `invalid ${key}`);
    }
  }
  async snapshot(id: string, scope: string | null): Promise<Snapshot> {
    const profile = this.profiles.get(id);
    if (!profile) throw new HubError("blocked", "command profile is not registered");
    const root = await this.policy.resolvePath(scope, ".", false, true);
    if (root.toLowerCase() !== (await realpath(profile.workspace)).toLowerCase()) throw new HubError("unauthorized", "command profile belongs to another workspace");
    const executable = await realpath(profile.executable);
    const sources: Snapshot["sources"] = [];
    const seen = new Set<string>(); let bytes = 0;
    const visit = async (path: string): Promise<void> => {
      const canonical = await this.policy.resolvePath(root, path, false, true);
      if (seen.has(canonical.toLowerCase())) return;
      if (seen.size >= 7000) throw new HubError("blocked", "profile directory inspection limit exceeded");
      seen.add(canonical.toLowerCase());
      const stat = await lstat(canonical);
      if (stat.isDirectory()) {
        for (const name of (await readdir(canonical)).sort()) await visit(join(path, name));
      } else {
        if (!stat.isFile() || sources.length >= 3000 || (bytes += stat.size) > 96 * 1024 * 1024) throw new HubError("blocked", "profile source snapshot exceeds the bounded inspection limit");
        sources.push({ path: relative(root, canonical).replaceAll("\\", "/"), sha256: await fileHash(canonical) });
      }
    };
    for (const path of profile.sources) await visit(path);
    sources.sort((a, b) => a.path.localeCompare(b.path, "en"));
    const source_fingerprint = fingerprint(sources);
    const executable_sha256 = await fileHash(executable);
    const execution_fingerprint = fingerprint({ profile: profile.id, root, executable, executable_sha256, argv: profile.argv, sources, output_dirs: profile.output_dirs });
    return { profile, root, executable, executable_sha256, sources, source_fingerprint, execution_fingerprint };
  }
  async inspect(id: string, scope: string | null): Promise<Record<string, unknown>> {
    const snapshot = await this.snapshot(id, scope);
    return { profile: id, description: snapshot.profile.description, executable: snapshot.executable, argv: snapshot.profile.argv,
      cwd: snapshot.root, output_dirs: snapshot.profile.output_dirs, source_fingerprint: snapshot.execution_fingerprint,
      files: snapshot.sources, approval_required: snapshot.source_fingerprint !== snapshot.profile.verified_source_fingerprint,
      summary: "Fixed command proposal; use this fingerprint for run_command" };
  }
  private async checked(value: unknown, context: CapabilityContext): Promise<Snapshot> {
    this.validate(value);
    const snapshot = await this.snapshot(value.profile, context.workspace_scope);
    if (snapshot.execution_fingerprint !== value.source_fingerprint) throw new HubError("stale", "command source or executable changed; inspect the concrete proposal again");
    const settings = context.settings ?? DEFAULT_SETTINGS;
    if ((value.timeout_ms ?? settings.command_timeout_ms) > settings.command_max_timeout_ms || (value.output_bytes ?? settings.command_output_bytes) > settings.command_output_bytes) throw new HubError("blocked", "command exceeds Task limits");
    return snapshot;
  }
  async prepare(value: unknown, context: CapabilityContext): Promise<{ approval?: string }> {
    const s = await this.checked(value, context);
    if (s.source_fingerprint === s.profile.verified_source_fingerprint) return {};
    return { approval: `実行内容の確認が必要です: ${s.profile.description}\n実行ファイル: ${s.executable}\n引数: ${JSON.stringify(s.profile.argv)}\n対象: ${s.root}\n出力先: ${s.profile.output_dirs.join(", ")}\n対象ファイル: ${s.sources.length} 件\n版: ${s.execution_fingerprint}\n前回確認したソースと異なります。この具体的な版を一度実行する許可です。作業フォルダーはOSの隔離ではありません。` };
  }
  async invoke(value: unknown, context: CapabilityContext): Promise<CapabilityResult> {
    const s = await this.checked(value, context); this.validate(value);
    if (!this.worker) throw new HubError("unavailable", "local command worker unavailable");
    const settings = context.settings ?? DEFAULT_SETTINGS;
    const system = process.env.SystemRoot ?? "C:\\Windows";
    const env = { SystemRoot: system, PATH: [dirname(s.executable), join(system, "System32")].join(";"),
      TEMP: process.env.TEMP ?? join(system, "Temp"), TMP: process.env.TEMP ?? join(system, "Temp"), ELECTRON_RUN_AS_NODE: "1", NO_COLOR: "1" };
    const result = await this.worker.run(context.run_id, { kind: "command", root: s.root, executable: s.executable, executable_sha256: s.executable_sha256,
      argv: s.profile.argv, sources: s.sources, protected_roots: this.policy.protectedRoots, env,
      profile: s.profile.id, execution_fingerprint: s.execution_fingerprint, verification_kind: s.profile.verification_kind ?? null,
      timeout_ms: value.timeout_ms ?? settings.command_timeout_ms, output_bytes: value.output_bytes ?? settings.command_output_bytes });
    if (result.state === "Completed") {
      try {
        if ((await this.snapshot(s.profile.id, context.workspace_scope)).execution_fingerprint !== s.execution_fingerprint) throw new Error("changed");
      } catch { result.state = "Failed"; result.effects = "partial"; result.error = { message: "Source changed during execution; the result cannot verify the current version" }; }
    }
    result.result = { ...(result.result as Record<string, unknown> ?? {}), profile: s.profile.id, source_fingerprint: s.execution_fingerprint,
      source_manifest: s.sources, executable_sha256: s.executable_sha256 };
    const ref = `workspace:${s.root}`;
    result.artifacts = [...(result.artifacts ?? []), { ref, fingerprint: s.execution_fingerprint, ownership: "project" }];
    if (s.profile.verification_kind) result.verification = { kind: s.profile.verification_kind, artifact_ref: ref, fingerprint: s.execution_fingerprint, passed: result.state === "Completed" };
    return result;
  }
}

export function initialCommandProfiles(workspace: string, executable: string): CommandProfile[] {
  const sources = ["src", "tests", "package.json", "package-lock.json", "tsconfig.json", "node_modules/typescript", "node_modules/@types/node", "node_modules/undici-types"];
  const profiles: CommandProfile[] = [{ id: "nirai-v2.build", workspace, executable, argv: [resolve(workspace, "node_modules/typescript/lib/tsc.js"), "-p", "tsconfig.json", "--outDir", ".nirai-build"], sources,
    output_dirs: [".nirai-build"], description: "Nirai v2を候補フォルダーへTypeScriptビルド", verification_kind: "build" },
  { id: "nirai-v2.test", workspace, executable, argv: ["--test", ".nirai-build/tests/*.test.js"], sources: [...sources, ".nirai-build"],
    output_dirs: ["<TEMP>/nirai-v2-*"], description: "候補ビルドのHub・ローカル操作テスト", verification_kind: "test" }];
  try {
    const baselines = JSON.parse(readFileSync(join(workspace, "resources/local-profiles.json"), "utf8")) as Record<string, string>;
    for (const profile of profiles) if (/^[a-f0-9]{64}$/.test(baselines[profile.id] ?? "")) profile.verified_source_fingerprint = baselines[profile.id]!;
  } catch { /* Missing/unreadable reviewed baselines require a concrete approval. */ }
  return profiles;
}
