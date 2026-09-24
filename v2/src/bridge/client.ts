import { readFile } from "node:fs/promises";
import { createConnection } from "node:net";
import { randomUUID } from "node:crypto";
import { FrameReader, encodeFrame } from "../shared/framing.js";
import type { HubCommandEnvelope, CommandResult } from "../shared/types.js";
import type { ControlConnection } from "../hub/control.js";

// Transport only: no authority, execution, durable queue, or automatic resubmission.
// A caller that loses a reply must keep the same envelope/command_id when retrying.
export async function controlCommand(connectionPath: string, turnId: string, envelope: HubCommandEnvelope): Promise<CommandResult> {
  const connection = JSON.parse(await readFile(connectionPath, "utf8")) as ControlConnection;
  if (typeof connection.pipe !== "string" || !connection.pipe.startsWith("\\\\.\\pipe\\nirai-v2-hub-")) throw new Error("invalid Hub connection file");
  return new Promise((resolve, reject) => {
    const socket = createConnection(connection.pipe);
    const reader = new FrameReader();
    const authId = randomUUID(), requestId = randomUUID();
    let settled = false;
    const finish = (error?: Error, value?: CommandResult) => {
      if (settled) return;
      settled = true; socket.destroy();
      if (error) reject(error); else resolve(value!);
    };
    socket.setTimeout(15_000, () => finish(new Error("Hub reply timed out; retain command_id for reconciliation")));
    socket.on("error", () => finish(new Error("Hub connection unavailable; retain command_id for reconciliation")));
    socket.on("close", () => finish(new Error("Hub disconnected; result may already be saved, retain command_id")));
    socket.on("connect", () => socket.write(encodeFrame({ id: authId, type: "authenticate", ...connection })));
    socket.on("data", chunk => {
      try {
        for (const item of reader.push(chunk)) {
          const message = item as { id: string; ok: boolean; result: CommandResult; error?: { code: string; message: string } };
          if (message.id !== authId && message.id !== requestId) throw new Error("invalid Hub response");
          if (!message.ok) { finish(new Error(`${message.error?.code}: ${message.error?.message}`)); return; }
          if (message.id === authId) socket.write(encodeFrame({ id: requestId, turn_id: turnId, envelope }));
          else finish(undefined, message.result);
        }
      } catch { finish(new Error("invalid Hub reply")); }
    });
  });
}
