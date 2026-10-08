import type { IncomingMessage, ServerResponse } from "node:http";

// The SDK also limits JSON-RPC bodies to 4 MiB. Keep that limit when using its
// supported pre-parsed-body entrypoint so that we can identify failed calls.
const MAX_BODY = 4 * 1024 * 1024;
const MAX_ERROR = 8 * 1024;

function short(value: unknown, limit = 240): string {
  return String(value ?? "-").replace(/[\r\n\t]/g, " ").slice(0, limit);
}

function jsonError(res: ServerResponse, status: number, code: number, message: string): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify({ jsonrpc: "2.0", error: { code, message }, id: null }));
}

/** Records only the protocol clues and returned error message for MCP 4xx responses. */
export async function withMcpDiagnostic(
  req: IncomingMessage,
  res: ServerResponse,
  serve: (parsedBody?: unknown) => Promise<void>,
  log: (line: string) => void = console.log,
): Promise<void> {
  let rpcMethod = "-";
  let rpcVersion = "-";
  const output: Buffer[] = [];
  let recorded = 0;
  const capture = (chunk: unknown) => {
    if (res.statusCode < 400 || res.statusCode >= 500) return;
    if (typeof chunk !== "string" && !(chunk instanceof Uint8Array)) return;
    const bytes = Buffer.from(chunk);
    if (recorded >= MAX_ERROR) return;
    const part = bytes.subarray(0, MAX_ERROR - recorded);
    output.push(part);
    recorded += part.length;
  };
  const write = res.write.bind(res);
  const end = res.end.bind(res);
  res.write = ((...args: Parameters<typeof res.write>) => {
    capture(args[0]);
    return write(...args);
  }) as typeof res.write;
  res.end = ((...args: Parameters<typeof res.end>) => {
    capture(args[0]);
    return end(...args);
  }) as typeof res.end;

  res.once("finish", () => {
    if (res.statusCode < 400 || res.statusCode >= 500) return;
    let error = "-";
    try {
      const response = JSON.parse(Buffer.concat(output).toString("utf8"));
      if (typeof response?.error?.message === "string") error = response.error.message;
    } catch { /* A response without a JSON-RPC error has no error text. */ }
    log(`${new Date().toISOString()} mcp 4xx ${JSON.stringify({
      status: res.statusCode,
      protocolVersion: short(req.headers["mcp-protocol-version"]),
      sessionIdPresent: Boolean(req.headers["mcp-session-id"]),
      accept: short(req.headers.accept),
      method: short(rpcMethod, 100),
      initializeProtocolVersion: short(rpcVersion, 100),
      error: short(error),
    })}`);
  });

  if (req.method !== "POST") return serve();

  const chunks: Buffer[] = [];
  let length = 0;
  if (Number(req.headers["content-length"]) > MAX_BODY) {
    return jsonError(res, 413, -32000, `Payload Too Large: Request body must not exceed ${MAX_BODY} bytes`);
  }
  for await (const chunk of req) {
    const data = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    length += data.length;
    if (length > MAX_BODY) {
      return jsonError(res, 413, -32000, `Payload Too Large: Request body must not exceed ${MAX_BODY} bytes`);
    }
    chunks.push(data);
  }
  let parsedBody: unknown;
  try {
    parsedBody = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return jsonError(res, 400, -32700, "Parse error: Invalid JSON");
  }
  const first = Array.isArray(parsedBody) ? undefined : parsedBody;
  if (first && typeof first === "object") {
    const message = first as { method?: unknown; params?: { protocolVersion?: unknown } };
    if (typeof message.method === "string") {
      rpcMethod = message.method;
      if (message.method === "initialize") rpcVersion = String(message.params?.protocolVersion ?? "-");
    }
  } else if (Array.isArray(parsedBody)) {
    rpcMethod = "batch";
  }
  return serve(parsedBody);
}
