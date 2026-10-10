// 郵便局の入口。127.0.0.1 の1つのHTTPサーバー。
// - /mcp/<住人>[/<作業場>]：住人ごとの郵便受け（MCP）。入口で差出人が決まる。手を持たない脳の住人（Holo）には、郵便局が手も貸す
// - /holo/…：Holoの席の拡張との口（席に入る・閉じる、返事の通信の知らせ、席へ送る一言、席の様子）
// 外から届く呼び出しは、ここで入口ごとに受け止め、内側には決まった形だけを渡す。
// 本番は番人（keeper.ts）が --live で起こし、出力を記録に残す。--live のときは、Holoへのトンネルも起こす。

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { claudeCommand, CliResident, codexCommand } from "./cli.ts";
import { Hands } from "./hands.ts";
import { HoloSeats, type NetReport, projectEntryUrl } from "./holo.ts";
import { append, newLetterId, readAll, waits } from "./letters.ts";
import { createMailbox, validWork } from "./mcp.ts";
import { withMcpDiagnostic } from "./mcp-diagnostic.ts";
import { PostOffice } from "./office.ts";
import { decodeRevision, fetchSeaStatus, postIdle, probePostOffice, readRevision, readRevisionNow, RELOAD_EXIT_CODE, ReloadWatcher, writeHandoff } from "./reload.ts";
import { resolveResident, settings } from "./settings.ts";
import { residentPostStatus } from "./status.ts";
import { startTunnel } from "./tunnel.ts";
import { meterDefaults } from "./usage.ts";
import { usageRequest } from "./usage-http.ts";
import { MESSENGER, POST_OFFICE } from "./waker.ts";

const live = process.argv.includes("--live");
// 本番は --live で自動入れ替えとトンネルを両方使う。使い捨て試験は、
// トンネルを起こさず自動入れ替えだけを試せるよう NIRAI_SELF_RELOAD=1 を使う。
const selfReload = process.env.NIRAI_SELF_RELOAD === "1" || (live && process.env.NIRAI_SELF_RELOAD !== "0");
const repoRoot = settings.repoRoot;
const runtimeDir = process.env.NIRAI_RUNTIME_DIR ?? join(repoRoot, "world", "runtime");
const suppliedRevision = decodeRevision(process.env.NIRAI_RUNNING_REVISION);
const runningRevision = await readRevision(repoRoot, suppliedRevision?.head ?? 'HEAD');

const holo = new HoloSeats(settings.residentsRoot, { restMs: settings.restMs, ...settings.holo });
// CodexとClaudeは郵便局がCLIで起こす。止まったら、すぐに見直す
const codex = new CliResident("Codex", settings.residentsRoot,
  codexCommand({ ...settings.codex, port: settings.port, workRoot: settings.workRoot }),
  settings.codex.limitMs, stop => office.onResidentStop("Codex", stop), settings.limitWaitMs);
const claude = new CliResident("Claude", settings.residentsRoot,
  claudeCommand({
    ...settings.claude, port: settings.port, workRoot: settings.workRoot, residentsRoot: settings.residentsRoot,
    scratch: join(tmpdir(), "nirai-post", String(settings.port)),
  }),
  settings.claude.limitMs, stop => office.onResidentStop("Claude", stop), settings.limitWaitMs);
// 手で始めた長いコマンドの結果は手紙で届く。手紙が出たときと同じく、すぐに見直す
const hands = new Hands(settings.residentsRoot, settings.workRoot, settings.hands, letter => office.onSent(letter));
let httpServer: ReturnType<typeof createServer>;
let office: PostOffice;
let activeRequests = 0;
let closingForReload = false;
const reloader = selfReload ? new ReloadWatcher({
  initial: runningRevision,
  read: () => readRevision(repoRoot),
  readNow: () => readRevisionNow(repoRoot),
  seaIdle: async () => {
    const sea = await fetchSeaStatus(Number(process.env.NIRAI_SEA_PORT ?? 47810), 1000);
    return !sea || sea.relaying === 0;
  },
  idle: () => postIdle(holo.awake(new Date()), [codex.awake(), claude.awake()], hands.busy(), activeRequests),
  probe: (from, to) => probePostOffice(repoRoot, runtimeDir, from, to),
  ready: candidate => {
    if (closingForReload) return;
    writeHandoff(runtimeDir, candidate);
    closingForReload = true;
    office.stop();
    console.log(`${new Date().toISOString()} post office: verified ${candidate.revision.post}; hand off ${candidate.root}`);
    httpServer.close(() => process.exit(RELOAD_EXIT_CODE));
    httpServer.closeIdleConnections?.();
  },
  rejected: (revision, detail) => {
    console.error(`${new Date().toISOString()} post office: new version rejected ${revision.post}: ${detail}`);
    const letter = {
      kind: "letter" as const, ts: new Date().toISOString(), id: newLetterId(), from: POST_OFFICE, to: MESSENGER, work: settings.holo.maintenanceWork,
      body: `郵便局の新しい版は、試しに起こしたとき正常に起きなかったので入れ替えなかった。今の郵便局はそのまま動いている。\n\n${detail}`,
      based_on: revision.post,
    };
    append(settings.residentsRoot, MESSENGER, letter);
    office.onSent(letter);
  },
}) : undefined;
office = new PostOffice(settings, [codex, claude], () => hands.busy(), () => { void reloader?.check(); },
  () => Boolean(reloader?.waiting()), { seats: holo, awaiting: () => hands.awaiting() });

function reply(res: ServerResponse, status: number, body?: unknown): void {
  if (body === undefined) return void res.writeHead(status).end();
  const json = typeof body === "string" ? JSON.stringify({ message: body }) : JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" }).end(json);
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 64_000) throw new Error("too large");
  }
  return JSON.parse(raw || "{}");
}

async function mailbox(resident: string, req: IncomingMessage, res: ServerResponse, work?: string): Promise<void> {
  // 毎回、新しい郵便受けで答える（状態はすべて生ログにあるので、つなぎっぱなしにしない）。
  await withMcpDiagnostic(req, res, async parsedBody => {
    if (req.method !== "POST") return reply(res, 405, "POST only");
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
      enableDnsRebindingProtection: true,
      allowedHosts: [`127.0.0.1:${settings.port}`, `localhost:${settings.port}`],
    });
    const server = createMailbox(resident, settings.residentsRoot, letter => office.onSent(letter),
      settings.hands.for.includes(resident) ? hands : undefined, work);
    res.on("close", () => {
      void transport.close();
      void server.close();
    });
    await server.connect(transport);
    await transport.handleRequest(req, res, parsedBody);
  });
}

const isSeat = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 1 && (value as number) <= settings.holo.seats;
const isText = (value: unknown): value is string => typeof value === "string" && value.length > 0;

async function holoSeats(action: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
  // Masterのブラウザで開いたほかのWebページからは受けない（ページからの呼び出しには必ず Origin が付く）
  const origin = req.headers.origin;
  if (origin && !origin.startsWith("chrome-extension://")) return reply(res, 403, "extension only");
  const now = new Date();
  if (action === "next" && req.method === "POST") {
    // 決めるのは見回りだけ。ここは今の決め方の先頭を返すだけで、何も書かない
    const offer = reloader?.waiting() ? undefined : office.holoNext(now);
    if (offer) holo.handed(now);
    return offer ? reply(res, 200, { ...offer, entry: projectEntryUrl(settings.holo.projectId) }) : reply(res, 204);
  }
  if (action === "status" && req.method === "GET") {
    const team = new Map(settings.team.map(name => [name, readAll(settings.residentsRoot, name)]));
    const waiting = waits([...team.values()]);
    const awake = new Map<string, boolean>([
      [MESSENGER, holo.awake(now)],
      [codex.name, codex.awake()],
      [claude.name, claude.awake()],
    ]);
    return reply(res, 200, {
      revision: runningRevision,
      reload: reloader?.waiting() ?? null,
      residents: settings.team.map(name => residentPostStatus(name, team.get(name)!, awake.get(name) ?? false, now, waiting)),
      seats: holo.status(now, waiting),
      entry: projectEntryUrl(settings.holo.projectId),
    });
  }
  if (req.method !== "POST") return reply(res, 404, "no such action");
  const body = await readJson(req);
  if (action === "enter") {
    const entered = holo.enter(now);
    if (!entered) return reply(res, 409, "no free seat");
    office.soon();
    return reply(res, 200, entered);
  }
  if (action === "leave") {
    if (!isSeat(body.seat)) return reply(res, 400, "invalid seat");
    const left = holo.leave(body.seat, now);
    if (!left) return reply(res, 409, "seat is empty");
    office.soon();
    return reply(res, 200, left);
  }
  if (action === "url") {
    if (!isSeat(body.seat) || !isText(body.since) || !isText(body.url)) return reply(res, 400, "invalid seat url");
    return reply(res, 200, { registered: holo.url(body.seat, body.since, body.url, now) });
  }
  if (action === "net") {
    const report = body as NetReport;
    console.log(`${now.toISOString()} holo ${report.phase} ${report.method} ${report.path} ${report.status ?? ""}${report.error ?? ""} seat=${report.seat ?? "-"}`);
    return holo.net(report, now) ? reply(res, 200, { ended: true }) : reply(res, 204);
  }
  if (action === "sent") {
    const { ok, seat, since, letters, url, reason } = body;
    console.log(`${now.toISOString()} holo sent seat=${String(seat)} ok=${String(ok)} ${typeof reason === "string" ? reason : ""}`);
    if (!isSeat(seat) || !isText(since) || !Array.isArray(letters) || !letters.every(isText)) return reply(res, 400, "invalid sent report");
    const result = { ok: ok === true, seat, since, letters, ...(isText(url) ? { url } : {}) };
    if (!holo.sent(result, now)) return reply(res, 409, "seat moved or letters not in the seat's work");
    return reply(res, 204);
  }
  return reply(res, 404, "no such action");
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const path = (req.url ?? "").split("?")[0];
  if (path === "/usage" || path.startsWith("/usage/")) return usageRequest(req, res,
    meterDefaults(settings.residentsRoot, repoRoot), settings.port);
  const mcp = /^\/mcp\/([^/]+)(?:\/([^/]+))?\/?$/.exec(path);
  const resident = mcp ? resolveResident(decodeURIComponent(mcp[1])) : undefined;
  const work = mcp?.[2] ? decodeURIComponent(mcp[2]) : undefined;
  // Holoは席で居場所が決まるので、作業場の入口は持たない。CLIの住人は作業場の入口で起こされる
  if (resident && (work === undefined || (resident !== MESSENGER && validWork(work)))) return mailbox(resident, req, res, work);
  const seats = /^\/holo\/([a-z]+)$/.exec(path);
  if (seats) return holoSeats(seats[1], req, res);
  return reply(res, 404, "not here");
}

httpServer = createServer((req, res) => {
  activeRequests++;
  let counted = true;
  const done = () => {
    if (!counted) return;
    counted = false;
    activeRequests = Math.max(0, activeRequests - 1);
  };
  res.once("finish", done);
  res.once("close", done);
  if (closingForReload) return reply(res, 503, "post office is changing version");
  if (!(req.url ?? "").startsWith("/holo/")) {
    res.on("finish", () => console.log(`${new Date().toISOString()} ${req.method} ${req.url} ${res.statusCode}`));
  }
  handle(req, res).catch(error => {
    console.error(error);
    if (!res.headersSent) reply(res, 500, "post office error");
  });
});

httpServer.listen(settings.port, "127.0.0.1", () => {
  console.log(`post office: http://127.0.0.1:${settings.port}  residents=${settings.residentsRoot}  work=${settings.workRoot}`);
  office.start();
  if (live) startTunnel();
});
