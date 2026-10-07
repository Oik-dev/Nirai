import type { IncomingMessage, ServerResponse } from 'node:http';
import { readFile } from 'node:fs/promises';
import { collectUsage, type MeterOptions } from './usage.ts';

export async function usageRequest(req: IncomingMessage, res: ServerResponse, options: MeterOptions, port: number) {
  const host = `127.0.0.1:${port}`;
  const origin = req.headers.origin;
  const site = req.headers['sec-fetch-site'];
  function reply(status: number, body: string, type = 'text/plain; charset=utf-8') {
    res.writeHead(status, {
      'Content-Type': type, 'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
      'Cross-Origin-Resource-Policy': 'same-origin',
      'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    }).end(body);
  }
  if (req.headers.host !== host) return reply(421, 'Misdirected Request');
  if ((origin && origin !== `http://${host}`) || (site && !['same-origin', 'none'].includes(String(site)))) return reply(403, 'Forbidden');
  if (req.method !== 'GET') return reply(405, 'GET only');
  const url = new URL(req.url ?? '/', `http://${host}`);
  if (url.pathname === '/usage' || url.pathname === '/usage/') {
    return reply(200, await readFile(new URL('./usage.html', import.meta.url), 'utf8'), 'text/html; charset=utf-8');
  }
  if (url.pathname !== '/usage/data') return reply(404, 'Not Found');
  // 外部からファイルの場所は指定させない。日付だけを受け取り、本文を含むエラーは返さない。
  const from = url.searchParams.get('from') || undefined;
  const to = url.searchParams.get('to') || undefined;
  try {
    const data = await collectUsage({ ...options, from, to });
    return reply(200, JSON.stringify(data), 'application/json; charset=utf-8');
  } catch {
    return reply(400, JSON.stringify({ message: '集計できませんでした。日付と記録の読み取り権限を確かめてください。' }), 'application/json; charset=utf-8');
  }
}
