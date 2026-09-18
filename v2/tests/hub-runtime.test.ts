import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { HubRuntime } from "../src/hub/runtime.js";

test("same Data Root cannot be opened by two Hub runtimes", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-v2-lock-"));
  const first = await HubRuntime.start(root);

  try {
    await assert.rejects(() => HubRuntime.start(root), /already using this Data Root/);
  } finally {
    await first.close();
    rmSync(root, { recursive: true, force: true });
  }
});
