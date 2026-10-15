// 精神からの知らせを海が1本で受ける。本人の体の選択は記録してから、眠りと目覚めはそのまま、暮らしが変わったと窓へ知らせる。
import { request, type ClientRequest, type IncomingMessage, type ServerResponse } from 'node:http';
import { readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { appendBodyApproach, appendBodyChoice, bodyRecordsNewestFirst, pendingBodyWishes, readBodyCatalog, type BodyCatalog } from './body.ts';
import { lifeOf, lifeWithoutMind } from './life.ts';
import { bodyResidents, seaResident, type BodyResident, type Resident } from './mind.ts';
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

// 仕事の記録が増えたときも窓へ知らせる。本文は読まず、時刻と大きさだけ。
async function recordRevision(idea: string) {
  const folder = join(idea, 'lifelog', 'body');
  const names = (await readdir(folder).catch(() => [] as string[])).filter(name => name.endsWith('.jsonl')).sort();
  const latest = names.at(-1);
  const info = latest ? await stat(join(folder, latest), { bigint: true }).catch(() => undefined) : undefined;
  return `${latest ?? ''}:${info?.size ?? ''}:${info?.mtimeNs ?? ''}`;
}

export class SeaEvents {
  private settings: SeaSettings;
  private resident?: Resident;
  private bodies: BodyResident[] = [];
  private catalogs: Record<string, BodyCatalog> = {};
  private revisions: Record<string, string> = {};
  private records: Record<string, string> = {};
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
      const lives: Record<string, Awaited<ReturnType<typeof lifeOf>>> = {};
      for (const body of this.bodies) {
        try {
          lives[body.name] = body.name === this.resident?.name
            ? await lifeOf(bodyRecordsNewestFirst(body.idea), !this.connected || this.mindAsleep, this.catalog)
            : lifeWithoutMind();
        } catch {
          // ひとりの記録が読めなくても、ほかの暮らしを欠かさない。
          lives[body.name] = lifeWithoutMind();
        }
      }
      return {
        lives,
        catalogs: { ...this.catalogs },
        revisions: { ...this.revisions },
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
    const bodies = await bodyResidents(this.settings);
    const resident = await seaResident(this.settings);
    if (this.stopped) return;
    const changedResident = resident?.idea !== this.resident?.idea || resident?.port !== this.resident?.port;
    const previousMentalRevision = this.revision;
    if (changedResident) {
      this.disconnect();
      this.resident = resident;
      this.revision = '';
      this.catalog = EMPTY;
      this.endClients();
    }
    const revisions: Record<string, string> = {};
    const catalogs: Record<string, BodyCatalog> = {};
    const records: Record<string, string> = {};
    for (const body of bodies) {
      const revision = await bodyRevision(body.idea).catch(() => '');
      const changed = revision !== this.revisions[body.name];
      const catalog = changed
        ? await readBodyCatalog(body.idea).catch(() => undefined)
        : this.catalogs[body.name];
      // 精神のカタログは読める版だけを確定し、選択の入口を壊さない。
      revisions[body.name] = body.name === resident?.name && !catalog
        ? this.revisions[body.name] ?? '' : revision;
      catalogs[body.name] = catalog ?? EMPTY;
      // 精神を持つ住人の変更はSSEですぐ届く。記録の見回りで二重通知しない。
      records[body.name] = body.name === resident?.name
        ? this.records[body.name] ?? '' : await recordRevision(body.idea);
    }
    const catalogChanged = JSON.stringify(revisions) !== JSON.stringify(this.revisions);
    const lifeChanged = JSON.stringify(records) !== JSON.stringify(this.records);
    this.bodies = bodies;
    this.revisions = revisions;
    this.catalogs = catalogs;
    this.records = records;
    this.revision = resident ? revisions[resident.name] ?? '' : '';
    this.catalog = resident ? catalogs[resident.name] ?? EMPTY : EMPTY;
    if (catalogChanged) {
      if (resident && this.connected && this.revision !== previousMentalRevision) await this.perceive();
      this.broadcast({ type: 'catalog' });
    } else if (lifeChanged) this.broadcast({ type: 'life' });
    if (resident && !this.upstream && !this.retry) this.connect();
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
