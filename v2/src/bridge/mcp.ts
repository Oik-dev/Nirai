import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { controlCommand } from "./client.js";
import type { HubCommandEnvelope } from "../shared/types.js";
import { DEFAULT_SETTINGS } from "../shared/settings.js";
import { productDataRoot } from "../shared/paths.js";

const PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"];
const worldRulesPath = process.env.NIRAI_WORLD_RULES_PATH
  ?? fileURLToPath(new URL("../../../../WORLD_RULES.md", import.meta.url));
const worldRules = readFileSync(worldRulesPath, "utf8").trim();
if (!worldRules) throw new Error("WORLD_RULES.md is empty");
const serverInstructions = `Nirai TaskではWORLD_RULESに従う。会話ContextはNiraiから届くMaster原文と現在のChatGPT会話とし、別Contextを再構成しない。Nirai MCPはTask制御やNirai固有能力が必要な場合に使う。自分の表情・衣装・アクセサリ・外見は利用可能なAvatar Capabilityの範囲で、自分のPersonaと会話・状況をもとに本人が選ぶ。瞬き・通常の視線追従等はAvatar Runtimeが担う。

${worldRules}`;

const tool = {
  name: "nirai_command",
  description: "Nirai Turn control; pass turn_id. Index: InvokeCapability {capability_id,operation,input}=Capabilityを使う; GetRunResult {run_id,max_bytes}=Run結果を見る; AwaitMasterReply {}=質問は通常回答に書き、次のMaster CHATを待つ; CompleteTask {result_summary,completion?}=完了条件を固定し、続く最終回答が保存された時点でTask完了. local: read {path,max_bytes?}=読む; search {path,query,max_results?}=探す; apply_patch {changes:[{path,before_sha256,content}]}=修正; inspect {profile|run_id}=run_command前確認・Run状態確認; run_command {profile,source_fingerprint,timeout_ms?,output_bytes?}=登録済みbuild/test実行; cancel {run_id}=Run停止. avatar: inspect {}=自分の利用可能な表情・衣装・アクセサリ・外見、選択状態と表示確認を読む; set {model_id,expected_revision,appearance:{expression:{id,weight}|null,wardrobe:{item_id:boolean,...},choices?:{control_id:option_id,...}}}=自分の選択を保存。inspectのmodel_idとdesired.revisionを使う。wardrobeは全項目を指定。capabilities.controlsがあるモデルではchoicesに全controlのoption idを指定し、labelで意味を判断する。変更しない項目はdesired.appearanceから維持する。Mesh名やMorph名を指定しない。自己表現はResident本人が状況に応じて選ぶ。set成功は保存済みだけを示す。表示済みと伝える前にinspectのdisplay_appliedを確認する。Paths are workspace-relative. New command_id per request; reuse only for the same lost reply. result_summary is internal.",
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
      const requested = request.params?.protocolVersion;
      const protocolVersion = typeof requested === "string" && PROTOCOL_VERSIONS.includes(requested) ? requested : PROTOCOL_VERSIONS[0];
      reply({
        protocolVersion,
        capabilities: { tools: {} },
        serverInfo: { name: "nirai-v2-local", version: "0.1.0" },
        instructions: serverInstructions,
      });
    } else if (request.method === "ping") reply({});
    else if (!initialized) send({ jsonrpc: "2.0", id: request.id, error: { code: -32600, message: "Initialize first" } });
    else if (request.method === "tools/list") reply({ tools: [tool] });
    else if (request.method === "tools/call" && request.params?.name === tool.name) {
      try {
        const args = request.params.arguments as { turn_id: string; envelope: Pick<HubCommandEnvelope, "command_id" | "type" | "payload"> };
        if (typeof args?.turn_id !== "string" || !args.turn_id || !args.envelope) throw new Error("turn_id and envelope are required");
        const envelope: HubCommandEnvelope = { protocol_version: 1, issued_at: new Date().toISOString(), target: null, ...args.envelope };
        const result = await controlCommand(connectionPath, args.turn_id, envelope);
        reply({ content: [{ type: "text", text: JSON.stringify(result) }], structuredContent: result });
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

const root = process.argv[2] ?? process.env.NIRAI_V2_DATA_ROOT ?? productDataRoot();
serveMcp(join(root, "control", "connection.json")).catch(() => { process.stderr.write("Nirai MCP transport stopped\n"); process.exitCode = 1; });
