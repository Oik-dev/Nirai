import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, request } from 'node:http';
import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { usageRequest } from './usage-http.ts';
import { meterDefaults } from './usage.ts';

test('メーターは手元のページだけに読み取りを許し、元の文章やファイル指定を返さない', async t => {
  const scratch = resolve(tmpdir());
  const root = await mkdtemp(join(scratch, '.tmp-usage-http-'));
  const residentsRoot = join(root, 'residents'), claudeProject = join(root, 'claude');
  for (const name of ['Claude', 'Codex', 'Holo']) await mkdir(join(residentsRoot, name, 'lifelog', 'post'), { recursive: true });
  await mkdir(join(residentsRoot, 'Codex', 'lifelog', 'codex-cli'));
  await mkdir(claudeProject);
  let port = 0;
  const server = createServer((req, res) => {
    void usageRequest(req, res, { ...meterDefaults(residentsRoot, root), claudeProject, now: new Date('2026-10-07T12:00:00+09:00') }, port)
      .catch(() => { res.writeHead(500).end('error'); });
  });
  t.after(async () => {
    server.closeAllConnections();
    await new Promise<void>(done => server.close(() => done()));
    assert.ok(resolve(root).startsWith(scratch + sep));
    await rm(root, { recursive: true, force: true });
  });
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done));
  port = (server.address() as { port: number }).port;
  const url = `http://127.0.0.1:${port}`;
  const page = await fetch(`${url}/usage`);
  assert.equal(page.status, 200);
  assert.match(page.headers.get('content-type')!, /text\/html/);
  assert.match(page.headers.get('content-security-policy')!, /frame-ancestors 'none'/);
  assert.equal(page.headers.get('cache-control'), 'no-store');
  assert.match(await page.text(), /使用量/);

  const data = await fetch(`${url}/usage/data?from=2026-10-07&to=2026-10-07&file=PRIVATE_SENTINEL`);
  assert.equal(data.status, 200);
  const body = await data.text();
  assert.ok(!body.includes('PRIVATE_SENTINEL'));
  assert.deepEqual(JSON.parse(body).range, { from: '2026-10-07', to: '2026-10-07' });
  assert.equal(JSON.parse(body).totals.weighted, 0);

  assert.equal((await fetch(`${url}/usage/data`, { headers: { origin: 'https://outside.example' } })).status, 403);
  assert.equal((await fetch(`${url}/usage/data`, { headers: { 'sec-fetch-site': 'cross-site' } })).status, 403);
  // fetchはHostを宛先から作り直すため、この境界だけはHTTPの生のヘッダーを送る。
  const wrongHost = await new Promise<number>((done, reject) => {
    const req = request(`${url}/usage/data`, { headers: { host: `outside.example:${port}` } }, res => {
      res.resume();
      res.on('end', () => done(res.statusCode!));
    });
    req.on('error', reject);
    req.end();
  });
  assert.equal(wrongHost, 421);
  assert.equal((await fetch(`${url}/usage/data`, { method: 'POST' })).status, 405);
  assert.equal((await fetch(`${url}/usage/unknown`)).status, 404);
  const invalid = await fetch(`${url}/usage/data?from=2026-99-99&to=2026-10-07`);
  assert.equal(invalid.status, 400);
  const error = await invalid.text();
  assert.ok(!error.includes(root));
  assert.match(error, /集計できませんでした/);

  const cli = execFileSync(process.execPath, [fileURLToPath(new URL('./usage-cli.ts', import.meta.url)), '--from', '2026-10-07', '--to', '2026-10-07'], {
    encoding: 'utf8', windowsHide: true, env: { ...process.env, NIRAI_RESIDENTS: residentsRoot, NIRAI_CLAUDE_PROJECT: claudeProject },
  });
  assert.deepEqual(JSON.parse(cli).range, { from: '2026-10-07', to: '2026-10-07' });
  assert.equal(JSON.parse(cli).totals.weighted, 0);
});
