import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { request as httpRequest } from 'node:http';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { append, readAll } from './letters.ts';

async function freePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('port');
  const port = address.port;
  await new Promise<void>(done => server.close(() => done()));
  return port;
}

test('本番と別の住人とポートで確認チケットの門、原文保持、二重送信、表示を確かめる', async t => {
  const root = mkdtempSync(join(tmpdir(), 'nirai-master-http-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const ts = new Date(Date.now() - 120_000).toISOString();
  append(root, 'Serina', { kind: 'letter', ts, id: 'A', from: 'Claude', to: 'Serina', work: 'ask-test', body: '確認' });
  append(root, 'Serina', { kind: 'note', ts: new Date(Date.now() - 110_000).toISOString(),
    letter: 'A', body: 'このまま進める？', waiting_for: 'Master' });
  const child = spawn(process.execPath, ['post/server.ts'], {
    cwd: fileURLToPath(new URL('../', import.meta.url)), windowsHide: true,
    env: { ...process.env, NIRAI_RESIDENTS: root, NIRAI_WORK: join(root, 'work'),
      NIRAI_RUNTIME_DIR: join(root, 'runtime'), NIRAI_PORT: String(port),
      NIRAI_SOURCE_REPO: fileURLToPath(new URL('../../', import.meta.url)), NIRAI_SWEEP_MS: '600000' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  t.after(() => { child.kill(); });
  let logs = '';
  child.stdout.on('data', d => { logs += d.toString(); });
  child.stderr.on('data', d => { logs += d.toString(); });
  let ready = false;
  for (let i = 0; i < 80; i++) {
    if (child.exitCode !== null) break;
    try { const response = await fetch(`${base}/master/asks`); if (response.ok) { ready = true; break; } }
    catch { /* 試験用のサーバーが起きるまで待つ */ }
    await new Promise(done => setTimeout(done, 50));
  }
  assert.ok(ready, logs);
  const wrongHost = await new Promise<number>((resolveStatus, reject) => {
    const req = httpRequest(`${base}/master/asks`, { headers: { Host: 'example.com' } },
      res => { res.resume(); res.on('end', () => resolveStatus(res.statusCode ?? 0)); });
    req.on('error', reject);
    req.end();
  });
  assert.equal(wrongHost, 421);
  assert.equal((await fetch(`${base}/master/asks`, { headers: { Origin: 'http://example.com' } })).status, 403);
  assert.equal((await fetch(`${base}/master/asks`, { headers: { 'Sec-Fetch-Site': 'same-origin' } })).status, 403);
  const { asks } = await (await fetch(`${base}/master/asks`)).json();
  assert.equal(asks.length, 1);
  assert.equal(asks[0].question, 'このまま進める？');
  const status = await (await fetch(`${base}/holo/status`)).json();
  assert.equal(status.masterAsks, 1, '席なしでも待ちがある');
  const answer = (body: string, asked = asks[0].asked) =>
    fetch(`${base}/master/answer`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ letter: 'A', asked, body }) });
  assert.equal((await answer('  \r\n  ')).status, 400, '空白だけは受け付けない');
  assert.equal((await answer('x'.repeat(12001))).status, 400);
  assert.equal((await answer('不正', 'old-version')).status, 409);
  const body = '  日本語\r\n\r\n 終わり  ';
  const response = await answer(body);
  assert.equal(response.status, 200);
  const { id } = await response.json();
  assert.ok(id.startsWith('L'));
  assert.equal((await answer(body)).status, 409, '二重送信しない');
  assert.equal((await (await fetch(`${base}/master/asks`)).json()).asks.length, 0);
  const replies = readAll(root, 'Serina').filter(l => l.kind === 'letter' && l.from === 'Master');
  assert.equal(replies.length, 1);
  assert.equal(replies[0].body, body, 'CRLFも前後空白も変えない');
  append(root, 'Serina', { kind: 'note', ts: new Date(Date.now() + 3000).toISOString(),
    letter: 'A', body: '別の質問', waiting_for: 'Master' });
  assert.equal((await (await fetch(`${base}/master/asks`)).json()).asks.length, 1);
  append(root, 'Serina', { kind: 'done', ts: new Date(Date.now() + 4000).toISOString(), letter: 'A' });
  assert.equal((await answer('遅れた返事')).status, 409);
});
