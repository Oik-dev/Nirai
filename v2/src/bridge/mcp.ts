import { join } from "node:path";
import { controlCommand } from "./client.js";
import type { HubCommandEnvelope } from "../shared/types.js";
import { DEFAULT_SETTINGS } from "../shared/settings.js";

const tool = {
  name: "nirai_command",
  description: "Use Nirai from the current Holo Turn. Start with GetTaskContext. Available commands are GetTaskContext, GetRunResult, InvokeCapability, RequestMasterInput, and CompleteTask. Reuse the same command_id after a lost reply.",
  inputSchema: { type: "object", additionalProperties: false, required: ["turn_id", "envelope"], properties: {
    turn_id: { type: "string" },
    envelope: { type: "object", additionalProperties: false, required: ["command_id", "type", "payload"], properties: {
      command_id: { type: "string" }, type: { type: "string" }, payload: { type: "object" },
    } },
  } },
};

export async function serveMcp(connectionPath: string): Promise<void> {
  let initialized = false;
  const send = (value: unknown) => process.stdout.write(`${JSON.stringify(value)}\n`);
  async function handle(line: string) {
    let request: { jsonrpc: string; id?: string | number; method: string; params?: Record<string, unknown> };
    try { request = JSON.parse(line); }
    catch { send({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }); return; }
    if (!request || request.jsonrpc !== "2.0" || typeof request.method !== "string") {
      send({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "Invalid Request" } }); return;
    }
    if (request.id === undefined) return;
    const reply = (result: unknown) => send({ jsonrpc: "2.0", id: request.id, result });
    if (request.method === "initialize") {
      initialized = true;
      reply({ protocolVersion: "2025-11-25", capabilities: { tools: {} }, serverInfo: { name: "nirai-v2-local", version: "0.1.0" } });
    } else if (request.method === "ping") reply({});
    else if (!initialized) send({ jsonrpc: "2.0", id: request.id, error: { code: -32600, message: "Initialize first" } });
    else if (request.method === "tools/list") reply({ tools: [tool] });
    else if (request.method === "tools/call" && request.params?.name === tool.name) {
      try {
        const args = request.params.arguments as { turn_id: string; envelope: Pick<HubCommandEnvelope, "command_id" | "type" | "payload"> };
        if (typeof args?.turn_id !== "string" || !args.turn_id || !args.envelope) throw new Error("turn_id and envelope are required");
        const envelope: HubCommandEnvelope = { protocol_version: 1, issued_at: new Date().toISOString(), target: null, ...args.envelope };
        const result = await controlCommand(connectionPath, args.turn_id, envelope);
        reply({ content: [{ type: "text", text: JSON.stringify(result) }] });
      } catch (error) {
        reply({ isError: true, content: [{ type: "text", text: error instanceof Error ? error.message : "Hub request failed" }] });
      }
    } else send({ jsonrpc: "2.0", id: request.id, error: { code: -32601, message: "Method or tool not found" } });
  }
  let pending = Buffer.alloc(0);
  for await (const chunk of process.stdin) {
    pending = Buffer.concat([pending, Buffer.from(chunk)]);
    let end: number;
    while ((end = pending.indexOf(10)) >= 0) {
      if (end > DEFAULT_SETTINGS.control_message_bytes) throw new Error("MCP message exceeds limit");
      const line = pending.subarray(0, end).toString("utf8");
      pending = pending.subarray(end + 1);
      if (line.trim()) await handle(line);
    }
    if (pending.length > DEFAULT_SETTINGS.control_message_bytes) throw new Error("MCP message exceeds limit");
  }
}

const root = process.env.NIRAI_V2_DATA_ROOT;
if (!root) { process.stderr.write("NIRAI_V2_DATA_ROOT is required\n"); process.exitCode = 1; }
else serveMcp(join(root, "control", "connection.json")).catch(() => { process.stderr.write("Nirai MCP transport stopped\n"); process.exitCode = 1; });
