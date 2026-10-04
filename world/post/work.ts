// 作業場（D:\Products\Work の下の依頼ごとのフォルダー）の寿命を、手紙に結び付ける。
// 名前を付けた手紙が届いたら作り、その名前を付けた手紙が全部済んだら片付ける。帳簿は持たず、毎回生ログから決める。
// 片付けは消さずにごみ箱へ送る（作業場は使い捨てだが、間違えても戻せるように）。

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import type { Line } from "./letters.ts";
import { unfinished } from "./letters.ts";

/** 片付けてよい作業場の名前。手紙に名前が出てきて、その名前の手紙が全部済んでいるもの。 */
export function toClean(folders: string[], linesOfTeam: Line[][]): string[] {
  const named = new Set<string>();
  const open = new Set<string>();
  for (const lines of linesOfTeam) {
    for (const line of lines) if (line.kind === "letter" && line.work) named.add(line.work);
    for (const letter of unfinished(lines)) if (letter.work) open.add(letter.work);
  }
  return folders.filter(name => named.has(name) && !open.has(name));
}

export function ensureWork(workRoot: string, name: string): void {
  mkdirSync(inside(workRoot, name), { recursive: true });
}

export function folders(workRoot: string): string[] {
  return existsSync(workRoot) ? readdirSync(workRoot, { withFileTypes: true }).filter(e => e.isDirectory()).map(e => e.name) : [];
}

/** ごみ箱へ送る。作業場の直下のフォルダーしか扱わない。 */
export function recycle(workRoot: string, name: string): boolean {
  const path = inside(workRoot, name);
  const script = `Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($env:NIRAI_RECYCLE, 'OnlyErrorDialogs', 'SendToRecycleBin')`;
  const result = spawnSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], {
    windowsHide: true, env: { ...process.env, NIRAI_RECYCLE: path },
  });
  return result.status === 0 && !existsSync(path);
}

function inside(workRoot: string, name: string): string {
  const path = resolve(workRoot, name);
  const rel = relative(resolve(workRoot), path);
  if (!rel || rel.startsWith("..") || rel.includes("\\") || rel.includes("/")) throw new Error(`作業場の外: ${name}`);
  return path;
}
