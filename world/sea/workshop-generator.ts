// D1の生成器は別プロセス。本人の願いはHTTPの本文だけで渡す。
// 引数・プロセス出力・例外には文や名前を載せず、親が消えたらstdinのEOFで子も消える。
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';
import { dirname } from 'node:path';
import type { WorkshopDescription, WorkshopGenerate } from './workshop-flow.ts';

export type WorkshopGenerator = {
  generate(description: WorkshopDescription, seed: number, signal: AbortSignal): Promise<WorkshopGenerate>;
  close(): Promise<void>;
};

type GeneratorOptions = {
  python: string;
  script: string;
  waitCapacitySeconds: number;
  startupMs?: number;
  motionMs?: number;
  launch?: (executable: string, args: string[]) => ChildProcess;
};

async function unusedPort(): Promise<number> {
  const server = createServer();
  try {
    return await new Promise<number>((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => {
        const address = server.address();
        if (!address || typeof address === 'string') reject(new Error('port'));
        else resolve(address.port);
      });
    });
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
}

function validMotion(bytes: Buffer): boolean {
  return bytes.length >= 12 && bytes.toString('ascii', 0, 4) === 'glTF'
    && bytes.readUInt32LE(4) === 2 && bytes.readUInt32LE(8) === bytes.length;
}

// health 503は、脳の退避とローカルモデル読み込み中の正常な待機状態。
export async function openWorkshopGenerator(options: GeneratorOptions, signal: AbortSignal): Promise<WorkshopGenerator> {
  if (signal.aborted) throw new Error('generator unavailable');
  const port = await unusedPort();
  if (signal.aborted) throw new Error('generator unavailable');
  const args = [options.script, '--port', String(port), '--wait-capacity-seconds',
    String(options.waitCapacitySeconds), '--nirai-workshop'];
  const child = options.launch?.(options.python, args) ?? spawn(options.python, args, {
    cwd: dirname(options.script), windowsHide: true, stdio: ['pipe', 'ignore', 'ignore'],
  });
  // 子が準備中に終了した場合のEPIPEは正常な終了経路。未処理例外にしない。
  child.stdin?.on('error', () => {});
  let exited = false;
  let finishExit!: () => void;
  const exit = new Promise<void>(resolve => { finishExit = resolve; });
  const requests = new AbortController();
  child.once('exit', () => { exited = true; requests.abort(); finishExit(); });
  child.once('error', () => { exited = true; requests.abort(); finishExit(); });
  let closing: Promise<void> | undefined;
  const close = () => {
    if (closing) return closing;
    requests.abort();
    child.stdin?.end();
    closing = (async () => {
      if (exited) return;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          exit,
          new Promise<void>(resolve => { timer = setTimeout(resolve, 2000); }),
        ]);
      } finally { if (timer) clearTimeout(timer); }
      if (!exited) {
        child.kill();
        await exit;
      }
    })();
    return closing;
  };
  const onAbort = () => { void close(); };
  signal.addEventListener('abort', onAbort, { once: true });
  const deadline = Date.now() + (options.startupMs ?? 600_000);
  const base = `http://127.0.0.1:${port}`;
  try {
    while (!signal.aborted && !exited && Date.now() < deadline) {
      try {
        const response = await fetch(`${base}/health`, { signal: AbortSignal.any([
          signal, requests.signal, AbortSignal.timeout(1500),
        ]) });
        await response.body?.cancel();
        if (response.ok && !signal.aborted && !exited) {
          const generate: WorkshopGenerator['generate'] = async (description, seed, generationSignal) => {
            if (signal.aborted || generationSignal.aborted || requests.signal.aborted || exited) {
              return { kind: 'unavailable' };
            }
            try {
              const response = await fetch(`${base}/motion`, {
                method: 'POST', headers: { 'content-type': 'application/json' },
                body: JSON.stringify({ text: description.text, seconds: description.seconds, seed }),
                signal: AbortSignal.any([
                  signal, generationSignal, requests.signal, AbortSignal.timeout(options.motionMs ?? 120_000),
                ]),
              });
              if (response.status === 500) { await response.body?.cancel(); return { kind: 'rejected' }; }
              if (!response.ok) { await response.body?.cancel(); return { kind: 'unavailable' }; }
              const length = Number(response.headers.get('content-length'));
              if (Number.isFinite(length) && length > 24 * 1024 * 1024) {
                await response.body?.cancel(); return { kind: 'rejected' };
              }
              if (!response.body) return { kind: 'rejected' };
              const chunks: Buffer[] = [];
              let total = 0;
              for await (const chunk of response.body) {
                total += chunk.length;
                if (total > 24 * 1024 * 1024) {
                  return { kind: 'rejected' };
                }
                chunks.push(Buffer.from(chunk));
              }
              const bytes = Buffer.concat(chunks);
              if (!validMotion(bytes)) return { kind: 'rejected' };
              return { kind: 'candidate', bytes };
            } catch { return { kind: 'unavailable' }; }
          };
          return { generate, close: async () => { signal.removeEventListener('abort', onAbort); await close(); } };
        }
      } catch { /* 準備中、または子の終了。文を例外に含めない。 */ }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  } catch { /* 失敗理由に願いを含めない。 */ }
  signal.removeEventListener('abort', onAbort);
  await close();
  throw new Error('generator unavailable');
}
