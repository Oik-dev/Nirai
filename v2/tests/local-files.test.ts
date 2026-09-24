import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { localFiles, LocalFilePolicy } from "../src/hub/local-files.js";

test("local file reads are bounded and reject scope escapes, linked targets, credentials and Hub state", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-file-"));
  const workspace = join(root, "workspace");
  const protectedRoot = join(workspace, "hub");
  mkdirSync(protectedRoot, { recursive: true });
  writeFileSync(join(workspace, "source.txt"), "abcdef");
  writeFileSync(join(workspace, ".env"), "private");
  writeFileSync(join(protectedRoot, "data.txt"), "private");
  symlinkSync(protectedRoot, join(workspace, "link"), "junction");
  const capability = localFiles(new LocalFilePolicy([protectedRoot]));
  const context = { task_id: "task", run_id: "run", turn_id: null, control_epoch: 1, workspace_scope: workspace };
  const read = (path: string) => capability.invoke("read", { path }, context);
  try {
    const result = await capability.invoke("read", { path: "source.txt", max_bytes: 3 }, context);
    assert.ok("result" in result);
    assert.deepEqual([result.result && (result.result as any).content, (result.result as any).truncated, (result.result as any).fingerprint_scope], ["abc", true, "returned_bytes"]);
    for (const path of ["../outside.txt", ".env", "hub/data.txt", "link/data.txt", "source.txt:stream"]) await assert.rejects(() => read(path));
    await assert.rejects(() => capability.invoke("read", { path: "source.txt" }, { ...context, workspace_scope: null }));
    await capability.cancel!(context.run_id);
    const cancelled = await read("source.txt");
    assert.ok("state" in cancelled && cancelled.state === "Cancelled");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
