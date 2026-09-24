import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdir, open, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import type { CapabilityResult } from "./capability.js";

const script = fileURLToPath(new URL("../../../src/hub/windows-host.ps1", import.meta.url));

export class LocalWorker {
  private readonly children = new Map<string, ChildProcessWithoutNullStreams>();
  private readonly stopped = new Set<string>();
  constructor(readonly root: string) {}
  stop(runId: string): void {
    this.stopped.add(runId);
    this.children.get(runId)?.stdin.end("cancel\n");
  }
  isStopped(runId: string): boolean { return this.stopped.has(runId); }
  clear(runId: string): void { this.stopped.delete(runId); }
  async run(runId: string, config: Record<string, unknown>): Promise<CapabilityResult> {
    if (process.platform !== "win32") throw new Error("Local mutations require the Windows worker");
    if (this.stopped.has(runId)) return { state: "Cancelled", effects: "none", cleanup_state: "clear" };
    const dir = join(this.root, "runs", runId);
    await mkdir(dir, { recursive: true });
    const request = join(dir, "request.json");
    const file = await open(request, "wx", 0o600);
    try { await file.writeFile(JSON.stringify({ ...config, run_id: runId, parent_pid: process.pid })); await file.sync(); } finally { await file.close(); }
    if (this.stopped.has(runId)) return { state: "Cancelled", effects: "none", cleanup_state: "clear" };
    const child = spawn(join(process.env.SystemRoot ?? "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
      ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, "-RequestPath", request], { windowsHide: true, stdio: "pipe",
        env: { SystemRoot: process.env.SystemRoot, TEMP: process.env.TEMP, TMP: process.env.TMP,
          PSModulePath: join(process.env.SystemRoot ?? "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "Modules") } });
    this.children.set(runId, child);
    child.stdin.on("error", () => {});
    // Native diagnostics may contain source paths; the worker's saved result is the
    // bounded public result. Drain pipes without retaining arbitrary diagnostic text.
    child.stdout.resume(); child.stderr.resume();
    if (this.stopped.has(runId)) child.stdin.end("cancel\n");
    try {
      await new Promise<void>(resolve => { child.once("error", () => resolve()); child.once("close", () => resolve()); });
      try {
        const result = JSON.parse(await readFile(join(dir, "result.json"), "utf8")) as CapabilityResult;
        let artifact = join(dir, "journal.json");
        try { await readFile(artifact); } catch { artifact = request; }
        result.artifacts = [{ ref: artifact, fingerprint: await fileHash(artifact), ownership: "recovery" }];
        return result;
      } catch {
        // Launch/worker failure without durable final evidence cannot clear ownership.
        return { state: "Failed", effects: "unknown", cleanup_state: "unknown", error: { message: "Local worker ended without a verifiable result" } };
      }
    } finally { this.children.delete(runId); this.stopped.delete(runId); }
  }
}

export async function fileHash(path: string): Promise<string> {
  const { createHash } = await import("node:crypto");
  const file = await open(path, "r");
  try { const hash = createHash("sha256"); for await (const chunk of file.createReadStream({ autoClose: false })) hash.update(chunk); return hash.digest("hex"); }
  finally { await file.close(); }
}
