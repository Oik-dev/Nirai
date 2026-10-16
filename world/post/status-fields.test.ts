import assert from 'node:assert/strict';
import { test } from 'node:test';
import { residentPostStatus } from './status.ts';
import type { Line } from './letters.ts';

const letter = (id: string, work: string, from = 'Claude'): Line => ({
  kind: 'letter', ts: '2026-10-16T00:00:00Z', id, work, from, to: 'Holo', body: id,
});
const read = (work: string): Line => ({ kind: 'read', ts: '2026-10-16T00:00:01Z', work });
const done = (id: string): Line => ({ kind: 'done', ts: '2026-10-16T00:00:02Z', letter: id });

test('awake is exposed even if the folded state reports a different priority', () => {
  const status = residentPostStatus('Holo', [], true);
  assert.equal(status.awake, true);
  assert.equal(status.unread, 0);
});

test('unread counts only incoming unfinished letters not yet read in their own work', () => {
  const lines: Line[] = [
    letter('a', 'first'), letter('self', 'first', 'Holo'),
    letter('b', 'second'), read('first'), letter('c', 'first'),
    done('b'), letter('d', 'second'),
  ];
  assert.equal(residentPostStatus('Holo', lines, false).unread, 2);
  assert.equal(residentPostStatus('Holo', [...lines, read('second')], false).unread, 1);
  assert.equal(residentPostStatus('Holo', [...lines, read('first')], false).unread, 1);
});
