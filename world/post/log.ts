// 郵便局の記録。本番（--live）では、画面（console）に出すものを world/runtime/post.log にも残す。
// タスクスケジューラが画面なしで起こすときも、記録が残るように。

import { createWriteStream, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { format } from "node:util";

export const LOG_FILE = join(import.meta.dirname, "..", "runtime", "post.log");

export function logToFile(file = LOG_FILE): void {
  mkdirSync(dirname(file), { recursive: true });
  const stream = createWriteStream(file, { flags: "a" });
  for (const level of ["log", "error"] as const) {
    const original = console[level].bind(console);
    console[level] = (...args: unknown[]) => {
      original(...args);
      stream.write(format(...args) + "\n");
    };
  }
}
