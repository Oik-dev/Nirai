// 住人の魂を、Gドライブへ日ごとに写す（タスクスケジューラ「Nirai Soul Backup」が毎晩呼ぶ）。
// 各日のフォルダーはそれだけで完全な写し。前の日から変わっていないファイルはハードリンクで共有するので、増えるのは変わった分だけ。
// SQLiteのDBは、書き込み中でも壊れた写しにならないよう、SQLiteのバックアップ機能で読み取り専用に写す（-wal・-shmは不要）。
// 古い日の写しは消さない。戻すときは、戻したい日のフォルダーから写し戻す（その住人の心は止めてから）。
import { appendFileSync, copyFileSync, existsSync, linkSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, statfsSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { backup, DatabaseSync } from "node:sqlite";

const ROOT = "G:\\Nirai-Backups\\daily";
const LOG = "G:\\Nirai-Backups\\backup.log";
const SOURCES = {
  Residents: "D:\\Products\\Residents",
};
const LOW_SPACE_GB = 10;

function log(line) {
  try {
    appendFileSync(LOG, `${line}\n`);
  } catch {
    console.error(line);
  }
}

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else if (entry.isFile() && !/\.db-(wal|shm)$/.test(entry.name)) yield path;
  }
}

// 変わったかどうかの目印。DBは、まだ本体へ書き戻されていない-walの変化も見る。
function fingerprint(path) {
  const stamp = p => (existsSync(p) ? `${statSync(p).size}:${statSync(p).mtimeMs}` : "-");
  return path.endsWith(".db") ? `${stamp(path)}|${stamp(`${path}-wal`)}` : stamp(path);
}

async function backupDb(src, dst) {
  const db = new DatabaseSync(src, { readOnly: true });
  try {
    await backup(db, dst);
  } finally {
    db.close();
  }
}

function readManifest(day) {
  try {
    return JSON.parse(readFileSync(join(ROOT, day, "manifest.json"), "utf8"));
  } catch {
    return {};
  }
}

const today = new Date().toLocaleDateString("sv-SE"); // YYYY-MM-DD（ローカル時刻）
const target = join(ROOT, today);
try {
  mkdirSync(ROOT, { recursive: true });
  if (!existsSync(target)) {
    const work = `${target}.partial`; // 途中で止まった写しは、完成した日として扱わない
    rmSync(work, { recursive: true, force: true });
    const previous = readdirSync(ROOT).filter(name => /^\d{4}-\d{2}-\d{2}$/.test(name)).sort().at(-1);
    const before = previous ? readManifest(previous) : {};
    const manifest = {};
    let copied = 0, linked = 0, bytes = 0;
    for (const [name, src] of Object.entries(SOURCES)) {
      for (const file of walk(src)) {
        const rel = join(name, relative(src, file));
        const dst = join(work, rel);
        manifest[rel] = fingerprint(file);
        mkdirSync(dirname(dst), { recursive: true });
        if (before[rel] === manifest[rel]) {
          try {
            linkSync(join(ROOT, previous, rel), dst);
            linked++;
            continue;
          } catch {} // リンク数の上限などで共有できないときは、写し直す
        }
        if (file.endsWith(".db")) await backupDb(file, dst);
        else copyFileSync(file, dst);
        copied++;
        bytes += statSync(dst).size;
      }
    }
    writeFileSync(join(work, "manifest.json"), JSON.stringify(manifest, null, 1));
    renameSync(work, target);
    const { bavail, bsize } = statfsSync("G:\\");
    const freeGB = (bavail * bsize) / 1024 ** 3;
    log(`${today} ok copied=${copied} linked=${linked} copiedMB=${(bytes / 1024 ** 2).toFixed(1)} freeGB=${freeGB.toFixed(1)}${freeGB < LOW_SPACE_GB ? " LOW-SPACE" : ""}`);
  }
} catch (error) {
  log(`${new Date().toISOString()} error ${error?.stack ?? error}`);
  process.exitCode = 1;
}
