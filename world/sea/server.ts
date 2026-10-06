import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readIdeaAvatar } from './body.ts';

export const SEA_HOST = '127.0.0.1';
export const SEA_PORT = 47810;

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

function requestedFile(pathname: string) {
  if (pathname === '/') return safePath(windowRoot, 'index.html');
  if (pathname.startsWith('/node_modules/three/')) {
    return safePath(resolve(modulesRoot, 'three'), pathname.slice('/node_modules/three/'.length));
  }
  if (pathname.startsWith('/node_modules/@pixiv/three-vrm/')) {
    return safePath(resolve(modulesRoot, '@pixiv', 'three-vrm'),
      pathname.slice('/node_modules/@pixiv/three-vrm/'.length));
  }
  if (pathname.startsWith('/node_modules/')) throw new Error('module');
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

export function createSeaServer({ ideaRoot }: { ideaRoot: string }) {
  if (!ideaRoot) throw new Error('NIRAI_IDEAが指定されていません。');
  return createServer(async (req, res) => {
    try {
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        reply(res, 405, 'Method Not Allowed');
        return;
      }
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
      if (pathname === '/health') {
        reply(res, 200, '{"ok":true}', 'application/json; charset=utf-8');
        return;
      }
      if (pathname === '/avatar.vrm') {
        const avatar = await readIdeaAvatar(ideaRoot);
        headers(res);
        res.statusCode = 200;
        res.setHeader('Content-Type', 'model/gltf-binary');
        res.setHeader('Content-Length', avatar.bytes.length);
        if (req.method === 'HEAD') res.end();
        else res.end(avatar.bytes);
        return;
      }
      await serveFile(req, res, requestedFile(pathname));
    } catch (error) {
      if (process.env.NIRAI_SEA_DEBUG === '1') console.error(error);
      if (!res.headersSent) reply(res, 404, 'Not Found');
      else res.destroy();
    }
  });
}

export async function startSeaServer({
  ideaRoot = process.env.NIRAI_IDEA ?? '',
  host = SEA_HOST,
  port = SEA_PORT,
}: { ideaRoot?: string; host?: string; port?: number } = {}) {
  if (host !== SEA_HOST) throw new Error('海のサーバーは127.0.0.1だけで起動できます。');
  const server = createSeaServer({ ideaRoot });
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
  startSeaServer().then(server => {
    const address = server.address();
    const port = typeof address === 'object' && address ? address.port : SEA_PORT;
    console.log(`Nirai sea: http://${SEA_HOST}:${port}/`);
  }).catch(error => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  });
}
