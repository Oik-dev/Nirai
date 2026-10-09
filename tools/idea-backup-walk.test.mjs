import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { walk } from './idea-backup-walk.mjs';

test('walk skips incomplete motion files and SQLite sidecars', () => {
  const root = mkdtempSync(join(tmpdir(), 'nirai-backup-test-'));
  try {
    const dir = join(root, 'body', 'motions');
    mkdirSync(dir, { recursive: true });
    for (const file of ['learned.vrma', 'unfinished.vrma.partial', 'still.txt.partial', 'idea.db', 'idea.db-wal', 'idea.db-shm']) {
      writeFileSync(join(dir, file), 'test');
    }
    assert.deepEqual([...walk(root)].map(path => relative(root, path)).sort(), [
      join('body', 'motions', 'idea.db'),
      join('body', 'motions', 'learned.vrma'),
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
