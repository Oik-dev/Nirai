// D0: put .vrma motions on Yumeka in headless Chrome, sample every frame (gate input) and draw a contact sheet.
// usage: node look.mjs <outDir> <name.vrma>...   (files are read from ./vrma)
import { createServer } from 'node:http';
import { readFile, writeFile, mkdir, mkdtemp, rm } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { join, extname, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

const here = dirname(fileURLToPath(import.meta.url));
const modules = join(here, '..', 'node_modules');
const model = 'D:/Products/Model Converter/output/Yumeka_v1.0.4-appearance.vrm';
const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const [outDir, ...clips] = process.argv.slice(2);
await mkdir(outDir, { recursive: true });
const types = { '.js': 'text/javascript', '.mjs': 'text/javascript', '.html': 'text/html', '.json': 'application/json' };

let finish;
const finished = new Promise(resolve => { finish = resolve; });
const server = createServer(async (request, response) => {
  const url = new URL(request.url, 'http://x');
  try {
    if (request.method === 'POST') {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks);
      if (url.pathname === '/done') { response.end('ok'); finish(body.toString()); return; }
      if (url.pathname === '/log') { console.log(body.toString()); response.end('ok'); return; }
      const name = decodeURIComponent(url.pathname.slice('/save/'.length)).replace(/[^\p{L}\p{N}_.-]/gu, '');
      await writeFile(join(outDir, name), name.endsWith('.png') ? Buffer.from(body.toString(), 'base64') : body);
      response.end('ok');
      return;
    }
    let file;
    if (url.pathname === '/') { response.setHeader('content-type', 'text/html'); response.end(await readFile(join(here, 'look.html'))); return; }
    if (url.pathname === '/clips.json') { response.end(JSON.stringify(clips)); return; }
    if (url.pathname === '/model.vrm') file = model;
    else if (url.pathname.startsWith('/node_modules/')) file = join(modules, decodeURIComponent(url.pathname.slice('/node_modules/'.length)));
    else if (url.pathname.startsWith('/vrma/')) file = join(here, '..', 'window', 'assets', 'motions', decodeURIComponent(url.pathname.slice('/vrma/'.length)));
    else if (url.pathname.startsWith('/body/') || url.pathname.startsWith('/sea/')) file = join(here, '..', 'window', decodeURIComponent(url.pathname.slice(1)));
    else if (url.pathname.startsWith('/check/')) file = join(here, decodeURIComponent(url.pathname.slice('/check/'.length)));
    else { response.statusCode = 404; response.end(); return; }
    response.setHeader('content-type', types[extname(file)] ?? 'application/octet-stream');
    response.end(await readFile(file));
  } catch (error) {
    response.statusCode = 500; response.end(String(error));
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
const profile = await mkdtemp(join(tmpdir(), 'nirai-d0-'));
const browser = spawn(chrome, ['--headless=new', '--no-first-run', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  `--user-data-dir=${profile}`, '--proxy-server=http://127.0.0.1:9', '--window-size=1400,900',
  `http://127.0.0.1:${port}/`], { windowsHide: true, stdio: 'ignore' });
const timer = setTimeout(() => finish('timeout'), 10 * 60 * 1000);
console.log('result', await finished);
clearTimeout(timer);
browser.kill();
server.close();
await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
