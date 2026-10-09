import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtempSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CliResident } from "./cli.ts";
import { append, readAll } from "./letters.ts";
import { createMailbox } from "./mcp.ts";
import { withMcpDiagnostic } from "./mcp-diagnostic.ts";
import { PostOffice } from "./office.ts";

test("別ポートの郵便局で偽CLI二筋が並列に起き、それぞれのHTTP郵便受けだけを読む", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-parallel-http-"));
  const workRoot = mkdtempSync(join(tmpdir(), "nirai-parallel-work-"));
  const server = createServer((req, res) => {
    const matched = /^\/mcp\/codex\/([^/]+)$/.exec(req.url ?? "");
    if (!matched) return void res.writeHead(404).end();
    void withMcpDiagnostic(req, res, async body => {
      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: undefined, enableJsonResponse: true,
        enableDnsRebindingProtection: true, allowedHosts: [String(req.headers.host)],
      });
      const mailbox = createMailbox("Codex", root, undefined, undefined, decodeURIComponent(matched[1]));
      res.on("close", () => { void transport.close(); void mailbox.close(); });
      await mailbox.connect(transport);
      await transport.handleRequest(req, res, body);
    }).catch(error => res.writeHead(500).end(String(error)));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw Error("no address");
  const sdkClient = import.meta.resolve("@modelcontextprotocol/sdk/client/index.js");
  const sdkTransport = import.meta.resolve("@modelcontextprotocol/sdk/client/streamableHttp.js");
  const program = `
    const { Client } = await import(${JSON.stringify(sdkClient)});
    const { StreamableHTTPClientTransport } = await import(${JSON.stringify(sdkTransport)});
    const client = new Client({name:"fake-cli",version:"1"});
    await client.connect(new StreamableHTTPClientTransport(new URL(process.argv[1])));
    const result = await client.callTool({name:"read_mailbox",arguments:{}});
    console.log(result.content[0].text);
    await client.close();
  `;
  const stops: string[] = [];
  let finish!: () => void;
  const both = new Promise<void>(resolve => finish = resolve);
  const cli = new CliResident("Codex", root, (_text, work) => ({
    file: process.execPath, cwd: workRoot,
    args: ["--input-type=module", "-e", program, `http://127.0.0.1:${address.port}/mcp/codex/${encodeURIComponent(work ?? "")}`],
  }), 15_000, stop => { stops.push(stop.how); if (stops.length === 2) finish(); });
  const post = new PostOffice({
    residentsRoot: root, workRoot, team: ["Holo", "Codex"], maxConcurrent: { Codex: 2 },
    tellMasterAfter: 3, sweepMs: 60_000, restMs: 60_000,
    workKeepMs: 50 * 60_000, limitWaitMs: 60_000,
  }, [cli]);
  try {
    for (const [id, work] of [["A", "work-A"], ["B", "work-B"]] as const) {
      append(root, "Codex", { kind: "letter", ts: new Date().toISOString(), id, from: "Holo", to: "Codex", body: id, work });
    }
    post.sweep(new Date());
    assert.equal(cli.awakeCount(), 2);
    await Promise.race([both, new Promise<never>((_, reject) => {
      const timeout = setTimeout(() => reject(new Error("fake CLI timeout")), 10_000);
      timeout.unref();
    })]);
    assert.deepEqual(stops, ["exit", "exit"]);
    assert.equal(cli.awake(), false);
    const lines = readAll(root, "Codex");
    assert.deepEqual(lines.filter(l => l.kind === "wake").map(l => l.work).sort(), ["work-a", "work-b"]);
    const logs = join(root, "Codex", "lifelog", "codex-cli");
    const files = readdirSync(logs);
    assert.equal(files.length, 2);
    const contents = files.map(f => JSON.parse(readFileSync(join(logs, f), "utf8")));
    assert.deepEqual(contents.map(x => x[0]?.id).sort(), ["A", "B"]);
    assert.ok(contents.every(x => x.length === 1));
  } finally {
    post.stop();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
