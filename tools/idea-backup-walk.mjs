// バックアップ対象は完成したファイルだけ。書き込み途中の .partial と SQLite の作業ファイルは除く。
import { readdirSync } from 'node:fs';
import { join } from 'node:path';

export function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else if (entry.isFile() && !/\.partial$|\.db-(?:wal|shm)$/.test(entry.name)) yield path;
  }
}
