// 使用量だけを読む。会話・手紙の本文を返さず、記録への書き込みもしない。
import { createReadStream } from 'node:fs';
import { readdir } from 'node:fs/promises';
import { homedir } from 'node:os';
import { basename, join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { workKey } from './work.ts';

type Usage = { input: number; cachedInput: number; cacheWrite: number; output: number; weighted: number };
type Channel = 'GUI' | 'CLI' | 'unknown';
type Quality = {
  malformedLines: number; readErrors: number; invalidUsages: number; incompleteWakes: number;
  missingSources: string[]; unmatchedCodexThreads: number; unassignedClaudeRequests: number; unknownClaudeChannels: number;
};
type Span = { resident: string; start: number; end: number; work: string; active: boolean; timed: boolean };
type Event = { kind: string; ts: number; id?: string; work?: string; letters?: string[] };
export type MeterOptions = {
  residentsRoot: string; claudeProject: string; from?: string; to?: string; now?: Date;
  wakeLimits?: Record<string, number>;
};
const DAY_MS = 86_400_000;
const JST = 9 * 3_600_000;
const UNASSIGNED = '(unassigned)';
export const jstDay = (ms: number) => new Date(ms + JST).toISOString().slice(0, 10);
const count = (n: unknown): n is number => Number.isSafeInteger(n) && Number(n) >= 0;
const blank = (): Usage => ({ input: 0, cachedInput: 0, cacheWrite: 0, output: 0, weighted: 0 });
function add(a: Usage, b: Usage) {
  for (const key of ['input', 'cachedInput', 'cacheWrite', 'output'] as const) a[key] += b[key];
  a.weighted = Math.round((a.input + a.cachedInput * .1 + a.cacheWrite * 1.25 + a.output * 5) * 100) / 100;
}
function usage(row: any, codex: boolean, quality: Quality): Usage | undefined {
  if (!row || typeof row !== 'object') return;
  const cached = (codex ? row.cached_input_tokens : row.cache_read_input_tokens) ?? 0;
  const write = (codex ? row.cache_write_input_tokens : row.cache_creation_input_tokens) ?? 0;
  if (![row.input_tokens, row.output_tokens, cached, write].every(count) || (codex && cached > row.input_tokens)) {
    quality.invalidUsages++;
    return;
  }
  const result = blank();
  add(result, { input: row.input_tokens - (codex ? cached : 0), cachedInput: cached, cacheWrite: write, output: row.output_tokens, weighted: 0 });
  return result;
}
export function meterDefaults(residentsRoot: string, sourceRepo: string): MeterOptions {
  // Claude自身が使うプロジェクト名と同じパスの変換。memory/やサブエージェントの記録は開かない。
  const project = resolve(sourceRepo).replace(/[^a-zA-Z0-9]/g, '-');
  return { residentsRoot, claudeProject: process.env.NIRAI_CLAUDE_PROJECT ?? join(homedir(), '.claude', 'projects', project) };
}
function dateStart(day: string): number {
  const ms = Date.parse(`${day}T00:00:00+09:00`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(ms) || jstDay(ms) !== day) throw new Error('日付は YYYY-MM-DD で指定してください。');
  return ms;
}
async function files(dir: string, label: string, quality: Quality): Promise<string[]> {
  try {
    return (await readdir(dir, { withFileTypes: true })).filter(f => f.isFile() && f.name.endsWith('.jsonl')).map(f => join(dir, f.name)).sort();
  } catch (error: any) {
    if (error.code === 'ENOENT') { quality.missingSources.push(label); return []; }
    // OSのエラーに含まれるパスや記録の断片を画面へ渡さない。
    throw new Error(`${label}の記録を読み取れません。`);
  }
}
async function scan(paths: string[], quality: Quality, accept: (row: any, file: string) => void) {
  for (const file of paths) {
    const stream = createReadStream(file, { encoding: 'utf8' });
    const lines = createInterface({ input: stream, crlfDelay: Infinity });
    try {
      for await (const raw of lines) {
        if (!raw.trim()) continue;
        let row;
        try { row = JSON.parse(raw); } catch { quality.malformedLines++; continue; }
        // 本文は参照しない。acceptは必要な数・時刻・識別子だけを取り出し、rowを保持しない。
        if (row && typeof row === 'object') accept(row, file);
      }
    } catch { quality.readErrors++; }
    finally { lines.close(); stream.destroy(); }
  }
}
async function spansOf(resident: string, options: MeterOptions, quality: Quality, now: number): Promise<Span[]> {
  const events: Event[] = [];
  const works = new Map<string, string>();
  await scan(await files(join(options.residentsRoot, resident, 'lifelog', 'post'), `${resident} 郵便`, quality), quality, row => {
    if (row.kind === 'letter' && typeof row.id === 'string') {
      works.set(row.id, typeof row.work === 'string' && row.work ? workKey(row.work) : UNASSIGNED);
    } else if (row.kind === 'wake' || row.kind === 'stop') {
      const ts = typeof row.ts === 'string' ? Date.parse(row.ts) : NaN;
      if (Number.isFinite(ts)) events.push({ kind: row.kind, ts, letters: row.kind === 'wake' && Array.isArray(row.letters) ? row.letters.filter((id: unknown) => typeof id === 'string') : [] });
    }
  });
  events.sort((a, b) => a.ts - b.ts);
  const spans: Span[] = [];
  const limit = options.wakeLimits?.[resident] ?? (resident === 'Holo' ? 30 : 50) * 60_000;
  let pending: Event | undefined;
  function close(end: number, stopped: boolean, current = false) {
    if (!pending) return;
    const active = current && end === now && now - pending.ts < limit;
    if (!stopped && !active) quality.incompleteWakes++;
    const names = [...new Set(pending.letters!.map(id => works.get(id) ?? UNASSIGNED))].sort();
    spans.push({ resident, start: pending.ts, end: Math.max(pending.ts, end), work: names.join(' / ') || UNASSIGNED, active, timed: stopped || active });
    pending = undefined;
  }
  for (const event of events) {
    if (event.ts > now) continue;
    if (event.kind === 'wake') {
      if (pending) close(Math.min(event.ts, pending.ts + limit), false);
      pending = event;
    } else close(event.ts, true);
  }
  if (pending) close(Math.min(now, pending.ts + limit), false, true);
  return spans;
}

export async function collectUsage(options: MeterOptions) {
  const now = (options.now ?? new Date()).getTime();
  const from = options.from ?? jstDay(now - 6 * DAY_MS);
  const to = options.to ?? jstDay(now);
  const start = dateStart(from), end = dateStart(to) + DAY_MS;
  if (start >= end) throw new Error('開始日は終了日以前にしてください。');
  const quality: Quality = { malformedLines: 0, readErrors: 0, invalidUsages: 0, incompleteWakes: 0, missingSources: [], unmatchedCodexThreads: 0, unassignedClaudeRequests: 0, unknownClaudeChannels: 0 };
  const spans: Span[] = [];
  for (const resident of ['Claude', 'Codex', 'Holo']) spans.push(...await spansOf(resident, options, quality, now));
  const totals = blank();
  const residents = new Map<string, { resident: string; channel: Channel; usage: Usage | null; minutes: number | null; wakes: number; incompleteWakes: number }>();
  const days = new Map<string, { day: string; resident: string; channel: Channel; usage: Usage }>();
  const works = new Map<string, { work: string; resident: string; usage: Usage | null; minutes: number | null; wakes: number; active: boolean; incompleteWakes: number }>();
  function residentRow(resident: string, channel: Channel) {
    const key = JSON.stringify([resident, channel]);
    if (!residents.has(key)) residents.set(key, { resident, channel, usage: resident === 'Holo' ? null : blank(), minutes: channel === 'CLI' || resident === 'Holo' ? 0 : null, wakes: 0, incompleteWakes: 0 });
    return residents.get(key)!;
  }
  function workRow(resident: string, work: string) {
    const key = JSON.stringify([resident, work]);
    if (!works.has(key)) works.set(key, { work, resident, usage: resident === 'Holo' ? null : blank(), minutes: 0, wakes: 0, active: false, incompleteWakes: 0 });
    return works.get(key)!;
  }
  for (const span of spans) {
    const overlap = Math.max(0, Math.min(span.end, end) - Math.max(span.start, start));
    if (!overlap && !(span.start >= start && span.start < end)) continue;
    const row = workRow(span.resident, span.work);
    const person = residentRow(span.resident, span.resident === 'Holo' ? 'GUI' : 'CLI');
    const minutes = span.timed ? overlap / 60_000 : 0;
    row.minutes! += minutes; person.minutes! += minutes;
    if (!span.timed) { row.incompleteWakes++; person.incompleteWakes++; }
    if (span.start >= start && span.start < end) { row.wakes++; person.wakes++; }
    row.active ||= span.active;
  }
  for (const row of [...works.values(), ...residents.values()]) {
    if (row.minutes === 0 && row.incompleteWakes > 0) row.minutes = null;
  }
  function record(resident: string, channel: Channel, ts: number, value: Usage, work?: string) {
    if (ts < start || ts >= end) return;
    add(totals, value);
    add(residentRow(resident, channel).usage!, value);
    const day = jstDay(ts);
    const key = JSON.stringify([day, resident, channel]);
    if (!days.has(key)) days.set(key, { day, resident, channel, usage: blank() });
    add(days.get(key)!.usage, value);
    if (channel === 'CLI') add(workRow(resident, work ?? UNASSIGNED).usage!, value);
  }
  // assistantの複数ブロックや再保存は同じAPI要求。出力は後のブロックで増えるため最大値を採る。
  const requests = new Map<string, { ts: number; channel: Channel; usage: Usage }>();
  await scan(await files(options.claudeProject, 'Claude 使用量', quality), quality, row => {
    if (row.type !== 'assistant' || !row.message?.usage) return;
    const ts = typeof row.timestamp === 'string' ? Date.parse(row.timestamp) : NaN;
    const id = row.message.id;
    if (!Number.isFinite(ts) || typeof id !== 'string') { quality.invalidUsages++; return; }
    const value = usage(row.message.usage, false, quality);
    if (!value) return;
    const key = JSON.stringify([id, typeof row.requestId === 'string' ? row.requestId : null]);
    const channel: Channel = row.entrypoint === 'claude-desktop' ? 'GUI' : row.entrypoint === 'sdk-cli' ? 'CLI' : 'unknown';
    const previous = requests.get(key);
    if (!previous) requests.set(key, { ts, channel, usage: value });
    else {
      previous.ts = Math.min(previous.ts, ts);
      for (const field of ['input', 'cachedInput', 'cacheWrite', 'output'] as const) previous.usage[field] = Math.max(previous.usage[field], value[field]);
    }
  });
  const claudeSpans = spans.filter(s => s.resident === 'Claude');
  for (const request of requests.values()) {
    if (request.ts < start || request.ts >= end) continue;
    const span = request.channel === 'CLI' ? claudeSpans.find(s => request.ts >= s.start && request.ts <= s.end) : undefined;
    if (request.channel === 'CLI' && !span) quality.unassignedClaudeRequests++;
    if (request.channel === 'unknown') quality.unknownClaudeChannels++;
    record('Claude', request.channel, request.ts, request.usage, span?.work);
  }
  // Codexのstdoutに時刻はない。開始日ごとにwakeとthreadの数が一致した場合だけ順序で対応させる。
  type Thread = { day: string; usage: Usage };
  const threads = new Map<string, Thread>();
  let current: string | undefined;
  let currentFile: string | undefined;
  await scan(await files(join(options.residentsRoot, 'Codex', 'lifelog', 'codex-cli'), 'Codex 使用量', quality), quality, (row, file) => {
    if (currentFile !== file) { currentFile = file; current = undefined; }
    if (row.type === 'thread.started') {
      current = typeof row.thread_id === 'string' ? row.thread_id : undefined;
      const day = basename(file, '.jsonl');
      let validDay = false;
      try { dateStart(day); validDay = true; } catch { quality.invalidUsages++; }
      if (current && !threads.has(current) && validDay) threads.set(current, { day, usage: blank() });
    } else if (row.type === 'turn.completed') {
      const value = usage(row.usage, true, quality);
      if (value && current && threads.has(current)) add(threads.get(current)!.usage, value);
      else if (value) quality.invalidUsages++;
    }
  });
  const codexByDay = new Map<string, Thread[]>();
  for (const thread of threads.values()) {
    if (!codexByDay.has(thread.day)) codexByDay.set(thread.day, []);
    codexByDay.get(thread.day)!.push(thread);
  }
  for (const [day, dayThreads] of codexByDay) {
    const daySpans = spans.filter(s => s.resident === 'Codex' && jstDay(s.start) === day);
    const aligned = dayThreads.length === daySpans.length;
    if (!aligned && day >= from && day <= to) quality.unmatchedCodexThreads += dayThreads.length;
    dayThreads.forEach((thread, i) => record('Codex', 'CLI', dateStart(day), thread.usage, aligned ? daySpans[i].work : undefined));
  }
  const coverage = [
    { resident: 'Claude', channel: 'GUI', status: quality.missingSources.includes('Claude 使用量') ? 'unmeasured' : 'measured' },
    { resident: 'Claude', channel: 'CLI', status: quality.missingSources.includes('Claude 使用量') ? 'unmeasured' : 'measured' },
    { resident: 'Codex', channel: 'CLI', status: quality.missingSources.includes('Codex 使用量') ? 'unmeasured' : 'measured' },
    { resident: 'Codex', channel: 'GUI', status: 'unmeasured' },
    { resident: 'Holo', channel: 'GUI', status: 'unmeasured' },
  ];
  for (const c of coverage) {
    if (c.status === 'measured') residentRow(c.resident, c.channel as Channel);
    else {
      const existing = residents.get(JSON.stringify([c.resident, c.channel]));
      if (existing) existing.usage = null;
      if (c.channel === 'CLI') for (const row of works.values()) if (row.resident === c.resident) row.usage = null;
    }
  }
  return { range: { from, to }, generatedAt: new Date(now).toISOString(), totals,
    residents: [...residents.values()], days: [...days.values()].sort((a, b) => a.day.localeCompare(b.day)),
    works: [...works.values()].sort((a, b) => (b.usage?.weighted ?? 0) - (a.usage?.weighted ?? 0)), quality, coverage };
}
