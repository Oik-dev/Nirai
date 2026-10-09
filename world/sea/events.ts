// 精神からの知らせを海が1本で受ける。本人の体の選択は記録してから、眠りと目覚めはそのまま、暮らしが変わったと窓へ知らせる。
import { request, type ClientRequest, type IncomingMessage, type ServerResponse } from 'node:http';
import { readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { appendBodyApproach, appendBodyChoice, bodyRecordsNewestFirst, pendingBodyWishes, readBodyCatalog, type BodyCatalog } from './body.ts';
import { lifeOf } from './life.ts';
import { seaResident, type Resident } from './mind.ts';
import { MIND_HOST, type SeaSettings } from './settings.ts';

const EMPTY: BodyCatalog = { expressions: [], gestures: [] };

// ファイルが替わったときだけ大きなVRMを読み直す。表情の記録はここへ混ぜない。
async function bodyRevision(idea: string) {
  const body = join(idea, 'body');
  const names = ['avatar.vrm'];
  const motions = await readdir(join(body, 'motions')).catch(() => [] as string[]);
  names.push(...motions.filter(name => name.endsWith('.vrma')).sort().map(name => join('motions', name)));
  const files = await Promise.all(names.map(async name => {
    const info = await stat(join(body, name), { bigint: true }).catch(() => undefined);
    return [name, info?.size.toString(), info?.mtimeNs.toString(), info?.ctimeNs.toString()];
  }));
  return createHash('sha256').update(JSON.stringify(files)).digest('hex');
}

export class SeaEvents {
  private settings: SeaSettings;
  private resident?: Resident;
  private catalog: BodyCatalog = EMPTY;
  private revision = '';
  private clients = new Set<ServerResponse>();
  private upstream?: ClientRequest;
  private incoming?: IncomingMessage;
  private poll?: ReturnType<typeof setInterval>;
  private retry?: ReturnType<typeof setTimeout>;
  private stopped = false;
  private connected = false;
  private mindAsleep = false; // 精神は流れにつないだ最初に今の値を送るので、つなぎ直せば正しくなる。
  private queue: Promise<unknown> = Promise.resolve();
  private perceiving?: AbortController;

  constructor(settings: SeaSettings) { this.settings = settings; }

  start() {
    this.poll = setInterval(() => { void this.enqueue(() => this.refresh()); }, 1000);
    this.poll.unref();
    void this.enqueue(() => this.refresh());
  }

  private enqueue<T>(run: () => Promise<T>): Promise<T> {
    const next = this.queue.then(run);
    // 応答の本文もエラーも海のログへ出さない。次の知らせは引き続き受ける。
    this.queue = next.catch(() => undefined);
    return next;
  }

  private broadcast(event: unknown) {
    const line = `data: ${JSON.stringify(event)}\n\n`;
    for (const client of this.clients) {
      if (!client.write(line)) client.end(); // 遅い窓は、つなぎ直して記録から読み直す。
    }
  }

  private endClients() {
    for (const client of this.clients) client.end();
    this.clients.clear();
  }

  subscribe(res: ServerResponse) {
    res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
    res.write(': connected\n\n');
    this.clients.add(res);
    res.once('close', () => this.clients.delete(res));
  }

  async snapshot() {
    return this.enqueue(async () => {
      await this.refresh();
      return {
        catalog: this.catalog,
        // 精神の流れにつながっていなければ、精神は動いていない（海の底で眠っている）。
        life: this.resident ? await lifeOf(bodyRecordsNewestFirst(this.resident.idea), !this.connected || this.mindAsleep, this.catalog) : null,
        revision: this.revision,
      };
    });
  }

  // 工房も、窓と同じ海の住人・精神接続・記録を見る。別の正本を持たない。
  // 願いは本人のイデアにだけ記録される。海のログやHTTP応答へ出さない。
  async workshopContext() {
    return this.enqueue(async () => {
      await this.refresh();
      const resident = this.resident;
      const connected = this.connected;
      const mindAsleep = this.mindAsleep;
      return {
        resident,
        connected,
        mindAsleep,
        wishes: resident && connected && !mindAsleep
          ? await pendingBodyWishes(resident.idea)
          : [],
      };
    });
  }

  private async refresh() {
    if (this.stopped) return;
    const resident = await seaResident(this.settings);
    if (this.stopped) return;
    const changedResident = resident?.idea !== this.resident?.idea || resident?.port !== this.resident?.port;
    if (changedResident) {
      this.disconnect();
      this.resident = resident;
      this.revision = '';
      this.catalog = EMPTY;
      this.endClients();
    }
    if (!resident) return;
    const revision = await bodyRevision(resident.idea);
    const changedBody = revision !== this.revision;
    if (changedBody) {
      // 壊れた体の間は、その体の選択を受け付けない。
      const catalog = await readBodyCatalog(resident.idea).catch(() => undefined);
      this.catalog = catalog ?? EMPTY;
      if (catalog) {
        // 一時的な読込失敗は版を確定せず、次の見回りで同じ体を読み直す。
        this.revision = revision;
        if (this.connected) await this.perceive();
        this.broadcast({ type: 'catalog' });
      }
    }
    if (!this.upstream && !this.retry) this.connect();
  }

  private async perceive() {
    const resident = this.resident;
    if (!resident || this.stopped) return;
    const controller = new AbortController();
    this.perceiving = controller;
    const timeout = setTimeout(() => controller.abort(), 1000);
    try {
      const response = await fetch(`http://${MIND_HOST}:${resident.port}/api/perceive`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ kind: 'body', catalog: this.catalog }), signal: controller.signal,
      });
      await response.body?.cancel();
      if (!response.ok) throw new Error('perceive');
    } catch {
      // 流れとカタログは一組。知覚が届かなければ、つなぎ直して両方を送り直す。
      this.disconnect();
      this.endClients();
    } finally {
      clearTimeout(timeout);
      if (this.perceiving === controller) this.perceiving = undefined;
    }
  }

  private connect() {
    const resident = this.resident;
    if (!resident || this.stopped) return;
    const upstream = request({ host: MIND_HOST, port: resident.port, path: '/api/events',
      headers: { accept: 'text/event-stream' } });
    this.upstream = upstream;
    const ended = () => {
      if (this.upstream !== upstream) return;
      this.upstream = undefined;
      this.incoming = undefined;
      this.connected = false;
      this.mindAsleep = false;
      upstream.destroy();
      this.endClients();
      if (!this.stopped) {
        this.retry = setTimeout(() => {
          this.retry = undefined;
          void this.enqueue(() => this.refresh());
        }, 500);
        this.retry.unref();
      }
    };
    upstream.once('error', ended);
    upstream.once('response', incoming => {
      if (this.stopped || this.upstream !== upstream) { incoming.destroy(); return; }
      this.incoming = incoming;
      if (incoming.statusCode !== 200 || !incoming.headers['content-type']?.startsWith('text/event-stream')) {
        incoming.destroy(); ended(); return;
      }
      this.connected = true;
      void this.enqueue(async () => { await this.refresh(); await this.perceive(); });
      let buffer = '';
      incoming.setEncoding('utf8');
      incoming.on('data', (chunk: string) => {
        buffer += chunk;
        // SSEは行末がCRLFでもLFでもよい。JSONの本文はdataの行だけから読む。
        let match;
        while ((match = /\r?\n\r?\n/.exec(buffer))) {
          const block = buffer.slice(0, match.index);
          buffer = buffer.slice(match.index + match[0].length);
          const data = block.split(/\r?\n/).filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).replace(/^ /, '')).join('\n');
          if (data) void this.enqueue(() => this.receive(data, resident));
        }
        if (buffer.length > 1024 * 1024) incoming.destroy();
      });
      incoming.once('end', ended);
      incoming.once('close', ended);
      incoming.once('error', ended);
    });
    upstream.end();
  }

  private async receive(data: string, from: Resident) {
    if (this.stopped || from.idea !== this.resident?.idea || from.port !== this.resident?.port) return;
    let event;
    try { event = JSON.parse(data); } catch { return; }
    if (!event || typeof event !== 'object') return;
    if (event.type === 'body') {
      await this.refresh(); // VRM入れ替えの直後でも、古いカタログで受け付けない。
      if (!this.resident || this.stopped || from.idea !== this.resident.idea || from.port !== this.resident.port) return;
      const records = await appendBodyChoice(this.resident.idea, event, this.catalog);
      if (records.length) this.broadcast({ type: 'life' });
    } else if (event.type === 'state' && (event.state === 'asleep' || event.state === 'awake')) {
      const asleep = event.state === 'asleep';
      if (asleep !== this.mindAsleep) {
        this.mindAsleep = asleep;
        this.broadcast({ type: 'life' });
      }
    } else if (event.type === 'approach') {
      const records = await appendBodyApproach(this.resident.idea, event);
      if (records.length) this.broadcast({ type: 'life' });
    } else if (event.type === 'said') {
      this.broadcast(event);
    }
  }

  private disconnect() {
    if (this.retry) clearTimeout(this.retry);
    this.retry = undefined;
    const upstream = this.upstream;
    this.upstream = undefined;
    this.connected = false;
    this.mindAsleep = false;
    this.incoming?.destroy();
    this.incoming = undefined;
    upstream?.destroy();
    this.perceiving?.abort();
  }

  stop() {
    this.stopped = true;
    if (this.poll) clearInterval(this.poll);
    this.disconnect();
    this.endClients();
  }
}
