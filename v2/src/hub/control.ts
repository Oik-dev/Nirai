import { randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { execFile } from "node:child_process";
import { mkdir, writeFile, rm } from "node:fs/promises";
import { join } from "node:path";
import type { Server, Socket } from "node:net";
import { promisify } from "node:util";
import { encodeFrame, FrameReader } from "../shared/framing.js";
import { commandError, HubError } from "../shared/errors.js";
import type { HubCommandEnvelope } from "../shared/types.js";
import type { HubStore } from "./store.js";
import type { HubService } from "./service.js";

export interface ControlConnection { pipe: string; instance_id: string; secret: string }
const exec = promisify(execFile);

export async function privateDirectory(path: string): Promise<void> {
  await mkdir(path, { recursive: true, mode: 0o700 });
  if (process.platform === "win32") {
    await exec(join(process.env.SystemRoot ?? "C:\\Windows", "System32/WindowsPowerShell/v1.0/powershell.exe"), ["-NoProfile", "-NonInteractive", "-Command", `
      $ErrorActionPreference='Stop'
      $sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
      $acl=New-Object System.Security.AccessControl.DirectorySecurity
      $acl.SetOwner($sid)
      $acl.SetAccessRuleProtection($true,$false)
      $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
      $acl.AddAccessRule($rule)
      [System.IO.Directory]::SetAccessControl($env:NIRAI_PRIVATE_PATH,$acl)
    `], { windowsHide: true, env: { ...process.env, NIRAI_PRIVATE_PATH: path } });
  }
}

export class ControlServer {
  readonly instanceId = randomUUID();
  private readonly secret = randomBytes(32).toString("hex");
  private readonly sockets = new Set<Socket>();
  private closed = false;
  private constructor(private readonly path: string, private readonly store: HubStore, private readonly service: HubService) {}

  static async attach(server: Server, pipe: string, root: string, store: HubStore, service: HubService): Promise<ControlServer> {
    const dir = join(root, "control");
    await privateDirectory(dir);
    const control = new ControlServer(join(dir, "connection.json"), store, service);
    server.removeAllListeners("connection");
    server.on("connection", socket => control.connect(socket));
    await writeFile(control.path, JSON.stringify({ pipe, instance_id: control.instanceId, secret: control.secret }), { mode: 0o600 });
    return control;
  }

  private connect(socket: Socket): void {
    if (this.closed) { socket.destroy(); return; }
    this.sockets.add(socket);
    const reader = new FrameReader();
    let authenticated = false;
    socket.setTimeout(30_000, () => socket.destroy());
    socket.on("error", () => {});
    socket.on("close", () => this.sockets.delete(socket));
    socket.on("data", data => {
      try {
        for (const value of reader.push(data)) {
          const message = value as Record<string, unknown>;
          if (!message || typeof message !== "object" || typeof message.id !== "string") throw new Error("invalid request");
          try {
            if (!authenticated) {
              const candidate = Buffer.from(typeof message.secret === "string" ? message.secret : "");
              const expected = Buffer.from(this.secret);
              if (message.type !== "authenticate" || message.instance_id !== this.instanceId || candidate.length !== expected.length || !timingSafeEqual(candidate, expected)) throw new HubError("unauthorized", "connection authentication failed");
              authenticated = true;
              socket.write(encodeFrame({ id: message.id, ok: true, result: { authenticated: true } }));
            } else {
              const turnId = typeof message.turn_id === "string" ? message.turn_id : "";
              const envelope = message.envelope as HubCommandEnvelope;
              const authenticatedTurn = this.store.authenticateTurn(turnId, envelope);
              const result = this.service.handleTurnCommand(authenticatedTurn, envelope);
              socket.write(encodeFrame({ id: message.id, ok: true, result }));
            }
          } catch (error) {
            socket.write(encodeFrame({ id: message.id, ok: false, error: commandError(error) }));
            if (!authenticated) socket.end();
          }
        }
      } catch { socket.destroy(); }
    });
  }

  async close(): Promise<void> {
    this.closed = true;
    for (const socket of this.sockets) socket.destroy();
    await rm(this.path, { force: true });
  }
}
