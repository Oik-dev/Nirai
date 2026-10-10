import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { Hands, LOOK_MAX_BYTES } from "./hands.ts";
import { createMailbox } from "./mcp.ts";

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl3uS8AAAAASUVORK5CYII=", "base64");

async function setup(resident = "Holo") {
  const root = mkdtempSync(join(tmpdir(), "nirai-look-"));
  const work = join(root, "work");
  const job = join(work, "job");
  const others = join(work, "others");
  mkdirSync(job, { recursive: true });
  mkdirSync(others);
  const hands = new Hands(join(root, "residents"), work, { waitMs: 1_000, limitMs: 2_000 }, () => {});
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
  await createMailbox(resident, join(root, "residents"), undefined, resident === "Holo" ? hands : undefined).connect(serverSide);
  const client = new Client({ name: "test", version: "0" });
  await client.connect(clientSide);
  const look = async (path: string, workName = "job") => client.callTool({ name: "look", arguments: { seat: 1, work: workName, path } });
  return { root, job, others, client, look };
}

test("lookは作業場内の本物の画像だけをMCP imageで返す", async () => {
  const p = await setup();
  writeFileSync(join(p.job, "test.png"), png);
  const result = await p.look("test.png");
  assert.equal(result.isError, undefined);
  assert.deepEqual(result.content, [
    { type: "text", text: `画像：test.png (${png.length} bytes, image/png)` },
    { type: "image", data: png.toString("base64"), mimeType: "image/png" },
  ]);
});

test("lookは作業場の外・リンクの先・画像以外・大きな画像を拒む", async () => {
  const p = await setup();
  const outside = join(p.others, "secret.png");
  writeFileSync(outside, png);
  writeFileSync(join(p.job, "text.png"), "秘密のテキスト");
  writeFileSync(join(p.job, "large.png"), Buffer.concat([png, Buffer.alloc(LOOK_MAX_BYTES)]));
  symlinkSync(p.others, join(p.job, "linked"), "junction");
  for (const path of ["../others/secret.png", outside, "linked/secret.png", "text.png", "large.png", "missing.png"]) {
    const result = await p.look(path);
    assert.equal(result.isError, true, `拒否すべき：${path}`);
    assert.match((result.content[0] as { text: string }).text, /絵を見せられなかった/);
    assert.equal(result.content.some((c: { type: string }) => c.type === "image"), false);
  }
});

test("lookはHoloにだけ貸す", async () => {
  const p = await setup("Codex");
  const tools = await p.client.listTools();
  assert.equal(tools.tools.some(tool => tool.name === "look"), false);
});
