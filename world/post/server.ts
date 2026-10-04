// 郵便局の入口。127.0.0.1 の1つのHTTPサーバーで、住人ごとに /mcp/<住人> を開く。
// 外から届く呼び出しは、ここで入口ごとに受け止め、内側には決まった形だけを渡す。

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { createMailbox } from "./mcp.ts";
import { resolveResident, settings } from "./settings.ts";

function reply(res: ServerResponse, status: number, message: string): void {
  res.writeHead(status, { "content-type": "text/plain; charset=utf-8" }).end(message);
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const match = /^\/mcp\/([^/?]+)\/?(?:\?.*)?$/.exec(req.url ?? "");
  const resident = match ? resolveResident(decodeURIComponent(match[1])) : undefined;
  if (!resident) return reply(res, 404, "no such mailbox");
  // 毎回、新しい郵便受けで答える（状態はすべて生ログにあるので、つなぎっぱなしにしない）。
  if (req.method !== "POST") return reply(res, 405, "POST only");

  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
    enableJsonResponse: true,
    enableDnsRebindingProtection: true,
    allowedHosts: [`127.0.0.1:${settings.port}`, `localhost:${settings.port}`],
  });
  const mailbox = createMailbox(resident);
  res.on("close", () => {
    void transport.close();
    void mailbox.close();
  });
  await mailbox.connect(transport);
  await transport.handleRequest(req, res);
}

const server = createServer((req, res) => {
  res.on("finish", () => console.log(`${new Date().toISOString()} ${req.method} ${req.url} ${res.statusCode}`));
  handle(req, res).catch(error => {
    console.error(error);
    if (!res.headersSent) reply(res, 500, "post office error");
  });
});

server.listen(settings.port, "127.0.0.1", () => {
  console.log(`post office: http://127.0.0.1:${settings.port}/mcp/<resident>  residents=${settings.residentsRoot}`);
});
