import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import test from 'node:test';

const dir = dirname(fileURLToPath(import.meta.url));
const script = join(dir, 'look.mjs');
const output = join(dir, 'unused-preview');

test('存在しない動きやフォルダー外の指定をChrome起動前に拒む', () => {
  for (const name of ['missing-motion.vrma', '../outside.vrma']) {
    const result = spawnSync(process.execPath, [script, output, name], {
      timeout: 2500, encoding: 'utf8',
    });
    assert.equal(result.status, 1, name);
    assert.equal(result.error, undefined, name);
    assert.match(result.stderr, /window\/assets\/motions|motion filename/u, name);
  }
});
