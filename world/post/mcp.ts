// 住人ひとりぶんの郵便受け（MCPの道具4つ）。入口（/mcp/<住人>）で差出人が決まる。
// 郵便局が手を貸す住人（Holo）には、手の道具2つ（run・apply_patch。hands.ts）も足す。

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { Hands } from "./hands.ts";
import { append, findLetter, type Letter, newLetterId, readAll, unfinished } from "./letters.ts";
import { resolveResident, settings } from "./settings.ts";

const RULES = new URL("./郵便の決まり.md", import.meta.url);

// 作業場の名前は、D:\Products\Work の直下のフォルダー名になる。区切りや上へ戻る名前は受け付けない。
const WORK_NAME = /^(?!\.)[^\\/:*?"<>|\x00-\x1f]{1,64}$/;

/** 決まりの「## <住人>だけ」の節は、その住人にだけ渡す。ほかの住人は読み直さずに済む。 */
export function rulesFor(resident: string, rules: string): string {
  return rules.trim().split(/\n(?=## )/).filter((section) => {
    const only = /^## (.+)だけ$/.exec(section.split("\n", 1)[0].trim());
    return !only || only[1] === resident;
  }).join("\n").trim();
}

/** 決まりと人格は、つなぐたびに読む（書き換えても、郵便局を起こし直さなくてよい）。 */
function instructions(resident: string, residentsRoot: string): string {
  const persona = join(residentsRoot, resident, "persona.md");
  const self = existsSync(persona) ? readFileSync(persona, "utf8").trim() : "";
  return [rulesFor(resident, readFileSync(RULES, "utf8")), `## あなた\n\nあなたは${resident}。`, self].filter(Boolean).join("\n\n");
}

function text(value: string) {
  return { content: [{ type: "text" as const, text: value }] };
}

function refuse(value: string) {
  return { ...text(value), isError: true };
}

/** onSent：手紙を出したあとに郵便局がすること（作業場を作る、すぐに見直す）。hands：この住人に貸す手 */
export function createMailbox(
  resident: string, residentsRoot = settings.residentsRoot, onSent?: (letter: Letter) => void, hands?: Hands,
): McpServer {
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
        work: z.string().optional().describe("作業場の名前（D:\\Products\\Work の下のフォルダー名）。返事では、省くと元の手紙の作業場を引き継ぐ"),
        reply_to: z.string().optional().describe("返事なら、元の手紙の番号"),
        based_on: z.string().optional().describe("何を見て書いたか"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    async ({ to, body, work, reply_to, based_on }) => {
      const receiver = resolveResident(to);
      if (!receiver) return refuse(`${to} には届けられない。宛先は ${settings.team.join("・")} のどれか。`);
      const myLines = reply_to ? readAll(residentsRoot, resident) : [];
      const replied = reply_to ? findLetter(myLines, reply_to) : undefined;
      const effectiveWork = work ?? replied?.work;
      if (effectiveWork !== undefined && !WORK_NAME.test(effectiveWork)) {
        return refuse(`作業場の名前「${effectiveWork}」は使えない。フォルダー名1つだけにする。`);
      }
      const letter: Letter = {
        kind: "letter", ts: new Date().toISOString(), id: newLetterId(), from: resident, to: receiver, body,
        ...(effectiveWork ? { work: effectiveWork } : {}), ...(reply_to ? { reply_to } : {}), ...(based_on ? { based_on } : {}),
      };
      append(residentsRoot, receiver, letter);
      onSent?.(letter);
      const place = effectiveWork ? `作業場は ${settings.workRoot}\\${effectiveWork}。` : "";
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
      const target = findLetter(lines, letter);
      if (!target) return refuse(`${letter} という手紙は、${resident} の郵便受けにない。`);
      if (!unfinished(lines).some(l => l.id === letter)) return text(`${letter} は、もう済んでいる。`);
      append(residentsRoot, resident, { kind: "done", ts: new Date().toISOString(), letter, ...(note ? { note } : {}) });
      return text(`${letter} に「済んだ」の印を付けた。`);
    },
  );

  if (hands) lendHands(server, resident, hands);
  return server;
}

const PATCH_EXAMPLE = `*** Begin Patch
*** Add File: notes/hello.md
+# こんにちは
*** Update File: src/app.ts
@@ function main() {
-  console.log("old");
+  console.log("new");
*** Delete File: old.txt
*** End Patch`;

function lendHands(server: McpServer, resident: string, hands: Hands): void {
  const work = z.string().describe("作業場の名前（D:\\Products\\Work の下のフォルダー名。手紙の work と同じ）");
  // 承認は置かない（要件§14）。ChatGPTの確認ボタンで止まると留守の間に進まないので、壊す道具としては知らせない
  const annotations = { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false };
  const waitSec = Math.round(settings.hands.waitMs / 1000);
  const limitMin = Math.round(settings.hands.limitMs / 60_000);

  server.registerTool(
    "run",
    {
      description: `作業場をカレントフォルダーにして、PowerShell 7 のコマンドを実行する。ファイルを読む・探す（rg）・一覧・テスト・git もこれで。` +
        `${waitSec}秒で終わらなければ「続いている」と返し、終わったら結果を郵便局からの手紙で届ける。${limitMin}分たっても終わらなければ止める。` +
        "出力が長いと途中を省くので、全部要るときはファイルに書き出して少しずつ読む。",
      inputSchema: { work, command: z.string().min(1).describe("PowerShell 7 のコマンド") },
      annotations,
    },
    async ({ work, command }) => {
      try {
        return text(await hands.run(resident, work, command));
      } catch (error) {
        return refuse((error as Error).message);
      }
    },
  );

  server.registerTool(
    "apply_patch",
    {
      description: "作業場のファイルを、Codexの apply_patch の形の差分で作る・書き換える・消す。パスは作業場からの相対。" +
        "書き換えは、変える行の前後3行ほどを空白付きの文脈行で添え、場所が紛れるときは @@ に関数やクラスの行を書く。" +
        `1か所でも当たらなければ、どのファイルも変えない。例：\n${PATCH_EXAMPLE}`,
      inputSchema: { work, patch: z.string().min(1).describe("*** Begin Patch の行で始まり、*** End Patch の行で終わる差分") },
      annotations,
    },
    async ({ work, patch }) => {
      try {
        return text(`当てた。\n${hands.patch(resident, work, patch).join("\n")}`);
      } catch (error) {
        return refuse(`当てられなかった。${(error as Error).message}`);
      }
    },
  );
}
