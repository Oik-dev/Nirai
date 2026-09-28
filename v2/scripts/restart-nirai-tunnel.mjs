import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const alias = "nirai-v2-runtime";
const tunnelClient = join(
  process.env.LOCALAPPDATA ?? "",
  "Programs",
  "OpenAI",
  "tunnel-client",
  "tunnel-client.exe",
);
const profileDir = join(process.env.APPDATA ?? "", "tunnel-client");
const profilePath = join(profileDir, `${alias}.yaml`);

if (!existsSync(tunnelClient)) {
  throw new Error(`tunnel-client.exe not found: ${tunnelClient}`);
}
if (!existsSync(profilePath)) {
  throw new Error(`Nirai tunnel profile not found: ${profilePath}`);
}

const profile = JSON.parse(readFileSync(profilePath, "utf8"));
const tunnelId = profile?.control_plane?.tunnel_id;
const runtimeApiKey = profile?.control_plane?.api_key;
const mcpCommand = profile?.mcp?.commands?.find(item => item?.channel === "main")?.command;

if (typeof tunnelId !== "string" || !tunnelId) {
  throw new Error("Nirai tunnel profile has no control_plane.tunnel_id");
}
if (typeof runtimeApiKey !== "string" || !runtimeApiKey) {
  throw new Error("Nirai tunnel profile has no control_plane.api_key");
}
if (typeof mcpCommand !== "string" || !mcpCommand) {
  throw new Error("Nirai tunnel profile has no main MCP command");
}

function tunnel(args) {
  return spawnSync(tunnelClient, args, {
    windowsHide: true,
    encoding: "utf8",
  });
}

// The profile remains the single source of truth. Reconnecting on every Nirai launch
// guarantees that the runtime loads the MCP definition produced by the current build.
tunnel(["runtimes", "stop", alias]);

const result = tunnel([
  "runtimes",
  "connect",
  "--json",
  "--alias", alias,
  "--tunnel-id", tunnelId,
  "--runtime-api-key", runtimeApiKey,
  "--mcp-command", mcpCommand,
]);

if (result.status !== 0) {
  throw new Error(
    `Nirai MCP tunnel connect failed (exit ${result.status}): ${result.stderr || result.stdout || "no output"}`,
  );
}

let status;
try {
  status = JSON.parse(result.stdout);
} catch {
  throw new Error(`Nirai MCP tunnel returned invalid status: ${result.stdout || "no output"}`);
}

if (!status.ready || !status.process_running || Number(status.process?.pid) <= 0) {
  throw new Error(`Nirai MCP tunnel did not become ready: ${result.stdout}`);
}
