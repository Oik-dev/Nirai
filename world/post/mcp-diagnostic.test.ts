import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { createMailbox } from "./mcp.ts";
import { withMcpDiagnostic } from "./mcp-diagnostic.ts";

test("MCP接続はそのまま動き、4xx時だけ本文を伏せて診断する", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-mcp-diagnostic-"));
  const lines: string[] = [];
  const http = createServer((req, res) => {
    void withMcpDiagnostic(req, res, async parsedBody => {
      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: undefined,
        enableJsonResponse: true,
        enableDnsRebindingProtection: true,
        allowedHosts: [String(req.headers.host)],
      });
      const server = createMailbox("Holo", root);
      res.on("close", () => { void transport.close(); void server.close(); });
      await server.connect(transport);
      await transport.handleRequest(req, res, parsedBody);
    }, line => lines.push(line)).catch(error => {
      res.writeHead(500).end(String(error));
    });
  });
  await new Promise<void>(resolve => http.listen(0, "127.0.0.1", resolve));
  try {
    const address = http.address();
    if (!address || typeof address === "string") throw Error("missing address");
    const url = `http://127.0.0.1:${address.port}/mcp/holo`;
    const call = (body: unknown, version?: string) => fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
        ...(version ? { "mcp-protocol-version": version } : {}),
      },
      body: JSON.stringify(body),
    });
    const init = await call({ jsonrpc: "2.0", id: 1, method: "initialize", params: {
      protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "diagnostic-test", version: "1" },
    } });
    assert.equal(init.status, 200, await init.text());
    assert.deepEqual(lines, []);

    const response = await call({ jsonrpc: "2.0", id: 2, method: "tools/list", params: { secret: "NEVER_LOG_ME" } }, "2099-01-01");
    const errorBody = await response.text();
    assert.equal(response.status, 400, errorBody);
    const json = JSON.parse(errorBody);
    assert.match(json.error.message, /Unsupported protocol version/);
    assert.equal(lines.length, 1);
    assert.match(lines[0], /"status":400/);
    assert.match(lines[0], /"protocolVersion":"2099-01-01"/);
    assert.match(lines[0], /"sessionIdPresent":false/);
    assert.match(lines[0], /"accept":"application\/json, text\/event-stream"/);
    assert.match(lines[0], /"method":"tools\/list"/);
    assert.match(lines[0], /Unsupported protocol version/);
    assert.doesNotMatch(lines[0], /NEVER_LOG_ME/);
  } finally {
    await new Promise<void>((resolve, reject) => http.close(error => error ? reject(error) : resolve()));
  }
});

test("initialize失敗では宣言バージョンを記録し、改行と内容は漏らさない", async () => {
  const lines: string[] = [];
  const http = createServer((req, res) => {
    void withMcpDiagnostic(req, res, async () => {
      res.writeHead(400, { "content-type": "application/json" });
      res.end(JSON.stringify({ jsonrpc: "2.0", error: { code: -32000, message: "Bad Request" }, id: null }));
    }, line => lines.push(line));
  });
  await new Promise<void>(resolve => http.listen(0, "127.0.0.1", resolve));
  try {
    const address = http.address();
    if (!address || typeof address === "string") throw Error("missing address");
    const response = await fetch(`http://127.0.0.1:${address.port}/mcp/holo`, {
      method: "POST", headers: { "content-type": "application/json", accept: "application/json\nNEVER_LOG_ME".replace("\n", " "), "mcp-session-id": "secret-id" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-03-26", private: "MY_PRIVATE_MESSAGE" } }),
    });
    assert.equal(response.status, 400);
    assert.equal(lines.length, 1);
    assert.match(lines[0], /"method":"initialize"/);
    assert.match(lines[0], /"initializeProtocolVersion":"2025-03-26"/);
    assert.match(lines[0], /"sessionIdPresent":true/);
    assert.doesNotMatch(lines[0], /secret-id|MY_PRIVATE_MESSAGE/);
  } finally {
    await new Promise<void>((resolve, reject) => http.close(error => error ? reject(error) : resolve()));
  }
});
