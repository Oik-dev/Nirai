// 作業場（D:\Products\Work の下の依頼ごとのフォルダー）の寿命を、手紙に結び付ける。
// 名前を付けた手紙が届いたら作り、その名前を付けた手紙が全部済み、最後のdoneから1回の目覚めの長さがたち、
// その中で動いているコマンド（Holoの手）もなくなったら片付ける。
// 帳簿は持たず、毎回生ログから決める。
// 片付けは消さずにごみ箱へ送る（作業場は使い捨てだが、間違えても戻せるように）。

import { spawnSync } from "node:child_process";
import { existsSync, lstatSync, mkdirSync, readdirSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import type { Line } from "./letters.ts";
import { unfinished } from "./letters.ts";

/** 作業場の名前を比べるときの形。Windowsのフォルダー名は大文字と小文字を区別しない（job と JOB は同じ作業場）。 */
export function workKey(name: string): string {
  return name.toLowerCase();
}

/** 片付けてよい作業場の名前。生ログのdone時刻と時計、手のbusyだけで決める。 */
export function toClean(
  folders: string[], linesOfTeam: Line[][], busy: ReadonlySet<string> = new Set(), now = new Date(), keepMs = 0,
): string[] {
  const named = new Set<string>();
  const open = new Set<string>();
  const lastDone = new Map<string, number>();
  for (const lines of linesOfTeam) {
    const workOf = new Map<string, string>();
    for (const line of lines) {
      if (line.kind !== "letter" || !line.work) continue;
      const key = workKey(line.work);
      named.add(key);
      workOf.set(line.id, key);
    }
    for (const line of lines) {
      if (line.kind !== "done") continue;
      const key = workOf.get(line.letter);
      if (!key) continue;
      const at = Date.parse(line.ts);
      if (!Number.isFinite(at)) continue;
      lastDone.set(key, Math.max(lastDone.get(key) ?? -Infinity, at));
    }
    for (const letter of unfinished(lines)) if (letter.work) open.add(workKey(letter.work));
  }
  return folders.filter(name => {
    const key = workKey(name);
    const doneAt = lastDone.get(key);
    return named.has(key) && !open.has(key) && !busy.has(key)
      && doneAt !== undefined && now.getTime() - doneAt >= keepMs;
  });
}

export function ensureWork(workRoot: string, name: string): void {
  mkdirSync(workPath(workRoot, name), { recursive: true });
}

export function folders(workRoot: string): string[] {
  return existsSync(workRoot) ? readdirSync(workRoot, { withFileTypes: true }).filter(e => e.isDirectory()).map(e => e.name) : [];
}

/** ごみ箱へ送る。作業場の直下のフォルダーしか扱わない。
 *  中にリンク（ジャンクション・シンボリックリンク）が1つでもあれば片付けない。リンクの先は作業場の外かもしれず、
 *  消し方の道具がリンクをどう扱うかに頼らないため（2026-10-04、Codexのレビュー）。残ったフォルダーはMasterが決める。 */
export function recycle(workRoot: string, name: string): "recycled" | "has-links" | "failed" {
  const path = workPath(workRoot, name);
  if (hasLinks(workRoot, false) || hasLinks(path, true)) return "has-links";
  const script = `Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($env:NIRAI_RECYCLE, 'OnlyErrorDialogs', 'SendToRecycleBin')`;
  const result = spawnSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], {
    windowsHide: true, env: { ...process.env, NIRAI_RECYCLE: path },
  });
  return result.status === 0 && !existsSync(path) ? "recycled" : "failed";
}

/** path そのもの（deep なら中も、リンクをたどらずに）がリンクか。Node は Windows のジャンクションもリンクとして扱う。 */
function hasLinks(path: string, deep: boolean): boolean {
  if (lstatSync(path).isSymbolicLink()) return true;
  if (!deep) return false;
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) return true;
    if (entry.isDirectory() && hasLinks(join(path, entry.name), true)) return true;
  }
  return false;
}

/** 作業場のフォルダー。Work の直下のフォルダー名でなければ投げる。 */
export function workPath(workRoot: string, name: string): string {
  const path = resolve(workRoot, name);
  const rel = relative(resolve(workRoot), path);
  if (!rel || rel.startsWith("..") || rel.includes("\\") || rel.includes("/")) throw new Error(`作業場の外: ${name}`);
  return path;
}
