// 郵便局の入口。127.0.0.1 の1つのHTTPサーバー。
// - /mcp/<住人>：住人ごとの郵便受け（MCP）。入口で差出人が決まる。手を持たない脳の住人（Holo）には、郵便局が手も貸す
// - /holo/…：Holoの部屋の拡張との口（返事の通信の知らせ、起こす一言）
// 外から届く呼び出しは、ここで入口ごとに受け止め、内側には決まった形だけを渡す。
// 本番は番人（keeper.ts）が --live で起こし、出力を記録に残す。--live のときは、Holoへのトンネルも起こす。

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { claudeCommand, CliResident, codexCommand } from "./cli.ts";
import { Hands } from "./hands.ts";
import { HoloRoom, type NetReport } from "./holo.ts";
import { append, newLetterId, readAll, workKey } from "./letters.ts";
import { createMailbox, validWork } from "./mcp.ts";
import { withMcpDiagnostic } from "./mcp-diagnostic.ts";
import { PostOffice } from "./office.ts";
import { decodeRevision, fetchSeaStatus, postIdle, probePostOffice, readRevision, readRevisionNow, RELOAD_EXIT_CODE, ReloadWatcher, writeHandoff } from "./reload.ts";
import { resolveResident, settings } from "./settings.ts";
import { residentPostStatus } from "./status.ts";
import { startTunnel } from "./tunnel.ts";
import { meterDefaults } from "./usage.ts";
import { usageRequest } from "./usage-http.ts";
import { POST_OFFICE } from "./waker.ts";

const live = process.argv.includes("--live");
// 本番は --live で自動入れ替えとトンネルを両方使う。使い捨て試験は、
// トンネルを起こさず自動入れ替えだけを試せるよう NIRAI_SELF_RELOAD=1 を使う。
const selfReload = process.env.NIRAI_SELF_RELOAD === "1" || (live && process.env.NIRAI_SELF_RELOAD !== "0");
const repoRoot = settings.repoRoot;
const runtimeDir = process.env.NIRAI_RUNTIME_DIR ?? join(repoRoot, "world", "runtime");
const suppliedRevision = decodeRevision(process.env.NIRAI_RUNNING_REVISION);
const runningRevision = await readRevision(repoRoot, suppliedRevision?.head ?? 'HEAD');

const holo = new HoloRoom(settings.residentsRoot, { restMs: settings.restMs, ...settings.holo });
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
      kind: "letter" as const, ts: new Date().toISOString(), id: newLetterId(), from: POST_OFFICE, to: "Holo",
      body: `郵便局の新しい版は、試しに起こしたとき正常に起きなかったので入れ替えなかった。今の郵便局はそのまま動いている。\n\n${detail}`,
      based_on: revision.post,
    };
    append(settings.residentsRoot, "Holo", letter);
    office.onSent(letter);
  },
}) : undefined;
office = new PostOffice(settings, [codex, claude], () => hands.busy(), () => { void reloader?.check(); },
  () => Boolean(reloader?.waiting()));

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
      settings.hands.for.includes(resident) ? hands : undefined,
      resident === "Holo" ? undefined : (work ?? ""), resident === "Holo");
    res.on("close", () => {
      void transport.close();
      void server.close();
    });
    await server.connect(transport);
    await transport.handleRequest(req, res, parsedBody);
  });
}

async function holoRoom(action: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
  // Masterのブラウザで開いたほかのWebページからは受けない（ページからの呼び出しには必ず Origin が付く）
  const origin = req.headers.origin;
  if (origin && !origin.startsWith("chrome-extension://")) return reply(res, 403, "extension only");
  const now = new Date();
  const queryWork = new URL(req.url ?? "/", `http://127.0.0.1:${settings.port}`).searchParams.get("work") ?? "";
  if (queryWork && !validWork(queryWork)) return reply(res, 400, "invalid work");
  const requestWork = (body: Record<string, unknown>): string | undefined => {
    const work = body.work ?? "";
    return typeof work === "string" && (!work || validWork(work)) ? work : undefined;
  };
  if (action === "next" && req.method === "GET") {
    if (reloader?.waiting()) return reply(res, 204);
    const next = holo.next(now);
    return next ? reply(res, 200, next) : reply(res, 204);
  }
  if (action === "status" && req.method === "GET") {
    const workRooms = [...new Set(readAll(settings.residentsRoot, "Holo")
      .filter(line => line.kind === "room" && Boolean(line.work)).map(line => workKey(line.work ?? "")))];
    const closedWorks = workRooms.filter(work => !existsSync(join(settings.workRoot, work)));
    const awake = new Map<string, boolean>([
      ["Holo", holo.awake(now)],
      [codex.name, codex.awake()],
      [claude.name, claude.awake()],
    ]);
    return reply(res, 200, {
      revision: runningRevision,
      reload: reloader?.waiting() ?? null,
      residents: settings.team.map(name => residentPostStatus(name, readAll(settings.residentsRoot, name), awake.get(name) ?? false, now)),
      room: holo.status(queryWork),
      closedWorks,
    });
  }
  if (action === "move" && req.method === "POST") {
    const body = await readJson(req);
    const work = requestWork(body);
    if (work === undefined) return reply(res, 400, "invalid work");
    const created = holo.move(now, work);
    if (created) office.soon();
    return reply(res, 200, { created });
  }
  if (action === "room" && req.method === "POST") {
    const body = await readJson(req);
    const work = requestWork(body);
    if (work === undefined) return reply(res, 400, "invalid work");
    const registered = typeof body.url === "string" && holo.register(body.url, now, work);
    return reply(res, 200, { registered, room: holo.status(work) });
  }
  if (action === "retry" && req.method === "POST") {
    const body = await readJson(req);
    const work = requestWork(body);
    if (work === undefined) return reply(res, 400, "invalid work");
    holo.retryRoom(work);
    office.soon();
    return reply(res, 204);
  }
  if (action === "net" && req.method === "POST") {
    const report = (await readJson(req)) as NetReport;
    if (requestWork(report as Record<string, unknown>) === undefined) return reply(res, 400, "invalid work");
    console.log(`${now.toISOString()} holo ${report.phase} ${report.method} ${report.path} ${report.status ?? ""}${report.error ?? ""}`);
    holo.net(report, now);
    return reply(res, 204);
  }
  if (action === "sent" && req.method === "POST") {
    const result = (await readJson(req)) as { ok: boolean; letters: string[]; url?: string; reason?: string; touched?: boolean; work?: string };
    if (requestWork(result as Record<string, unknown>) === undefined) return reply(res, 400, "invalid work");
    console.log(`${now.toISOString()} holo sent ok=${result.ok} ${result.reason ?? ""}`);
    holo.sent(result, now);
    return reply(res, 204);
  }
  return reply(res, 404, "no such action");
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const path = (req.url ?? "").split("?")[0];
  if (path === "/usage" || path.startsWith("/usage/")) return usageRequest(req, res,
    { ...meterDefaults(settings.residentsRoot, repoRoot), wakeLimits: {
      Codex: settings.codex.limitMs, Claude: settings.claude.limitMs, Holo: settings.holo.busyLimitMs,
    } }, settings.port);
  const mcp = /^\/mcp\/([^/]+)(?:\/([^/]+))?\/?$/.exec(path);
  const resident = mcp ? resolveResident(decodeURIComponent(mcp[1])) : undefined;
  const work = mcp?.[2] ? decodeURIComponent(mcp[2]) : undefined;
  if (resident && (work === undefined || (resident !== "Holo" && validWork(work)))) return mailbox(resident, req, res, work);
  const room = /^\/holo\/([a-z]+)$/.exec(path);
  if (room) return holoRoom(room[1], req, res);
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
