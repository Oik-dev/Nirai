import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve, sep } from 'node:path';
import { collectUsage, meterDefaults, type MeterOptions } from './usage.ts';

// 集計の試験は、OSの一時フォルダーに作った架空の記録だけで行う。
const scratch = resolve(tmpdir());
const secret = 'PRIVATE_SENTINEL_本文_会話_ツール結果_エラー';
const day = '2026-10-07';
const now = new Date(`${day}T12:00:00+09:00`);
type Report = Awaited<ReturnType<typeof collectUsage>>;
type Row = Record<string, unknown>;

async function fixture(t: TestContext, sources = true) {
  const root = await mkdtemp(join(scratch, '.tmp-usage-'));
  // 片付ける場所は、今作った使い捨て置き場に限る。
  assert.equal(dirname(root), scratch);
  assert.ok(basename(root).startsWith('.tmp-usage-'));
  t.after(async () => {
    assert.ok(resolve(root).startsWith(`${scratch}${sep}`));
    await rm(root, { recursive: true, force: true });
  });
  const residentsRoot = join(root, 'residents');
  const claudeProject = join(root, 'claude-project');
  for (const resident of ['Claude', 'Codex', 'Holo']) {
    await mkdir(join(residentsRoot, resident, 'lifelog', 'post'), { recursive: true });
  }
  if (sources) {
    await mkdir(claudeProject);
    await mkdir(join(residentsRoot, 'Codex', 'lifelog', 'codex-cli'));
  }
  async function write(relative: string, rows: Array<Row | string>) {
    const file = join(root, relative);
    await mkdir(dirname(file), { recursive: true });
    await writeFile(file, rows.map(row => typeof row === 'string' ? row : JSON.stringify(row)).join('\n') + '\n');
  }
  return {
    root, residentsRoot, claudeProject, write,
    options: { residentsRoot, claudeProject, from: day, to: day, now } satisfies MeterOptions,
    post: (resident: string, rows: Array<Row | string>, fileDay = day) => write(`residents/${resident}/lifelog/post/${fileDay}.jsonl`, rows),
    claude: (rows: Array<Row | string>, name = 'session') => write(`claude-project/${name}.jsonl`, rows),
    codex: (rows: Array<Row | string>, fileDay = day) => write(`residents/Codex/lifelog/codex-cli/${fileDay}.jsonl`, rows),
  };
}

const letter = (id: string, work: string): Row => ({ kind: 'letter', id, work, body: secret });
const wake = (ts: string, letters: string[]): Row => ({ kind: 'wake', ts, letters, error: secret });
const stop = (ts: string): Row => ({ kind: 'stop', ts, error: secret });
const at = (time: string, date = day) => `${date}T${time}:00+09:00`;
const claudeUsage = (input = 100, output = 10, cached = 0, write = 0) => ({
  input_tokens: input, output_tokens: output, cache_read_input_tokens: cached, cache_creation_input_tokens: write,
});
const assistant = (id: string, timestamp: string, entrypoint: string, usage = claudeUsage(), requestId = `request-${id}`): Row => ({
  type: 'assistant', timestamp, entrypoint, requestId,
  message: { id, usage, content: [{ type: 'text', text: secret }, { type: 'tool_use', input: { command: secret } }] },
  error: secret,
});
const thread = (id: string): Row => ({ type: 'thread.started', thread_id: id });
const turn = (input: number, cached: number, output: number, reasoning = 0): Row => ({
  type: 'turn.completed', usage: { input_tokens: input, cached_input_tokens: cached, output_tokens: output, reasoning_output_tokens: reasoning },
});
const person = (report: Report, resident: string, channel: string) => {
  const row = report.residents.find(row => row.resident === resident && row.channel === channel);
  assert.ok(row, `${resident} ${channel}の集計がある`);
  return row;
};
const work = (report: Report, resident: string, name: string) => {
  const row = report.works.find(row => row.resident === resident && row.work === name);
  assert.ok(row, `${resident}の仕事「${name}」の集計がある`);
  return row;
};
const emptyUsage = { input: 0, cachedInput: 0, cacheWrite: 0, output: 0, weighted: 0 };

async function snapshot(dir: string): Promise<Record<string, { bytes: string; mtime: number }>> {
  const result: Record<string, { bytes: string; mtime: number }> = {};
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      Object.assign(result, await snapshot(path));
    } else if (entry.isFile()) {
      result[path] = { bytes: (await readFile(path)).toString('base64'), mtime: (await stat(path)).mtimeMs };
    }
  }
  return result;
}

test('本文・会話・ツール・エラーを返さず、記録を一切書き換えない', async t => {
  const f = await fixture(t);
  await f.post('Claude', [letter('a', '集計の仕事'), wake(at('10:00'), ['a']), stop(at('10:20'))]);
  await f.post('Codex', [letter('b', '確認の仕事'), wake(at('10:00'), ['b']), stop(at('10:20'))]);
  await f.claude([
    assistant('m1', at('10:01'), 'sdk-cli'),
    { type: 'user', timestamp: at('10:02'), content: secret },
    { type: 'tool_result', content: secret },
    `{broken ${secret}`,
  ]);
  await f.codex([thread('t1'), turn(100, 40, 20, 10), { type: 'item.completed', item: { type: 'agent_message', text: secret } }, { type: 'error', message: secret }]);
  const before = await snapshot(f.root);
  const report = await collectUsage(f.options);
  assert.ok(report.totals.weighted > 0);
  assert.equal(report.quality.malformedLines, 1);
  assert.ok(!JSON.stringify(report).includes(secret));
  assert.deepEqual(await snapshot(f.root), before);
});

test('Claudeの同じ要求を複数ブロック・複数ファイルで一度だけ数え、GUIとCLIを分ける', async t => {
  const f = await fixture(t);
  await f.post('Claude', [letter('a', '建築'), wake(at('10:00'), ['a']), stop(at('10:20'))]);
  await f.claude([
    assistant('m1', at('10:01'), 'sdk-cli', claudeUsage(100, 3, 200, 40)),
    assistant('m1', at('10:02'), 'sdk-cli', claudeUsage(100, 8, 200, 40)),
    assistant('m2', at('10:03'), 'claude-desktop', claudeUsage(50, 4, 10, 0)),
  ], 'first');
  await f.claude([assistant('m1', at('10:01'), 'sdk-cli', claudeUsage(100, 5, 200, 40))], 'saved-again');
  const report = await collectUsage(f.options);
  assert.deepEqual(person(report, 'Claude', 'CLI').usage, { input: 100, cachedInput: 200, cacheWrite: 40, output: 8, weighted: 210 });
  assert.deepEqual(person(report, 'Claude', 'GUI').usage, { input: 50, cachedInput: 10, cacheWrite: 0, output: 4, weighted: 71 });
  assert.deepEqual(report.totals, { input: 150, cachedInput: 210, cacheWrite: 40, output: 12, weighted: 281 });
  assert.deepEqual(work(report, 'Claude', '建築').usage, person(report, 'Claude', 'CLI').usage);
  assert.equal(report.works.reduce((sum, row) => sum + (row.usage?.weighted ?? 0), 0), 210);
  assert.equal(person(report, 'Claude', 'GUI').minutes, null);
  assert.equal(report.quality.unassignedClaudeRequests, 0);
});

test('Claudeの要求IDが違えば同じメッセージIDでも別の要求として数える', async t => {
  const f = await fixture(t);
  await f.claude([
    assistant('same-message', at('10:00'), 'claude-desktop', claudeUsage(10, 1), 'request-a'),
    assistant('same-message', at('10:01'), 'claude-desktop', claudeUsage(20, 2), 'request-b'),
  ]);
  const report = await collectUsage(f.options);
  assert.equal(report.totals.input, 30);
  assert.equal(report.totals.output, 3);
});

test('Claudeの起動経路や仕事が分からない使用量は捨てず、推測で仕事へ割り当てない', async t => {
  const f = await fixture(t);
  await f.claude([
    assistant('cli', at('10:00'), 'sdk-cli', claudeUsage(10, 1)),
    assistant('unknown', at('10:01'), 'future-client', claudeUsage(20, 2)),
  ]);
  const report = await collectUsage(f.options);
  assert.equal(report.totals.weighted, 45);
  assert.equal(person(report, 'Claude', 'unknown').usage?.weighted, 30);
  assert.equal(work(report, 'Claude', '(unassigned)').usage?.weighted, 15);
  assert.equal(report.quality.unassignedClaudeRequests, 1);
  assert.equal(report.quality.unknownClaudeChannels, 1);
});

test('Codexはキャッシュを入力の内数として引き、思考出力を二重加算せず、ターンを合計する', async t => {
  const f = await fixture(t);
  await f.post('Codex', [letter('a', 'レビュー'), wake(at('09:00'), ['a']), stop(at('09:15'))]);
  await f.codex([thread('t1'), turn(1000, 800, 100, 40), turn(200, 100, 20, 10)]);
  const report = await collectUsage(f.options);
  const expected = { input: 300, cachedInput: 900, cacheWrite: 0, output: 120, weighted: 990 };
  assert.deepEqual(report.totals, expected);
  assert.deepEqual(person(report, 'Codex', 'CLI').usage, expected);
  assert.deepEqual(work(report, 'Codex', 'レビュー').usage, expected);
  assert.equal(work(report, 'Codex', 'レビュー').minutes, 15);
  assert.equal(report.quality.unmatchedCodexThreads, 0);
});

test('Codexは日ごとの目覚めとスレッド数が一致する日にだけ、順序で仕事へ対応させる', async t => {
  const f = await fixture(t);
  const yesterday = '2026-10-06';
  await f.post('Codex', [
    letter('a', '前日の仕事'), wake(at('09:00', yesterday), ['a']), stop(at('09:10', yesterday)),
    letter('b', '今日一つ目'), wake(at('09:00'), ['b']), stop(at('09:10')),
    letter('c', '今日二つ目'), wake(at('10:00'), ['c']), stop(at('10:10')),
  ]);
  await f.codex([thread('y1'), turn(10, 0, 1), thread('y2'), turn(20, 0, 2)], yesterday);
  await f.codex([thread('d1'), turn(30, 0, 3), thread('d2'), turn(40, 0, 4)]);
  const report = await collectUsage({ ...f.options, from: yesterday });
  assert.equal(work(report, 'Codex', '前日の仕事').usage?.weighted, 0);
  assert.equal(work(report, 'Codex', '(unassigned)').usage?.weighted, 45);
  assert.equal(work(report, 'Codex', '今日一つ目').usage?.weighted, 45);
  assert.equal(work(report, 'Codex', '今日二つ目').usage?.weighted, 60);
  assert.equal(report.quality.unmatchedCodexThreads, 2);
  assert.equal(report.totals.weighted, 150);
});

test('一度に複数の仕事を受けた目覚めは、使用量と時間を重複させない', async t => {
  const f = await fixture(t);
  await f.post('Claude', [
    letter('a', '城'), letter('b', '道'), letter('c', '城'),
    wake(at('10:00'), ['a', 'b', 'c']), stop(at('10:20')),
  ]);
  await f.claude([assistant('m1', at('10:01'), 'sdk-cli', claudeUsage(100, 10))]);
  const report = await collectUsage(f.options);
  const rows = report.works.filter(row => row.resident === 'Claude');
  assert.equal(rows.length, 1);
  assert.ok(rows[0].work.includes('城'));
  assert.ok(rows[0].work.includes('道'));
  assert.equal(rows.reduce((sum, row) => sum + (row.minutes ?? 0), 0), 20);
  assert.equal(rows.reduce((sum, row) => sum + row.wakes, 0), 1);
  assert.equal(rows.reduce((sum, row) => sum + (row.usage?.weighted ?? 0), 0), 150);
  assert.equal(person(report, 'Claude', 'CLI').minutes, 20);
});

test('大文字と小文字だけが違う仕事名は、同じ作業場として一つにまとめる', async t => {
  const f = await fixture(t);
  await f.post('Claude', [
    letter('a', 'JOB'), letter('b', 'job'), letter('c', 'Job'),
    wake(at('09:00'), ['a', 'b']), stop(at('09:10')),
    wake(at('10:00'), ['c']), stop(at('10:10')),
  ]);
  await f.claude([
    assistant('m1', at('09:01'), 'sdk-cli', claudeUsage(10, 1)),
    assistant('m2', at('10:01'), 'sdk-cli', claudeUsage(20, 2)),
  ]);
  const report = await collectUsage(f.options);
  const rows = report.works.filter(row => row.resident === 'Claude');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].work, 'job');
  assert.equal(rows[0].wakes, 2);
  assert.equal(rows[0].minutes, 20);
  assert.equal(rows[0].usage?.weighted, 45);
});

test('並列したClaude二筋のwakeとstopは独立した時間で集計し、重なる要求をそれぞれの仕事へ付ける', async t => {
  const f = await fixture(t);
  await f.post('Claude', [
    letter('a', 'Job-A'), letter('b', 'Job-B'),
    { ...wake(at('10:00'), ['a']), work: 'Job-A' },
    { ...wake(at('10:05'), ['b']), work: 'Job-B' },
    { ...stop(at('10:10')), work: 'Job-A' },
    { ...stop(at('10:15')), work: 'Job-B' },
  ]);
  await f.claude([
    assistant('request-a', at('10:01'), 'sdk-cli', claudeUsage(10, 1)),
    assistant('request-b', at('10:12'), 'sdk-cli', claudeUsage(20, 2)),
  ]);
  const report = await collectUsage(f.options);
  assert.equal(work(report, 'Claude', 'job-a').minutes, 10);
  assert.equal(work(report, 'Claude', 'job-b').minutes, 10);
  assert.equal(person(report, 'Claude', 'CLI').wakes, 2);
  assert.equal(work(report, 'Claude', 'job-a').usage?.weighted, 15);
  assert.equal(work(report, 'Claude', 'job-b').usage?.weighted, 30);
  assert.equal(report.quality.incompleteWakes, 0);
});

test('Codexの筋別ファイル名は日付と仕事名を復元し、並列の使用量を混ぜない', async t => {
  const f = await fixture(t);
  for (const [name, tokens] of [['Job-A', 10], ['Job-B', 20]] as const) {
    await f.codex([thread(name), turn(tokens, 0, 1)], `${day}.${Buffer.from(name.toLowerCase()).toString('hex')}`);
  }
  const report = await collectUsage(f.options);
  assert.equal(work(report, 'Codex', 'job-a').usage?.weighted, 15);
  assert.equal(work(report, 'Codex', 'job-b').usage?.weighted, 25);
  assert.equal(report.quality.unmatchedCodexThreads, 0);
});

test('日本時間の日付境界で絞り、開始日の午前0時を含み、翌日の午前0時を除く', async t => {
  const f = await fixture(t);
  await f.claude([
    assistant('before', '2026-10-06T14:59:59.999Z', 'claude-desktop', claudeUsage(1, 0)),
    assistant('start', '2026-10-06T15:00:00.000Z', 'claude-desktop', claudeUsage(2, 0)),
    assistant('last', '2026-10-07T14:59:59.999Z', 'claude-desktop', claudeUsage(4, 0)),
    assistant('after', '2026-10-07T15:00:00.000Z', 'claude-desktop', claudeUsage(8, 0)),
  ]);
  const report = await collectUsage({ ...f.options, now: new Date('2026-10-08T01:00:00+09:00') });
  assert.equal(report.totals.input, 6);
  assert.equal(report.days.length, 1);
  assert.equal(report.days[0].day, day);
});

test('日をまたぐ作業時間は期間内の分だけ数え、前日に始まった目覚めを当日に足さない', async t => {
  const f = await fixture(t);
  await f.post('Claude', [
    letter('a', '夜の仕事'), wake('2026-10-06T23:50:00+09:00', ['a']), stop(at('00:20')),
    letter('b', '翌日へ続く仕事'), wake(at('23:50'), ['b']), stop('2026-10-08T00:20:00+09:00'),
  ]);
  const report = await collectUsage({ ...f.options, now: new Date('2026-10-08T01:00:00+09:00') });
  assert.equal(work(report, 'Claude', '夜の仕事').minutes, 20);
  assert.equal(work(report, 'Claude', '夜の仕事').wakes, 0);
  assert.equal(work(report, 'Claude', '翌日へ続く仕事').minutes, 10);
  assert.equal(work(report, 'Claude', '翌日へ続く仕事').wakes, 1);
  assert.equal(person(report, 'Claude', 'CLI').minutes, 30);
});

test('終了記録のない現在の作業だけ経過時間を数え、古い作業は時間不明とする', async t => {
  const f = await fixture(t);
  await f.post('Claude', [letter('a', '作業中'), wake(at('11:45'), ['a'])]);
  await f.post('Codex', [letter('b', '古い中断'), wake(at('10:00'), ['b'])]);
  const report = await collectUsage(f.options);
  assert.equal(work(report, 'Claude', '作業中').active, true);
  assert.equal(work(report, 'Claude', '作業中').minutes, 15);
  assert.equal(work(report, 'Claude', '作業中').incompleteWakes, 0);
  assert.equal(work(report, 'Codex', '古い中断').active, false);
  assert.equal(work(report, 'Codex', '古い中断').minutes, null);
  assert.equal(work(report, 'Codex', '古い中断').incompleteWakes, 1);
  assert.equal(person(report, 'Codex', 'CLI').minutes, null);
  assert.equal(person(report, 'Codex', 'CLI').incompleteWakes, 1);
  assert.equal(report.quality.incompleteWakes, 1);
  const shortLimit = await collectUsage({ ...f.options, wakeLimits: { Claude: 10 * 60_000 } });
  assert.equal(work(shortLimit, 'Claude', '作業中').active, false);
  assert.equal(work(shortLimit, 'Claude', '作業中').minutes, null);
  assert.equal(work(shortLimit, 'Claude', '作業中').incompleteWakes, 1);
  assert.equal(shortLimit.quality.incompleteWakes, 2);
});

test('同じ仕事に終了済みと終了不明の作業が混在しても、測れた時間と不足分を別々に示す', async t => {
  const f = await fixture(t);
  await f.post('Claude', [
    letter('a', '継続の仕事'),
    wake(at('09:00'), ['a']), stop(at('09:10')),
    wake(at('10:00'), ['a']),
  ]);
  const report = await collectUsage(f.options);
  const row = work(report, 'Claude', '継続の仕事');
  assert.equal(row.wakes, 2);
  assert.equal(row.minutes, 10);
  assert.equal(row.incompleteWakes, 1);
  assert.equal(row.active, false);
  assert.equal(person(report, 'Claude', 'CLI').minutes, 10);
  assert.equal(person(report, 'Claude', 'CLI').incompleteWakes, 1);
  assert.equal(report.quality.incompleteWakes, 1);
});

test('使用量の記録がない状態と、測れている使用量ゼロを区別し、Holoの時間を示す', async t => {
  const f = await fixture(t, false);
  await f.post('Claude', [letter('a', '使用量不明'), wake(at('10:00'), ['a']), stop(at('10:10'))]);
  await f.post('Holo', [letter('b', '相談'), wake(at('10:00'), ['b']), stop(at('10:05'))]);
  const missing = await collectUsage(f.options);
  assert.equal(person(missing, 'Claude', 'CLI').usage, null);
  assert.equal(person(missing, 'Claude', 'CLI').minutes, 10);
  assert.equal(work(missing, 'Claude', '使用量不明').usage, null);
  assert.equal(work(missing, 'Claude', '使用量不明').minutes, 10);
  assert.equal(person(missing, 'Holo', 'GUI').usage, null);
  assert.equal(person(missing, 'Holo', 'GUI').minutes, 5);
  assert.equal(missing.coverage.find(row => row.resident === 'Claude' && row.channel === 'CLI')?.status, 'unmeasured');
  assert.equal(missing.coverage.find(row => row.resident === 'Codex' && row.channel === 'CLI')?.status, 'unmeasured');
  await mkdir(f.claudeProject);
  await mkdir(join(f.residentsRoot, 'Codex', 'lifelog', 'codex-cli'));
  const measured = await collectUsage(f.options);
  assert.deepEqual(person(measured, 'Claude', 'CLI').usage, emptyUsage);
  assert.deepEqual(work(measured, 'Claude', '使用量不明').usage, emptyUsage);
  assert.deepEqual(person(measured, 'Codex', 'CLI').usage, emptyUsage);
  assert.equal(measured.coverage.find(row => row.resident === 'Claude' && row.channel === 'CLI')?.status, 'measured');
  assert.equal(measured.coverage.find(row => row.resident === 'Holo')?.status, 'unmeasured');
});

test('壊れた行や不正な使用量を飛ばし、後に続く正しい記録を集計する', async t => {
  const f = await fixture(t);
  await f.claude([
    '{malformed', '', 'null', '42',
    assistant('negative', at('10:00'), 'sdk-cli', claudeUsage(-1, 2)),
    assistant('fraction', at('10:00'), 'sdk-cli', claudeUsage(1.5, 2)),
    assistant('bad-date', 'not-a-date', 'sdk-cli'),
    assistant('valid', at('10:00'), 'claude-desktop', claudeUsage(10, 2)),
  ]);
  await f.codex([thread('t1'), turn(10, 11, 1), turn(10, 0, 1)]);
  const report = await collectUsage(f.options);
  assert.equal(report.quality.malformedLines, 1);
  assert.equal(report.quality.invalidUsages, 4);
  assert.deepEqual(report.totals, { input: 20, cachedInput: 0, cacheWrite: 0, output: 3, weighted: 35 });
  assert.ok(!JSON.stringify(report).includes(secret));
});

test('日付として成立しないCodexのファイル名があっても有効な記録を集計する', async t => {
  const f = await fixture(t);
  await f.codex([thread('bad-day'), turn(100, 0, 10)], '2026-99-99');
  await f.codex([thread('good-day'), turn(10, 0, 1)]);
  const report = await collectUsage(f.options);
  assert.deepEqual(report.totals, { input: 10, cachedInput: 0, cacheWrite: 0, output: 1, weighted: 15 });
  assert.ok(report.quality.invalidUsages > 0 || report.quality.malformedLines > 0);
});

test('存在しない日付や逆順の期間を拒否し、既定期間は日本時間の今日まで7日とする', async t => {
  const f = await fixture(t);
  await assert.rejects(collectUsage({ ...f.options, from: '2026-02-30' }), /日付/);
  await assert.rejects(collectUsage({ ...f.options, from: '2026-10-08', to: '2026-10-07' }), /開始日/);
  await assert.rejects(collectUsage({ ...f.options, from: '10/07/2026' }), /日付/);
  const report = await collectUsage({ residentsRoot: f.residentsRoot, claudeProject: f.claudeProject, now: new Date('2026-10-06T15:00:00Z') });
  assert.deepEqual(report.range, { from: '2026-10-01', to: '2026-10-07' });
});

test('既定の場所は設定から組み立てるだけで、記録を読み始めない', async t => {
  const f = await fixture(t);
  const before = await snapshot(f.root);
  const defaults = meterDefaults(f.residentsRoot, join(f.root, 'source repo'));
  assert.equal(defaults.residentsRoot, f.residentsRoot);
  assert.equal(typeof defaults.claudeProject, 'string');
  assert.deepEqual(await snapshot(f.root), before);
});
