import { test } from 'node:test';
import assert from 'node:assert/strict';
import { asks, waits, type Line, MASTER } from './letters.ts';

const at = (second: number) => new Date(Date.UTC(2026, 9, 15, 0, 0, second)).toISOString();
const letter = (id: string, to: string, work: string): Line =>
  ({ kind: 'letter', ts: at(0), id, from: 'Claude', to, work, body: `依頼${id}` });
const note = (id: string, second: number, body: string, waiting_for = MASTER): Line =>
  ({ kind: 'note', ts: at(second), letter: id, body, ...(waiting_for ? { waiting_for } : {}) });
const answer = (id: string, second: number): Line =>
  ({ kind: 'letter', ts: at(second), id: `reply-${second}-${id}`, from: MASTER, to: 'Holo',
     work: 'job-1', reply_to: id, body: 'はい' });

test('全部の住人・作業場の問いが、席なしでも古い順に出る', () => {
  const team = [
    [letter('A', 'Holo', 'job-1'), note('A', 3, 'Aの問い')],
    [letter('B', 'Codex', 'job-2'), note('B', 2, 'Bの問い')],
    [letter('C', 'Claude', 'job-3'), note('C', 5, '別の仕事を待つ', 'B')],
  ];
  assert.deepEqual(asks(team).map(a => [a.letter, a.resident, a.work, a.question]), [
    ['B', 'Codex', 'job-2', 'Bの問い'], ['A', 'Holo', 'job-1', 'Aの問い'],
  ]);
  assert.equal(asks(team)[0].letterBody, '依頼B');
  assert.equal(waits(team).get('C'), 'B');
});

test('Masterの返事が最後の問いより後なら解け、再質問すると新しいチケットになる', () => {
  const team: Line[][] = [[letter('A', 'Holo', 'job-1'), note('A', 1, '最初')]];
  assert.equal(asks(team).length, 1);
  team[0].push(answer('A', 2));
  assert.equal(asks(team).length, 0);
  team[0].push(note('A', 3, 'もう一度'));
  assert.equal(asks(team)[0].question, 'もう一度');
  team[0].push(answer('A', 4));
  assert.equal(asks(team).length, 0);
  team[0].push(note('A', 5, '進める', ''));
  assert.equal(waits(team).has('A'), false);
});

test('後から届いた古い返事では新しい問いは解けず、doneでも消える', () => {
  const team: Line[][] = [[letter('A', 'Holo', 'job-1'), note('A', 5, '最新'), answer('A', 4)]];
  assert.equal(asks(team).length, 1);
  team[0].push({ kind: 'done', ts: at(6), letter: 'A' });
  assert.equal(asks(team).length, 0);
});
