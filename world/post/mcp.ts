// 住人ひとりぶんの郵便受け（MCPの道具4つ）。入口（/mcp/<住人>）で差出人が決まる。

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { append, findLetter, type Letter, newLetterId, readAll, unfinished } from "./letters.ts";
import { resolveResident, settings } from "./settings.ts";

const RULES = readFileSync(new URL("./郵便の決まり.md", import.meta.url), "utf8");

// 作業場の名前は、D:\Products\Work の直下のフォルダー名になる。区切りや上へ戻る名前は受け付けない。
const WORK_NAME = /^(?!\.)[^\\/:*?"<>|\x00-\x1f]{1,64}$/;

function instructions(resident: string, residentsRoot: string): string {
  const persona = join(residentsRoot, resident, "persona.md");
  const self = existsSync(persona) ? readFileSync(persona, "utf8").trim() : "";
  return [RULES.trim(), `## あなた\n\nあなたは${resident}。`, self].filter(Boolean).join("\n\n");
}

function text(value: string) {
  return { content: [{ type: "text" as const, text: value }] };
}

function refuse(value: string) {
  return { ...text(value), isError: true };
}

/** onSent：手紙を出したあとに郵便局がすること（作業場を作る、すぐに見直す） */
export function createMailbox(resident: string, residentsRoot = settings.residentsRoot, onSent?: (letter: Letter) => void): McpServer {
  const server = new McpServer(
    { name: "nirai-post", version: "0.1.0" },
    { instructions: instructions(resident, residentsRoot) },
  );
  const mine = () => readAll(residentsRoot, resident);

  server.registerTool(
    "read_mailbox",
    {
      description: "まだ済んでいない手紙を、届いた順に、書き残しと一緒に読む。",
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async () => {
      const letters = unfinished(mine());
      if (letters.length === 0) return text("郵便受けは空。済んでいない手紙はない。");
      return text(JSON.stringify(letters, null, 2));
    },
  );

  server.registerTool(
    "send_letter",
    {
      description: "住人に手紙を出す。頼みごと、返事、知らせ、自分宛ての段取りも手紙。",
      inputSchema: {
        to: z.string().describe("宛先の住人（Holo・Codex・Claude）。自分宛てもよい"),
        body: z.string().min(1).describe("本文"),
        work: z.string().optional().describe("作業場の名前（D:\\Products\\Work の下のフォルダー名）"),
        reply_to: z.string().optional().describe("返事なら、元の手紙の番号"),
        based_on: z.string().optional().describe("何を見て書いたか"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    async ({ to, body, work, reply_to, based_on }) => {
      const receiver = resolveResident(to);
      if (!receiver) return refuse(`${to} には届けられない。宛先は ${settings.team.join("・")} のどれか。`);
      if (work !== undefined && !WORK_NAME.test(work)) return refuse(`作業場の名前「${work}」は使えない。フォルダー名1つだけにする。`);
      const letter: Letter = {
        kind: "letter", ts: new Date().toISOString(), id: newLetterId(), from: resident, to: receiver, body,
        ...(work ? { work } : {}), ...(reply_to ? { reply_to } : {}), ...(based_on ? { based_on } : {}),
      };
      append(residentsRoot, receiver, letter);
      onSent?.(letter);
      const place = work ? `作業場は ${settings.workRoot}\\${work}。` : "";
      return text(`${receiver} へ出した。手紙の番号は ${letter.id}。${place}`);
    },
  );

  server.registerTool(
    "write_note",
    {
      description: "自分の郵便受けの手紙に、やったことや見つけたことを書き残す。次に起きた自分が続きから始められる。",
      inputSchema: {
        letter: z.string().describe("手紙の番号"),
        body: z.string().min(1).describe("書き残すこと"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    async ({ letter, body }) => {
      if (!unfinished(mine()).some(l => l.id === letter)) return refuse(`${letter} は、済んでいない手紙の中にない。`);
      append(residentsRoot, resident, { kind: "note", ts: new Date().toISOString(), letter, body });
      return text("書き残した。");
    },
  );

  server.registerTool(
    "mark_done",
    {
      description: "自分の郵便受けの手紙に「済んだ」の印を付ける。付けるまで、手紙は何度でも届き直す。",
      inputSchema: {
        letter: z.string().describe("手紙の番号"),
        note: z.string().optional().describe("ひとこと（何をしたか、どこに残したか）"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ letter, note }) => {
      const lines = mine();
      if (!findLetter(lines, letter)) return refuse(`${letter} という手紙は、${resident} の郵便受けにない。`);
      if (!unfinished(lines).some(l => l.id === letter)) return text(`${letter} は、もう済んでいる。`);
      append(residentsRoot, resident, { kind: "done", ts: new Date().toISOString(), letter, ...(note ? { note } : {}) });
      return text(`${letter} に「済んだ」の印を付けた。`);
    },
  );

  return server;
}
