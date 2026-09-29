import { open, realpath, lstat, readdir } from "node:fs/promises";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { createHash } from "node:crypto";
import type { Capability, CapabilityContext, CapabilityOperationSpec } from "./capability.js";
import { HubError } from "../shared/errors.js";
import { LocalWorker, fileHash } from "./local-worker.js";
import { LocalCommands, type CommandProfile } from "./local-process.js";

const READ_LIMIT = 64 * 1024;
function within(root: string, target: string): boolean {
  const path = relative(root, target);
  return path === "" || (!isAbsolute(path) && path !== ".." && !path.startsWith(`..${sep}`));
}

function relativePath(path: unknown, allowRoot = false): asserts path is string {
  if (typeof path !== "string" || !path.trim() || isAbsolute(path) || path.includes(":") || path.includes("\0")
    || path.split(/[\\/]/).some(part => part !== "." && (part === ".." || /[ .]$/.test(part) || /^(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(part)))
    || (!allowRoot && path === ".")) throw new HubError("invalid", "a relative ordinary path is required");
}

function fields(value: unknown, allowed: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(key => !allowed.includes(key))) throw new HubError("invalid", "invalid local operation input");
  return value as Record<string, unknown>;
}

interface PatchInput { changes: Array<{ path: string; before_sha256: string | null; content: string }> }
function patchInput(value: unknown): asserts value is PatchInput {
  const input = fields(value, ["changes"]);
  if (!Array.isArray(input.changes) || !input.changes.length || input.changes.length > 16) throw new HubError("blocked", "patch supports 1 to 16 explicit file changes");
  let bytes = 0;
  const paths = new Set<string>();
  for (const item of input.changes) {
    const change = fields(item, ["path", "before_sha256", "content"]);
    relativePath(change.path);
    if (typeof change.content !== "string" || !(change.before_sha256 === null || typeof change.before_sha256 === "string" && /^[a-f0-9]{64}$/.test(change.before_sha256))) throw new HubError("invalid", "patch requires content and a before SHA256, or null for an absent path");
    const path = change.path.replaceAll("\\", "/").toLowerCase();
    if (paths.has(path)) throw new HubError("invalid", "duplicate patch path");
    paths.add(path); bytes += Buffer.byteLength(change.content);
  }
  if (bytes > 256 * 1024) throw new HubError("blocked", "patch exceeds the bounded local operation size");
}

function searchInput(value: unknown): asserts value is { path: string; query: string; max_results?: number } {
  const input = fields(value, ["path", "query", "max_results"]); relativePath(input.path, true);
  if (typeof input.query !== "string" || !input.query || input.query.length > 256) throw new HubError("invalid", "search requires a literal query of 1 to 256 characters");
  if (input.max_results !== undefined && (!Number.isSafeInteger(input.max_results) || Number(input.max_results) < 1 || Number(input.max_results) > 100)) throw new HubError("invalid", "search max_results must be 1 to 100");
}
function runInput(value: unknown): asserts value is { run_id: string } {
  const input = fields(value, ["run_id"]);
  if (typeof input.run_id !== "string" || !input.run_id) throw new HubError("invalid", "run_id is required");
}

function readInput(value: unknown): asserts value is { path: string; max_bytes?: number } {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new HubError("invalid", "local.read requires an object");
  const input = value as Record<string, unknown>;
  relativePath(input.path);
  if (Object.keys(input).some(key => !["path", "max_bytes"].includes(key))
    || typeof input.path !== "string" || !input.path.trim() || isAbsolute(input.path)
    || input.path.includes(":") || input.path.includes("\0")) throw new HubError("invalid", "local.read requires a relative file path");
  if (input.max_bytes !== undefined && (!Number.isSafeInteger(input.max_bytes) || Number(input.max_bytes) < 1 || Number(input.max_bytes) > READ_LIMIT)) {
    throw new HubError("invalid", "local.read max_bytes must be between 1 and 65536");
  }
}

// Shared path gate for local capabilities. Write policy is intentionally not implied
// by read permission; patch and Process operations must add their own inspection.
export class LocalFilePolicy {
  constructor(readonly protectedRoots: readonly string[], readonly writeProtectedRoots: readonly string[] = []) {}

  async resolveRead(scope: string | null, path: string): Promise<string> {
    return this.resolvePath(scope, path);
  }

  async resolvePath(scope: string | null, path: string, absent = false, directory = false, writing = false): Promise<string> {
    relativePath(path, directory);
    if (!scope || !isAbsolute(scope)) throw new HubError("blocked", "Task requires an explicit absolute workspace scope");
    const root = await realpath(scope);
    if (root.startsWith("\\\\")) throw new HubError("unauthorized", "network workspace paths are not supported");
    const target = resolve(root, path);
    if (!within(root, target) || (!directory && target === root)) throw new HubError("unauthorized", "file is outside the Task workspace");
    const parts = relative(root, target).split(sep).filter(Boolean);
    // Credentials and Hub/runtime state are not ordinary source input.
    if (target.split(/[\\/]/).some(part => /^(?:\.git|\.ssh|\.aws|\.azure|\.codex|\.env(?:\..*)?|credentials?(?:\..*)?|auth\.json|config\.toml|hub\.sqlite3(?:-.*)?|connection\.json)$/i.test(part))) {
      throw new HubError("unauthorized", "secret or internal state path is protected");
    }
    let current = root;
    for (const part of parts) {
      current = resolve(current, part);
      let stat;
      try { stat = await lstat(current); }
      catch (error) { if (absent && current === target && (error as NodeJS.ErrnoException).code === "ENOENT") break; throw error; }
      if (stat.isSymbolicLink() || stat.isFile() && stat.nlink !== 1) throw new HubError("unauthorized", "linked or shared file paths are not permitted");
      if (absent && current === target) throw new HubError("stale", "new file path is no longer absent");
    }
    const canonical = absent ? target : await realpath(target);
    if (!within(root, canonical)) throw new HubError("unauthorized", "resolved file is outside the Task workspace");
    for (const protectedRoot of [...this.protectedRoots, ...(writing ? this.writeProtectedRoots : [])]) {
      if (within(protectedRoot, canonical)) throw new HubError("unauthorized", "Hub or running application files are protected");
    }
    return canonical;
  }
}

export function localFiles(policy: LocalFilePolicy, dataRoot?: string, profiles: readonly CommandProfile[] = []): Capability {
  const stopped = new Set<string>();
  const worker = dataRoot ? new LocalWorker(dataRoot) : undefined;
  const commands = new LocalCommands(policy, worker, profiles);
  const resources = ["local:workspace"];
  const path = { type: "string", description: "Task workspace relative path" };
  const runId = { type: "object", additionalProperties: false, required: ["run_id"], properties: { run_id: { type: "string" } } };
  const operations = new Map<string, CapabilityOperationSpec>([
    ["read", { side_effects: "none", resources, validateInput: readInput, input_schema: {
      type: "object", additionalProperties: false, required: ["path"],
      properties: { path, max_bytes: { type: "integer", minimum: 1, maximum: READ_LIMIT } },
    } }],
    ["search", { side_effects: "none", resources, validateInput: searchInput, input_schema: {
      type: "object", additionalProperties: false, required: ["path", "query"],
      properties: { path: { ...path, description: "relative directory or file; \".\" for the workspace" }, query: { type: "string", description: "literal text" }, max_results: { type: "integer", minimum: 1, maximum: 100 } },
    } }],
    ["apply_patch", { side_effects: "possible", resources, validateInput: patchInput, input_schema: {
      type: "object", additionalProperties: false, required: ["changes"],
      properties: { changes: { type: "array", minItems: 1, maxItems: 16, items: {
        type: "object", additionalProperties: false, required: ["path", "before_sha256", "content"],
        properties: { path, before_sha256: { type: ["string", "null"], description: "content_sha256 from a whole-file local.read, or null to create an absent file" }, content: { type: "string", description: "complete new file content" } },
      } } },
    } }],
    ["run_command", { side_effects: "possible", resources, validateInput: input => commands.validate(input), input_schema: {
      type: "object", additionalProperties: false, required: ["profile", "source_fingerprint"],
      properties: { profile: { type: "string", enum: commands.profileIds() }, source_fingerprint: { type: "string", description: "source_fingerprint returned by local.inspect for this profile" },
        timeout_ms: { type: "integer", minimum: 1 }, output_bytes: { type: "integer", minimum: 1 } },
    } }],
    ["inspect", { side_effects: "none", input_schema: { oneOf: [
      { type: "object", additionalProperties: false, required: ["profile"], properties: { profile: { type: "string", enum: commands.profileIds() } } },
      runId,
    ] }, validateInput: value => {
      if (value && typeof value === "object" && "profile" in value) { const input = fields(value, ["profile"]); if (typeof input.profile !== "string") throw new HubError("invalid", "profile is required"); }
      else runInput(value);
    } }],
    ["cancel", { side_effects: "none", validateInput: runInput, input_schema: runId }],
  ]);
  return {
    id: "local",
    operations,
    availability: () => ({ state: "ready" }),
    async prepare(operation, value, context) {
      if (operation === "run_command") return commands.prepare(value, context);
      if (operation === "apply_patch") {
        patchInput(value);
        for (const change of value.changes) {
          const path = await policy.resolvePath(context.workspace_scope, change.path, change.before_sha256 === null, false, true);
          if (change.before_sha256 !== null) {
            if ((await lstat(path)).size > 1024 * 1024) throw new HubError("blocked", "patch original exceeds 1 MiB");
            if (await fileHash(path) !== change.before_sha256) throw new HubError("stale", "patch before fingerprint changed");
          }
        }
      }
      return {};
    },
    async cancel(runId) { stopped.add(runId); worker?.stop(runId); },
    async invoke(operation, value, context: CapabilityContext) {
      if (operation === "inspect" || operation === "cancel") {
        if (operation === "inspect" && value && typeof value === "object" && "profile" in value) {
          const input = fields(value, ["profile"]);
          const result = await commands.inspect(String(input.profile), context.workspace_scope);
          return { state: "Completed", effects: "none", cleanup_state: "clear", result };
        }
        runInput(value);
        const run = operation === "inspect" ? context.inspectRun?.(value.run_id) : context.stopRun?.(value.run_id);
        if (!run) throw new HubError("unavailable", "Hub Run access unavailable");
        return { state: "Completed", effects: "none", cleanup_state: "clear", result: { run, summary: operation === "cancel" ? "Stop requested; inspect the target Run for actual termination" : `Run ${run.state}` } };
      }
      if (operation === "run_command") return commands.invoke(value, context);
      if (operation === "apply_patch") {
        if (!worker) throw new HubError("unavailable", "local write worker unavailable");
        patchInput(value);
        for (const change of value.changes) await policy.resolvePath(context.workspace_scope, change.path, change.before_sha256 === null, false, true);
        const result = await worker.run(context.run_id, { kind: "patch", root: await realpath(context.workspace_scope!), changes: value.changes, protected_roots: [...policy.protectedRoots, ...policy.writeProtectedRoots] });
        if (result.effects !== "none") result.artifacts = [...(result.artifacts ?? []), { ref: `workspace:${await realpath(context.workspace_scope!)}`, fingerprint: `patch:${context.run_id}`, ownership: "project" }];
        if (result.state === "Completed") result.artifacts = [...(result.artifacts ?? []), ...value.changes.map(change => ({
          ref: resolve(context.workspace_scope!, change.path), fingerprint: createHash("sha256").update(change.content).digest("hex"), ownership: "project" as const,
        }))];
        stopped.delete(context.run_id);
        return result;
      }
      if (operation === "search") {
        searchInput(value);
        const matches: Array<{ path: string; line: number; text: string }> = [];
        const queue = [value.path]; let scanned = 0, visited = 0, skipped = 0, size = 0;
        try {
          while (queue.length && visited++ < 1000 && matches.length < (value.max_results ?? 50) && size < READ_LIMIT && !stopped.has(context.run_id)) {
            const path = queue.shift()!;
            try {
              const canonical = await policy.resolvePath(context.workspace_scope, path, false, true);
              const stat = await lstat(canonical);
              if (stat.isDirectory()) {
                const entries = await readdir(canonical);
                for (const entry of entries.sort()) { if (queue.length >= 1000) { skipped++; break; } queue.push(path === "." ? entry : `${path}/${entry}`); }
                continue;
              }
              scanned++;
              if (stat.size > READ_LIMIT) { skipped++; continue; }
              const content = await boundedRead(policy, context, path, READ_LIMIT);
              const lines = content.content.split(/\r?\n/);
              for (let i = 0; i < lines.length && matches.length < (value.max_results ?? 50); i++) if (lines[i]!.includes(value.query)) {
                const text = lines[i]!.slice(0, 500); size += Buffer.byteLength(text);
                matches.push({ path, line: i + 1, text });
              }
            } catch { skipped++; }
          }
          return { state: stopped.has(context.run_id) ? "Cancelled" : "Completed", effects: "none", cleanup_state: "clear", result: { matches, scanned, skipped, truncated: queue.length > 0 || skipped > 0, summary: `${matches.length} matches` } };
        } finally { stopped.delete(context.run_id); }
      }
      if (operation !== "read") throw new HubError("invalid", "unsupported local operation");
      readInput(value);
      try {
        const result = await boundedRead(policy, context, value.path, value.max_bytes ?? READ_LIMIT);
        return { state: stopped.has(context.run_id) ? "Cancelled" : "Completed", effects: "none", cleanup_state: "clear", result };
      } finally { stopped.delete(context.run_id); }
    },
  };
}

async function boundedRead(policy: LocalFilePolicy, context: CapabilityContext, path: string, limit: number) {
      const file = await policy.resolveRead(context.workspace_scope, path);
      const handle = await open(file, "r");
      try {
        const before = await handle.stat();
        if (!before.isFile() || before.nlink !== 1) throw new HubError("unauthorized", "only ordinary unshared files can be read");
        // Recheck the opened identity before reading; a replaced path is rejected.
        const checked = await policy.resolveRead(context.workspace_scope, path);
        const pathStat = await lstat(checked);
        if (before.dev !== pathStat.dev || before.ino !== pathStat.ino) throw new HubError("stale", "file changed while opening");
        const bytes = Buffer.alloc(limit);
        const { bytesRead } = await handle.read(bytes, 0, limit, 0);
        const after = await handle.stat();
        if (before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs) throw new HubError("stale", "file changed while reading");
        const content = bytes.subarray(0, bytesRead);
        return {
          path, content: content.toString("utf8"), bytes_read: bytesRead,
          truncated: before.size > bytesRead,
          content_sha256: createHash("sha256").update(content).digest("hex"),
          fingerprint_scope: before.size > bytesRead ? "returned_bytes" : "whole_file",
          summary: `${path}: ${bytesRead} bytes read`,
        };
      } finally {
        await handle.close();
      }
}
