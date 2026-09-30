import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawn } from "node:child_process";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import { HubRuntime } from "../src/hub/runtime.js";
import { controlCommand } from "../src/bridge/client.js";
import type { HubCommandEnvelope } from "../src/shared/types.js";

const envelope = (type: string, payload: Record<string, unknown> = {}): HubCommandEnvelope => ({
  protocol_version: 1,
  command_id: randomUUID(),
  issued_at: new Date().toISOString(),
  type,
  target: null,
  payload,
});

test("authenticated pipe grants only the active Holo Turn and receipts survive lost authority", async () => {
  const root = await mkdtemp(join(tmpdir(), "nirai-v2-control-"));
  const runtime = await HubRuntime.start(root);
  const connectionPath = join(root, "control", "connection.json");
  const connection = await readFile(connectionPath, "utf8");

  const create = () => {
    const task = runtime.store.createTask("holo");
    runtime.store.addMasterMessage(task.id, "work");
    const turn = runtime.store.reserveHoloTurn(task.id)!;
    return {
      task,
      turn,
      call: (command: HubCommandEnvelope) => controlCommand(connectionPath, turn.id, command),
    };
  };

  let bridge: ReturnType<typeof spawn> | undefined;
  try {
    const f = create();
    const receiptCommand = envelope("AwaitMasterReply");
    const receipt = await f.call(receiptCommand);
    assert.deepEqual(await f.call(receiptCommand), receipt);

    await assert.rejects(
      () => controlCommand(connectionPath, randomUUID(), envelope("AwaitMasterReply")),
      /unauthorized/,
    );

    const wrongPath = join(root, "wrong.json");
    await writeFile(wrongPath, JSON.stringify({ ...JSON.parse(connection), secret: "wrong" }));
    await assert.rejects(
      () => controlCommand(wrongPath, f.turn.id, envelope("AwaitMasterReply")),
      /authentication failed/,
    );

    await assert.rejects(
      () => f.call({ ...receiptCommand, payload: { other: true } }),
      /conflict/,
    );
    await assert.rejects(
      () => f.call(envelope("SendConversationMessage", { content: "no" })),
      /unsupported Holo/,
    );

    const expired = envelope("AwaitMasterReply");
    expired.issued_at = new Date(0).toISOString();
    await assert.rejects(() => f.call(expired), /expired/);

    bridge = spawn(process.execPath, [join(import.meta.dirname, "../src/bridge/mcp.js")], {
      env: { ...process.env, NIRAI_V2_DATA_ROOT: root },
      windowsHide: true,
      stdio: "pipe",
    });

    const replies = new Map<number, (message: any) => void>();
    let pending = "";
    bridge.stdout!.on("data", chunk => {
      pending += chunk.toString();
      let end;
      while ((end = pending.indexOf("\n")) >= 0) {
        const item = JSON.parse(pending.slice(0, end));
        pending = pending.slice(end + 1);
        replies.get(item.id)?.(item);
      }
    });

    let id = 0;
    const rpc = (method: string, params: unknown = {}) => new Promise<any>((resolve, reject) => {
      const current = ++id;
      const timer = setTimeout(() => reject(new Error("MCP reply missing")), 10_000);
      replies.set(current, message => {
        clearTimeout(timer);
        resolve(message);
      });
      bridge!.stdin!.write(`${JSON.stringify({ jsonrpc: "2.0", id: current, method, params })}\n`);
    });

    const initialized = await rpc("initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "test", version: "1" },
    });
    assert.equal(initialized.result.protocolVersion, "2025-06-18");
    const tools = (await rpc("tools/list")).result.tools;
    assert.deepEqual(tools.map((tool: any) => tool.name), ["nirai_command"]);
    assert.deepEqual(tools[0].inputSchema.required, ["turn_id", "envelope"]);
    assert.equal(JSON.stringify(tools[0]).includes("FinishResponse"), false);
    assert.equal(JSON.stringify(tools[0]).includes("SendConversationMessage"), false);
    assert.equal(JSON.stringify(tools[0]).includes("GetTaskContext"), false);
    assert.match(String(tools[0].description), /AwaitMasterReply/);
    assert.match(String(tools[0].description), /local: read/);
    assert.match(String(initialized.result.instructions), /WORLD_RULES/);
    assert.match(String(initialized.result.instructions), /CompleteTask/);
    assert.match(String(initialized.result.instructions), /AwaitMasterReply/);

    const result = await rpc("tools/call", {
      name: "nirai_command",
      arguments: {
        turn_id: f.turn.id,
        envelope: { command_id: randomUUID(), type: "AwaitMasterReply", payload: {} },
      },
    });
    assert.equal(JSON.parse(result.result.content[0].text).awaiting_master, true);

    runtime.store.bindConversation(f.task.id, "chatgpt", "routing-hint", "https://chatgpt.com/c/routing-hint");

    runtime.store.endHoloTurn(f.turn.id, "done");
    assert.deepEqual(await f.call(receiptCommand), receipt);
    await assert.rejects(() => f.call(envelope("AwaitMasterReply")), /stale|unauthorized/);

    const completing = create();
    const completion = await rpc("tools/call", {
      name: "nirai_command",
      arguments: {
        turn_id: completing.turn.id,
        envelope: { command_id: randomUUID(), type: "CompleteTask", payload: { result_summary: "疎通確認を完了" } },
      },
    });
    assert.equal(completion.result.isError, undefined);
    assert.equal(completion.result.structuredContent.completion_pending, true);
    assert.equal(completion.result.structuredContent.reply_required, true);
    assert.equal(runtime.store.getTask(completing.task.id)?.state, "Running");
    assert.equal(runtime.store.getHoloTurn(completing.turn.id)?.completion_summary, "疎通確認を完了");

    const finalReply = "接続確認OK";
    runtime.store.syncHoloTurn(completing.turn.id, finalReply, true);
    assert.equal(runtime.store.getTask(completing.task.id)?.state, "Completed");
    const completedMessages = (runtime.store.snapshot().messages as Array<{ turn_id: string | null; content: string }>)
      .filter(message => message.turn_id === completing.turn.id);
    assert.deepEqual(completedMessages.map(message => message.content), [finalReply]);

    const stale = await rpc("tools/call", {
      name: "nirai_command",
      arguments: {
        turn_id: completing.turn.id,
        envelope: { command_id: randomUUID(), type: "AwaitMasterReply", payload: {} },
      },
    });
    assert.equal(stale.result.isError, true);
    assert.match(stale.result.content[0].text, /stale|unauthorized/);

    const paused = create();
    runtime.store.pauseTask(paused.task.id);
    await assert.rejects(() => paused.call(envelope("AwaitMasterReply")), /stale|unauthorized/);

    const timed = create();
    const db = new DatabaseSync(join(root, "hub.sqlite3"));
    db.prepare("UPDATE holo_turns SET created_at=? WHERE id=?").run(new Date(0).toISOString(), timed.turn.id);
    db.close();
    await assert.rejects(() => timed.call(envelope("AwaitMasterReply")), /expired/);
  } finally {
    bridge?.stdin?.end();
    if (bridge) await new Promise<void>(resolve => bridge!.once("close", () => resolve()));
    await runtime.close();
    await rm(root, { recursive: true, force: true });
  }
});
