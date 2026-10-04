// 郵便局の入口。127.0.0.1 の1つのHTTPサーバー。
// - /mcp/<住人>：住人ごとの郵便受け（MCP）。入口で差出人が決まる
// - /holo/…：Holoの部屋の拡張との口（返事の通信の知らせ、起こす一言）
// 外から届く呼び出しは、ここで入口ごとに受け止め、内側には決まった形だけを渡す。

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { HoloRoom, type NetReport } from "./holo.ts";
import { createMailbox } from "./mcp.ts";
import { PostOffice } from "./office.ts";
import { resolveResident, settings } from "./settings.ts";

const holo = new HoloRoom(settings.residentsRoot, { restMs: settings.restMs, ...settings.holo });
const office = new PostOffice(settings);

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

async function mailbox(resident: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
  // 毎回、新しい郵便受けで答える（状態はすべて生ログにあるので、つなぎっぱなしにしない）。
  if (req.method !== "POST") return reply(res, 405, "POST only");
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
    enableJsonResponse: true,
    enableDnsRebindingProtection: true,
    allowedHosts: [`127.0.0.1:${settings.port}`, `localhost:${settings.port}`],
  });
  const server = createMailbox(resident, settings.residentsRoot, letter => office.onSent(letter));
  res.on("close", () => {
    void transport.close();
    void server.close();
  });
  await server.connect(transport);
  await transport.handleRequest(req, res);
}

async function holoRoom(action: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
  // Masterのブラウザで開いたほかのWebページからは受けない（ページからの呼び出しには必ず Origin が付く）
  const origin = req.headers.origin;
  if (origin && !origin.startsWith("chrome-extension://")) return reply(res, 403, "extension only");
  const now = new Date();
  if (action === "next" && req.method === "GET") {
    const next = holo.next(now);
    return next ? reply(res, 200, next) : reply(res, 204);
  }
  if (action === "net" && req.method === "POST") {
    const report = (await readJson(req)) as NetReport;
    console.log(`${now.toISOString()} holo ${report.phase} ${report.method} ${report.path} ${report.status ?? ""}${report.error ?? ""}`);
    holo.net(report, now);
    return reply(res, 204);
  }
  if (action === "sent" && req.method === "POST") {
    const result = (await readJson(req)) as { ok: boolean; letters: string[]; reason?: string };
    console.log(`${now.toISOString()} holo sent ok=${result.ok} ${result.reason ?? ""}`);
    holo.sent(result, now);
    return reply(res, 204);
  }
  return reply(res, 404, "no such action");
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const path = (req.url ?? "").split("?")[0];
  const mcp = /^\/mcp\/([^/]+)\/?$/.exec(path);
  const resident = mcp ? resolveResident(decodeURIComponent(mcp[1])) : undefined;
  if (resident) return mailbox(resident, req, res);
  const room = /^\/holo\/([a-z]+)$/.exec(path);
  if (room) return holoRoom(room[1], req, res);
  return reply(res, 404, "not here");
}

const server = createServer((req, res) => {
  if (!(req.url ?? "").startsWith("/holo/")) {
    res.on("finish", () => console.log(`${new Date().toISOString()} ${req.method} ${req.url} ${res.statusCode}`));
  }
  handle(req, res).catch(error => {
    console.error(error);
    if (!res.headersSent) reply(res, 500, "post office error");
  });
});

server.listen(settings.port, "127.0.0.1", () => {
  console.log(`post office: http://127.0.0.1:${settings.port}  residents=${settings.residentsRoot}  work=${settings.workRoot}`);
  office.start();
});
