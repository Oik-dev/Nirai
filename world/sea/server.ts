import { createServer, request as httpRequest, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readIdeaAvatar, readIdeaMotion } from './body.ts';
import { mindState, seaResident, wakeMind } from './mind.ts';
import { SeaEvents } from './events.ts';
import { WorkshopDuty } from './workshop.ts';
import { WorkshopSchedule } from './workshop-schedule.ts';
import { SEA_HOST, SEA_PORT, MIND_HOST, seaSettings, type SeaSettings } from './settings.ts';
import { decodeRevision, readRevision, type Revision } from '../post/reload.ts';

export { SEA_HOST, SEA_PORT } from './settings.ts';

const worldRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const windowRoot = resolve(worldRoot, 'window');
const modulesRoot = resolve(worldRoot, 'node_modules');

const MIME = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.webp', 'image/webp'],
  ['.png', 'image/png'],
  ['.vrm', 'model/gltf-binary'],
]);

function headers(res: ServerResponse) {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Content-Security-Policy',
    "default-src 'self'; connect-src 'self' data: blob:; img-src 'self' data: blob:; "
      + "style-src 'self'; script-src 'self' 'unsafe-inline'; object-src 'none'; "
      + "frame-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'");
}

function reply(res: ServerResponse, status: number, body = '', contentType = 'text/plain; charset=utf-8') {
  headers(res);
  res.statusCode = status;
  res.setHeader('Content-Type', contentType);
  res.end(body);
}

function safePath(root: string, relative: string) {
  const clean = relative.replace(/^[/\\]+/, '');
  const path = resolve(root, clean);
  if (path !== root && !path.startsWith(root + sep)) throw new Error('path');
  return path;
}

// 窓が読むパッケージだけを配る（窓の import map と同じ並び）。
const WINDOW_MODULES = ['three', '@pixiv/three-vrm', '@pixiv/three-vrm-animation'];

function requestedFile(pathname: string) {
  if (pathname === '/') return safePath(windowRoot, 'index.html');
  if (pathname.startsWith('/node_modules/')) {
    const name = WINDOW_MODULES.find(module => pathname.startsWith(`/node_modules/${module}/`));
    if (!name) throw new Error('module');
    return safePath(resolve(modulesRoot, name), pathname.slice(`/node_modules/${name}/`.length));
  }
  return safePath(windowRoot, pathname);
}

async function serveFile(req: IncomingMessage, res: ServerResponse, path: string) {
  const info = await stat(path);
  if (!info.isFile()) throw new Error('not-file');
  const bytes = await readFile(path);
  headers(res);
  res.statusCode = 200;
  res.setHeader('Content-Type', MIME.get(extname(path).toLowerCase()) ?? 'application/octet-stream');
  res.setHeader('Content-Length', bytes.length);
  if (req.method === 'HEAD') res.end();
  else res.end(bytes);
}

function isMindRoute(method: string, pathname: string) {
  if (method === 'POST' && pathname === '/api/chat') return true;
  if (method === 'GET' && pathname === '/api/conversation') return true;
  if (method === 'DELETE' && pathname.startsWith('/api/conversation/')) return true;
  return false;
}

function proxyMind(req: IncomingMessage, res: ServerResponse, mindPort: number) {
  return new Promise<void>((resolvePromise, reject) => {
    let finished = false;
    const finish = (error?: Error) => {
      if (finished) return;
      finished = true;
      res.off('close', closed);
      res.off('finish', completed);
      if (error) reject(error);
      else resolvePromise();
    };
    const closed = () => { if (!res.writableEnded) upstream.destroy(); finish(); };
    const completed = () => finish();
    const upstream = httpRequest({
      host: MIND_HOST,
      port: mindPort,
      method: req.method,
      path: req.url,
      headers: {
        accept: req.headers.accept ?? '*/*',
        'content-type': req.headers['content-type'] ?? 'application/json',
      },
    }, upstreamRes => {
      headers(res);
      res.statusCode = upstreamRes.statusCode ?? 502;
      const contentType = upstreamRes.headers['content-type'];
      if (contentType) res.setHeader('Content-Type', contentType);
      upstreamRes.on('error', finish);
      upstreamRes.pipe(res);
    });
    res.on('close', closed);
    res.on('finish', completed);
    upstream.on('error', finish);
    req.on('error', finish);
    req.pipe(upstream);
  });
}

export function createSeaServer(settings: SeaSettings, revision?: Revision, workshop = new WorkshopDuty(),
  schedule?: WorkshopSchedule) {
  let relaying = 0;
  let chatting = 0;
  let mindOperation = false;
  let draining = false;
  const events = new SeaEvents(settings);
  let finishDrain: (() => void) | undefined;
  const server = createServer(async (req, res) => {
    try {
      if (draining) { reply(res, 503, '海を入れ替えています。'); return; }
      const host = req.headers.host ?? '';
      if (host !== SEA_HOST && !host.startsWith(`${SEA_HOST}:`)) {
        reply(res, 421, 'Misdirected Request');
        return;
      }
      const url = new URL(req.url ?? '/', `http://${SEA_HOST}`);
      let pathname;
      try { pathname = decodeURIComponent(url.pathname); }
      catch { reply(res, 400, 'Bad Request'); return; }
      if (pathname.includes('\0')) {
        reply(res, 400, 'Bad Request');
        return;
      }
      const method = req.method ?? 'GET';
      const mutating = method !== 'GET' && method !== 'HEAD';
      const fetchSite = req.headers['sec-fetch-site'];
      if (mutating && ((fetchSite && fetchSite !== 'same-origin')
          || (req.headers.origin && req.headers.origin !== `http://${host}`))) {
        reply(res, 403, 'Forbidden'); return;
      }
      if (pathname === '/sea/status' && method === 'GET') {
        reply(res, 200, JSON.stringify({ revision, relaying }), 'application/json; charset=utf-8'); return;
      }
      if (pathname === '/sea/body' && method === 'GET') {
        reply(res, 200, JSON.stringify(await events.snapshot()), 'application/json; charset=utf-8'); return;
      }
      const resident = await seaResident(settings);
      // 切断が住人の読込と重なった場合も、新しい中継を始めない。
      if (draining) { reply(res, 503, '海を入れ替えています。'); return; }
      if (pathname === '/sea/mind' && method === 'GET') {
        reply(res, 200, JSON.stringify({ resident: resident?.name ?? null, mind: resident ? await mindState(resident) : 'down' }), 'application/json; charset=utf-8'); return;
      }
      if (method === 'POST' && pathname === '/sea/mind/wake') {
        if (!resident) { reply(res, 404, '海に住人がいません。'); return; }
        if (relaying !== 0) { reply(res, 409, '返答や起動の途中です。'); return; }
        relaying++;
        mindOperation = true;
        try {
          await wakeMind(settings, resident);
          reply(res, 200, '{"mind":"up"}', 'application/json; charset=utf-8');
        } catch (error) {
          reply(res, 502, error instanceof Error ? error.message : '精神を起こせませんでした。');
        } finally { mindOperation = false; relaying--; finishDrain?.(); }
        return;
      }
      if (pathname.startsWith('/api/')) {
        if (pathname === '/api/events' && method === 'GET') {
          if (!resident) { reply(res, 404, '海に住人がいません。'); return; }
          headers(res);
          events.subscribe(res);
          return;
        }
        if (!isMindRoute(method, pathname)) {
          reply(res, 404, 'Not Found');
          return;
        }
        if (!resident) { reply(res, 404, '海に住人がいません。'); return; }
        if (mindOperation) { reply(res, 409, '精神を起こしています。'); return; }
        const isChat = pathname === '/api/chat';
        const counted = isChat || method === 'DELETE';
        if (counted) relaying++;
        if (isChat) chatting++;
        try {
          // Masterへの返事より先に、生成器とChromeの終了まで待つ。
          if (isChat) {
            if (schedule) await schedule.pause();
            else await workshop.stop();
            if (draining) { reply(res, 503, '海を入れ替えています。'); return; }
          }
          await proxyMind(req, res, resident.port);
        }
        finally {
          if (isChat) {
            chatting--;
            // 会話が重なっても、最後の返答を届けるまで工房を再開しない。
            if (chatting === 0 && !draining) schedule?.resume();
          }
          if (counted) relaying--;
          finishDrain?.();
        }
        return;
      }
      if (method !== 'GET' && method !== 'HEAD') {
        reply(res, 405, 'Method Not Allowed');
        return;
      }
      // 体と覚えた動きは、住人のイデアの body/ から読む（名前の検査と、イデアの外を指していないかは入口で見る）。
      const motion = /^\/motions\/([^/]+)\.vrma$/.exec(pathname);
      if (pathname === '/avatar.vrm' || motion) {
        if (!resident) { reply(res, 404, '海に住人がいません。'); return; }
        const file = motion
          ? await readIdeaMotion(resident.idea, motion[1])
          : await readIdeaAvatar(resident.idea);
        headers(res);
        res.statusCode = 200;
        res.setHeader('Content-Type', 'model/gltf-binary');
        res.setHeader('Content-Length', file.bytes.length);
        if (req.method === 'HEAD') res.end();
        else res.end(file.bytes);
        return;
      }
      await serveFile(req, res, requestedFile(pathname));
    } catch (error) {
      // 精神の応答・会話の本文は海の記録に出さない。
      if (!res.headersSent) {
        const status = (req.url ?? '').startsWith('/api/') ? 502 : 404;
        reply(res, status, status === 502 ? 'Bad Gateway' : 'Not Found');
      }
      else res.destroy();
    }
  });
  server.once('listening', () => { events.start(); schedule?.start(); });
  server.once('close', () => { events.stop(); void schedule?.stop(); });
  let drainPromise: Promise<void> | undefined;
  return Object.assign(server, {
    drain(timeoutMs = 180_000): Promise<void> {
      if (drainPromise) return drainPromise;
      draining = true;
      drainPromise = (schedule ? schedule.stop() : workshop.stop()).then(() => new Promise<void>(resolveDrain => {
        let finished = false;
        const finish = () => {
          if (finished) return;
          finished = true;
          clearTimeout(timer);
          events.stop();
          server.closeAllConnections();
          resolveDrain();
        };
        const timer = setTimeout(finish, timeoutMs);
        finishDrain = () => { if (relaying === 0) finish(); };
        server.close(() => finish());
        server.closeIdleConnections();
        finishDrain();
      }));
      return drainPromise;
    },
  });
}

export async function startSeaServer({
  settings = seaSettings(),
  host = SEA_HOST,
  port = settings.port,
  revision,
  workshop,
  schedule,
}: { settings?: SeaSettings; host?: string; port?: number; revision?: Revision;
  workshop?: WorkshopDuty; schedule?: WorkshopSchedule } = {}) {
  if (host !== SEA_HOST) throw new Error('海のサーバーは127.0.0.1だけで起動できます。');
  const server = createSeaServer(settings, revision, workshop, schedule);
  await new Promise<void>((resolvePromise, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => {
      server.off('error', reject);
      resolvePromise();
    });
  });
  return server;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const settings = seaSettings();
  const supplied = decodeRevision(process.env.NIRAI_RUNNING_REVISION);
  const revision = await readRevision(settings.sourceRepo, supplied?.head ?? 'HEAD');
  startSeaServer({ settings, revision }).then(server => {
    process.once('disconnect', () => { void server.drain().then(() => process.exit(0)); });
    const address = server.address();
    const port = typeof address === 'object' && address ? address.port : SEA_PORT;
    console.log(`Nirai sea: http://${SEA_HOST}:${port}/`);
  }).catch(error => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  });
}
