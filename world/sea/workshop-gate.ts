// 住人の体に候補の動きを載せ、窓と同じbodyで測る関門。
// イデアへ候補を置くのは、この関門が通ったあとだけ。URL/子の引数に願いの名前を渡さない。
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { checkMotion, type GateInput } from './gate.ts';

const worldRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const windowRoot = join(worldRoot, 'window');
const modulesRoot = join(worldRoot, 'node_modules');
const chromeDefault = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const mime: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.vrm': 'model/gltf-binary',
  '.vrma': 'model/gltf-binary',
};

type Job = {
  id: number;
  bytes: Buffer;
  resolve: (pass: boolean) => void;
  reject: (reason: Error) => void;
  timer: ReturnType<typeof setTimeout>;
  signal?: AbortSignal;
  aborted: () => void;
};
export type WorkshopGate = {
  check: (candidate: Buffer, signal?: AbortSignal) => Promise<boolean>;
  close: () => Promise<void>;
};

async function readLimited(req: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let length = 0;
  for await (const chunk of req) {
    length += chunk.length;
    if (length > 24 * 1024 * 1024) throw new Error('too-large');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

// 許されたbody・sea・3つのモジュールだけ。同じ「窓の版」を見る。
function asset(pathname: string): string | null {
  let root: string;
  let relative: string;
  if (pathname.startsWith('/body/') || pathname.startsWith('/sea/')) {
    root = windowRoot;
    relative = pathname.slice(1);
  } else if (pathname.startsWith('/node_modules/')) {
    const modules = ['three', '@pixiv/three-vrm', '@pixiv/three-vrm-animation'];
    const name = modules.find(value => pathname.startsWith('/node_modules/' + value + '/'));
    if (!name) return null;
    root = join(modulesRoot, name);
    relative = pathname.slice(('/node_modules/' + name + '/').length);
  } else return null;
  const path = resolve(root, relative);
  return path.startsWith(root + sep) ? path : null;
}

export async function openWorkshopGate(
  avatar: Buffer,
  options: {
    chrome?: string;
    startupMs?: number;
    checkMs?: number;
    signal?: AbortSignal;
    // テストではブラウザーの代わりにHTTP経路をたどる偽の住人を使う。
    launch?: (url: string, profile: string) => ChildProcess;
  } = {},
): Promise<WorkshopGate> {
  if (options.signal?.aborted) throw new Error('関門を中断しました。');
  let job: Job | undefined;
  let nextId = 0;
  let stopped = false;
  let readyResolve!: () => void;
  let readyReject!: (reason: Error) => void;
  const ready = new Promise<void>((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  // ブラウザーの初期化に失敗し呼び出し元がもう待っていなくても、未処理例外にしない。
  void ready.catch(() => {});
  const settle = (id: number, pass?: boolean, error?: Error) => {
    if (!job || job.id !== id) return;
    const current = job;
    job = undefined;
    clearTimeout(current.timer);
    current.signal?.removeEventListener('abort', current.aborted);
    if (error) current.reject(error);
    else current.resolve(pass === true);
  };
  const server = createServer((req, res) => {
    void (async () => {
      const address = new URL(req.url ?? '/', 'http://127.0.0.1');
      const pathname = decodeURIComponent(address.pathname);
      const reply = (status: number, body?: Buffer | string, type?: string) => {
        res.writeHead(status, { 'content-type': type ?? 'text/plain', 'cache-control': 'no-store' });
        res.end(body);
      };
      if (stopped || !/^127\.0\.0\.1(?::\d+)?$/.test(req.headers.host ?? '')) {
        reply(403); return;
      }
      if (req.method === 'POST' && pathname === '/ready') { readyResolve(); reply(200); return; }
      if (req.method === 'POST' && pathname === '/setup-failed') {
        readyReject(new Error('関門のページを起動できませんでした。'));
        reply(200); return;
      }
      if (req.method === 'GET' && pathname === '/task') {
        if (!job) { reply(204); return; }
        reply(200, JSON.stringify({ id: job.id }), 'application/json'); return;
      }
      if (req.method === 'GET' && pathname === '/avatar.vrm') {
        reply(200, avatar, 'model/gltf-binary'); return;
      }
      if (req.method === 'GET' && pathname === '/motion.vrma') {
        if (!job) { reply(404); return; }
        reply(200, job.bytes, 'model/gltf-binary'); return;
      }
      const resultId = /^\/result\/(\d+)$/.exec(pathname);
      if (req.method === 'POST' && resultId) {
        const id = Number(resultId[1]);
        if (!job || job.id !== id) { reply(409); return; }
        const bytes = await readLimited(req);
        let pass = false;
        try {
          const sample = JSON.parse(bytes.toString('utf8')) as GateInput | null;
          if (sample !== null) pass = checkMotion(sample).pass;
        } catch { /* 壊れた候補は決して通さない */ }
        settle(id, pass);
        reply(200);
        return;
      }
      if (req.method === 'GET' && pathname === '/') {
        reply(200, await readFile(new URL('./workshop-gate.html', import.meta.url)), 'text/html; charset=utf-8');
        return;
      }
      const path = req.method === 'GET' ? asset(pathname) : null;
      if (!path) { reply(404); return; }
      reply(200, await readFile(path), mime[extname(path)] ?? 'application/octet-stream');
    })().catch(() => {
      if (!res.headersSent) { res.writeHead(404); res.end(); }
      else res.destroy();
    });
  });
  await new Promise<void>((resolveListen, rejectListen) => {
    server.once('error', rejectListen);
    server.listen(0, '127.0.0.1', resolveListen);
  });
  const port = (server.address() as { port: number }).port;
  const profile = await mkdtemp(join(tmpdir(), 'nirai-gate-'));
  const url = 'http://127.0.0.1:' + port + '/';
  // Chrome DevToolsの親子パイプを開いたままにする。親が死んでパイプが閉じれば
  // Chromeが終了する形を使う。関門が終わったら明示的にもkillして待つ。
  const browser = options.launch
    ? options.launch(url, profile)
    : spawn(options.chrome ?? chromeDefault, [
      '--headless=new', '--no-first-run', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
      '--remote-debugging-pipe', '--proxy-server=http://127.0.0.1:9',
      '--user-data-dir=' + profile, url,
    ], { windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });
  let browserExited = false;
  let browserExitResolve!: () => void;
  const browserExit = new Promise<void>(resolve => { browserExitResolve = resolve; });
  const markBrowserExit = () => {
    browserExited = true;
    browserExitResolve();
  };
  browser.once('exit', markBrowserExit);
  // spawn自体に失敗した子にはexitが届かないため、errorも終了として扱う。
  browser.once('error', markBrowserExit);
  browser.once('error', () => readyReject(new Error('関門のChromeを起動できませんでした。')));
  browser.once('exit', () => {
    if (!stopped) readyReject(new Error('関門のChromeが終了しました。'));
    if (job) settle(job.id, undefined, new Error('関門のChromeが終了しました。'));
  });
  const startupTimer = setTimeout(() => readyReject(new Error('関門のChrome準備が時間切れです。')), options.startupMs ?? 60_000);
  const startupAborted = () => readyReject(new Error('関門を中断しました。'));
  options.signal?.addEventListener('abort', startupAborted, { once: true });
  const gate: WorkshopGate = {
    check(candidate, signal) {
      if (stopped || job || signal?.aborted) return Promise.reject(new Error('関門を開始できません。'));
      const id = ++nextId;
      return new Promise<boolean>((resolve, reject) => {
        const aborted = () => settle(id, undefined, new Error('関門を中断しました。'));
        const timer = setTimeout(aborted, options.checkMs ?? 90_000);
        job = { id, bytes: candidate, resolve, reject, timer, signal, aborted };
        signal?.addEventListener('abort', aborted, { once: true });
      });
    },
    async close() {
      if (stopped) return;
      stopped = true;
      if (job) settle(job.id, undefined, new Error('関門を終了しました。'));
      server.closeAllConnections();
      await new Promise<void>(resolveClose => server.close(() => resolveClose()));
      if (!browserExited) {
        browser.kill();
        let timer!: ReturnType<typeof setTimeout>;
        try {
          await Promise.race([
            browserExit,
            new Promise<never>((_, reject) => {
              timer = setTimeout(() => reject(new Error('関門のChrome終了を確認できませんでした。')), 10_000);
            }),
          ]);
        } finally { clearTimeout(timer); }
      }
      // Chromeの終了確認前にprofileを消して成功扱いしない。
      await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    },
  };
  try {
    await ready;
    return gate;
  } catch (error) {
    await gate.close();
    throw error;
  } finally {
    clearTimeout(startupTimer);
    options.signal?.removeEventListener('abort', startupAborted);
  }
}
