import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { fingerprint } from "../shared/stable.js";
import type { CapabilityResult } from "./capability.js";
import { fileHash } from "./local-worker.js";
import type { LocalFilePolicy } from "./local-files.js";
import type { HubStore } from "./store.js";

const exec = promisify(execFile);
export async function probeLocalWorker(request: string): Promise<string> {
  const script = fileURLToPath(new URL("../../../src/hub/windows-host.ps1", import.meta.url));
  try {
    const { stdout } = await exec(join(process.env.SystemRoot ?? "C:\\Windows", "System32/WindowsPowerShell/v1.0/powershell.exe"),
      ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, "-RequestPath", request, "-Recover"], { windowsHide: true, timeout: 15_000, maxBuffer: 4096 });
    return stdout.trim();
  } catch { return "unknown"; }
}

// The worker journal is evidence for a Hub Run, never another queue or Task store.
// Recovery observes; it never replays a command or blindly restores old file bytes.
export async function recoverLocalRuns(root: string, policy: LocalFilePolicy, store: HubStore): Promise<void> {
  for (const run of store.listRuns()) {
    if (run.capability_id !== "local" || !["Failed", "Interrupted"].includes(run.state)
      || run.effects !== "unknown" && run.cleanup_state === "clear") continue;
    const dir = join(root, "runs", run.id), request = join(dir, "request.json");
    try {
      const config = JSON.parse(await readFile(request, "utf8"));
      if (config.run_id !== run.id || config.root !== await policy.resolvePath(run.workspace_scope, ".", false, true)) continue;
      let result: CapabilityResult | undefined;
      try { result = JSON.parse(await readFile(join(dir, "result.json"), "utf8")); } catch { /* inspect durable phase below */ }
      if (!result) {
        if (await probeLocalWorker(request) !== "stopped") continue;
        let journal: any;
        try { journal = JSON.parse(await readFile(join(dir, "journal.json"), "utf8")); } catch { /* worker did not cross its write-ahead boundary */ }
        if (!journal || journal.kind === "command" && journal.phase === "created") {
          result = { state: "Failed", effects: "none", cleanup_state: "clear", error: { message: "Worker stopped before execution" } };
        } else if (journal.kind === "command") {
          result = journal.phase === "stopped" && journal.result ? journal.result : { state: "Failed", effects: "unknown", cleanup_state: "clear", error: { message: "Process tree stopped; command effects still require reconciliation" }, result: { identity: journal.identity, tree_empty: true } };
        } else if (journal.kind === "patch") {
          const observed: Array<{ path: string; sha256: string | null; before_sha256: string | null; after_sha256: string }> = [];
          for (const entry of journal.entries) {
            let sha256: string | null;
            try { sha256 = await fileHash(await policy.resolveRead(run.workspace_scope, entry.path)); }
            catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; sha256 = null; }
            observed.push({ path: entry.path, sha256, before_sha256: entry.before_sha256, after_sha256: entry.after_sha256 });
          }
          const none = observed.every(entry => entry.sha256 === entry.before_sha256), applied = observed.every(entry => entry.sha256 === entry.after_sha256);
          result = { state: "Failed", effects: none ? "none" : applied ? "applied" : "partial", cleanup_state: "clear", result: { changes: observed, summary: "Interrupted patch reconciled against current files; no recovery writes performed" } };
        }
      }
      if (!result) continue;
      const proof = await readFile(join(dir, "journal.json"), "utf8").catch(() => "");
      result.artifacts = [{ ref: proof ? join(dir, "journal.json") : request, fingerprint: proof ? fingerprint(JSON.parse(proof)) : await fileHash(request), ownership: "recovery" }];
      if (config.kind === "command") {
        result.result = { ...(result.result as Record<string, unknown> ?? {}), source_manifest: config.sources, source_fingerprint: config.execution_fingerprint };
        // A crash result cannot verify a current source version without a fresh run.
        if (config.verification_kind) result.verification = { kind: config.verification_kind, artifact_ref: `workspace:${config.root}`, fingerprint: config.execution_fingerprint, passed: false };
      }
      if (config.kind === "patch" && result.effects !== "none") result.artifacts.push({ ref: `workspace:${config.root}`, fingerprint: `patch:${run.id}`, ownership: "project" });
      store.recordRunResult(run.id, result);
    } catch { /* Preserve unknown and its resource ownership without evidence. */ }
  }
}
