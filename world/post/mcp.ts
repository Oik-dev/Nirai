// 住人ひとりぶんの郵便受け（MCPの道具）。入口（/mcp/<住人>[/<作業場>]）で差出人と筋が決まる。
// Holoは席（ChatGPTの会話1つ。seats.ts）ごとに働くので、道具に席の番号を添え、その席の作業場の筋だけを扱う。席を結ぶ・閉じる道具もHoloだけ。
// 郵便局が手を貸す住人（Holo）には、作業の手（run・apply_patch・look。hands.ts）も足す。

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { Caller, Hands } from "./hands.ts";
import {
  append, findLetter, type Letter, type Line, MASTER, newLetterId, readAll, track, trackKey, tracksOf, unfinished, waitRefusal, waits,
} from "./letters.ts";
import { closeLetter, firstRead, lastHandover, type Seat, seatOf, seatsOf } from "./seats.ts";
import { resolveResident, settings } from "./settings.ts";
import { MESSENGER } from "./waker.ts";

const RULES = new URL("./郵便の決まり.md", import.meta.url);

// 作業場の名前は、D:\Products\Work の直下のフォルダー名になる。区切りや上へ戻る名前は受け付けない。
const WORK_NAME = /^(?!\.)[^\\/:*?"<>|\x00-\x1f]{1,64}$/;
export const validWork = (work: string) => WORK_NAME.test(work) && work !== "受付";

/** 決まりの「## <住人>だけ」の節は、その住人にだけ渡す。ほかの住人は読み直さずに済む。 */
export function rulesFor(resident: string, rules: string): string {
  return rules.trim().split(/\n(?=## )/).filter((section) => {
    const only = /^## (.+)だけ$/.exec(section.split("\n", 1)[0].trim());
    return !only || only[1] === resident;
  }).join("\n").trim();
}

/** 決まりと人格は、つなぐたびに読む（書き換えても、郵便局を起こし直さなくてよい）。
 *  MCPのサーバー説明として渡す。説明を途中で切る脳（Claude Code）には、起こすときに全文も渡す（cli.ts の claudeCommand）。 */
export function instructions(resident: string, residentsRoot: string): string {
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

const WORK_HINT = `作業場（${settings.workRoot} の下のフォルダー名。プロジェクトごとに1つ、例：nirai）`;

/** 今の居場所。lines は住人の生ログ全部、mine はこの居場所で扱う筋（席がまだ結ばれていなければ undefined）。 */
type Place = { lines: Line[]; seat?: Seat; work?: string; mine?: Line[] };

/** onSent：手紙を出したあとに郵便局がすること（作業場を作る、すぐに見直す）。hands：この住人に貸す手。
 *  scope：CLIの住人がこの作業場の筋だけで起こされたとき、その作業場 */
export function createMailbox(
  resident: string, residentsRoot = settings.residentsRoot, onSent?: (letter: Letter) => void, hands?: Hands, scope?: string,
): McpServer {
  const server = new McpServer(
    { name: "nirai-post", version: "0.1.0" },
    { instructions: instructions(resident, residentsRoot) },
  );
  const seated = resident === MESSENGER;
  const count = settings.holo.seats;
  const seatField = z.number().int().min(1).max(count).describe("今いる席の番号（起こされた一言や、Masterの最初の言葉の［席k］）");
  const seatSchema = seated ? { seat: seatField } : {};
  const team = () => settings.team.map(r => readAll(residentsRoot, r));

  /** 居場所を決める。Holoの空いた席は居場所にならない（refused に理由）。 */
  const place = (n?: number): Place & { refused?: string } => {
    const lines = readAll(residentsRoot, resident);
    if (!seated) return { lines, work: scope, mine: scope === undefined ? lines : track(lines, scope) };
    const seat = seatsOf(lines, count).find(s => s.seat === n);
    if (!seat?.since) return { lines, refused: `席${n}は空いている。Masterが入った席か、郵便局に起こされた席でだけ郵便受けを使う。` };
    return { lines, seat, work: seat.work, mine: seat.work ? track(lines, seat.work) : undefined };
  };
  const unbound = (n: number) => `席${n}はまだ作業場に結ばれていない。Masterの言葉から${WORK_HINT}を決めて bind_seat で結ぶ。`;

  server.registerTool(
    "read_mailbox",
    {
      description: seated
        ? "今いる席の作業場の、まだ済んでいない手紙を、届いた順に、書き残しと一緒に読む。席に入って最初に読むときは、前の席からの引き継ぎも添える。"
        : "まだ済んでいない手紙を、届いた順に、書き残しと一緒に読む。",
      ...(seated ? { inputSchema: seatSchema } : {}),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ seat: n }: { seat?: number }) => {
      const here = place(n);
      if (here.refused) return refuse(here.refused);
      if (!here.mine) {
        const open = tracksOf(unfinished(here.lines));
        return text(unbound(n!) + "結んだら、もう一度 read_mailbox を読む。" + (open.length ? `済んでいない手紙のある作業場：${open.join("・")}` : ""));
      }
      const { seat, work, mine } = here;
      const closing = seat ? closeLetter(mine, seat) : undefined;
      const waiting = waits(team());
      const letters = unfinished(mine)
        .filter(letter => !closing || letter.id === closing.id)
        .map(letter => waiting.has(letter.id) ? { ...letter, waiting_for: waiting.get(letter.id) } : letter);
      const handover = seat && work && firstRead(here.lines, seat) ? lastHandover(here.lines, work) : undefined;
      if (work !== undefined) {
        append(residentsRoot, resident, { kind: "read", ts: new Date().toISOString(), work, ...(seat ? { seat: seat.seat } : {}) });
      }
      const parts = [
        handover ? `前の席からの引き継ぎ（${handover.ts}）：\n${handover.handover}` : "",
        letters.length ? JSON.stringify(letters, null, 2) : "郵便受けは空。済んでいない手紙はない。",
      ];
      return text(parts.filter(Boolean).join("\n\n"));
    },
  );

  server.registerTool(
    "send_letter",
    {
      description: "住人に手紙を出す。頼みごと、返事、知らせ、自分宛ての段取りも手紙。",
      inputSchema: {
        ...seatSchema,
        to: z.string().describe("宛先の住人（Holo・Codex・Claude）。自分宛てもよい"),
        body: z.string().min(1).describe("本文"),
        work: z.string().optional().describe(`${WORK_HINT}。省くと、返事なら元の手紙の、そうでなければ今いる作業場になる`),
        reply_to: z.string().optional().describe("返事なら、元の手紙の番号"),
        based_on: z.string().optional().describe("何を見て書いたか"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    async ({ seat: n, to, body, work, reply_to, based_on }) => {
      const receiver = resolveResident(to);
      if (!receiver) return refuse(`${to} には届けられない。宛先は ${settings.team.join("・")} のどれか。`);
      const here = place(n);
      if (here.refused) return refuse(here.refused);
      const replied = reply_to ? findLetter(here.mine ?? here.lines, reply_to) : undefined;
      if (reply_to && !replied) return refuse(`返事の手紙 ${reply_to} は、ここの郵便受けにない。`);
      const destination = work ?? replied?.work ?? here.work;
      if (destination === undefined) return refuse(`手紙には work に${WORK_HINT}を付ける。`);
      if (!validWork(destination)) return refuse(`作業場の名前「${destination}」は使えない。フォルダー名1つだけにする。`);
      const letter: Letter = {
        kind: "letter", ts: new Date().toISOString(), id: newLetterId(), from: resident, to: receiver, body, work: destination,
        ...(reply_to ? { reply_to } : {}), ...(based_on ? { based_on } : {}),
      };
      append(residentsRoot, receiver, letter);
      onSent?.(letter);
      return text(`${receiver} へ出した。手紙の番号は ${letter.id}。作業場は ${settings.workRoot}\\${destination}。`);
    },
  );

  server.registerTool(
    "write_note",
    {
      description: "自分の郵便受けの手紙に、やったことや見つけたことを書き残す。次に起きた自分が続きから始められる。" +
        "ほかの手紙が済むのやMasterの返事を待つなら waiting_for を付ける。待っている間、その手紙では起こされない。",
      inputSchema: {
        ...seatSchema,
        letter: z.string().describe("手紙の番号"),
        body: z.string().min(1).describe("書き残すこと"),
        waiting_for: z.string().optional().describe(`待つもの：済むのを待つ手紙の番号か、Masterの返事なら「${MASTER}」`),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    async ({ seat: n, letter, body, waiting_for }) => {
      const here = place(n);
      if (here.refused) return refuse(here.refused);
      if (!here.mine) return refuse(unbound(n!));
      if (!unfinished(here.mine).some(l => l.id === letter)) return refuse(`${letter} は、ここの済んでいない手紙にない。`);
      const target = waiting_for?.toLowerCase() === MASTER.toLowerCase() ? MASTER : waiting_for;
      if (target) {
        const why = waitRefusal(team(), letter, target);
        if (why) return refuse(why);
      }
      append(residentsRoot, resident, { kind: "note", ts: new Date().toISOString(), letter, body, ...(target ? { waiting_for: target } : {}) });
      if (target === MASTER) {
        return text(`書き残した。Masterの返事を待つ間、${letter} では起こさない。Masterには${seated ? "この席で" : "知らせる手紙で"}聞く。` +
          "返事が来たら、waiting_for を付けずに書き残すと待ちが解ける。");
      }
      if (target) return text(`書き残した。${target} が済むまで、${letter} では起こさない。済んだら、また起こされる。`);
      return text("書き残した。");
    },
  );

  server.registerTool(
    "mark_done",
    {
      description: "自分の郵便受けの手紙に「済んだ」の印を付ける。頼まれたことが本当に終わったときだけ付ける（待つときは write_note の waiting_for）。" +
        "付けるまで、手紙は何度でも届き直す。",
      inputSchema: {
        ...seatSchema,
        letter: z.string().describe("手紙の番号"),
        note: z.string().optional().describe("ひとこと（何をしたか、どこに残したか）"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ seat: n, letter, note }) => {
      const here = place(n);
      if (here.refused) return refuse(here.refused);
      if (!here.mine) return refuse(unbound(n!));
      const target = findLetter(here.mine, letter);
      if (!target) return refuse(`${letter} は、ここの郵便受けにない。`);
      if (target.close !== undefined) return refuse("席を閉じてほしい手紙は、leave_seat で席を閉じると済む。");
      if (!unfinished(here.mine).some(l => l.id === letter)) return text(`${letter} は、もう済んでいる。`);
      append(residentsRoot, resident, { kind: "done", ts: new Date().toISOString(), letter, ...(note ? { note } : {}) });
      return text(`${letter} に「済んだ」の印を付けた。`);
    },
  );

  if (seated) lendSeats(server, residentsRoot, seatField);
  if (hands) lendHands(server, resident, hands, seated ? (n: number) => place(n).seat : undefined, seatSchema);
  return server;
}

/** Holoだけの道具：席を作業場に結ぶ・席を閉じる。 */
function lendSeats(server: McpServer, residentsRoot: string, seatField: z.ZodTypeAny): void {
  const count = settings.holo.seats;
  const now = () => {
    const lines = readAll(residentsRoot, MESSENGER);
    return { lines, seats: seatsOf(lines, count) };
  };
  const annotations = { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false };

  server.registerTool(
    "bind_seat",
    {
      description: `Masterが入ったばかりの席を、${WORK_HINT}に結ぶ。1つの作業場に席は1つ。結んだら read_mailbox で読む。`,
      inputSchema: { seat: seatField, work: z.string().describe(WORK_HINT) },
      annotations,
    },
    async ({ seat: n, work }) => {
      const { seats } = now();
      const seat = seats.find(s => s.seat === n)!;
      if (!seat.since) return refuse(`席${n}は空いている。`);
      if (seat.work) {
        if (trackKey(seat.work) === trackKey(work)) return text(`席${n}はもう ${seat.work} に結ばれている。read_mailbox で読む。`);
        return refuse(`席${n}は ${seat.work} に結ばれている。別の作業場へ移るなら、leave_seat に handover と next_work を付けて閉じる。`);
      }
      if (!validWork(work)) return refuse(`作業場の名前「${work}」は使えない。フォルダー名1つだけにする。`);
      const other = seatOf(seats, work);
      if (other) return refuse(`${other.work} は席${other.seat}で開いている。1つの作業場に席は1つなので、Masterに席${other.seat}へ移ってもらう（拡張の「行く」）。`);
      append(residentsRoot, MESSENGER, { kind: "seat", ts: new Date().toISOString(), seat: n, event: "bind", work });
      return text(`席${n}を ${work} に結んだ。read_mailbox で読む。`);
    },
  );

  server.registerTool(
    "leave_seat",
    {
      description: "今いる席を閉じる。作業場に結ばれた席なら、次にこの作業場で起きるHoloが続けるのに要ることを handover に書く。" +
        "同じ会話のまま別の作業場へ移るなら next_work も付ける。閉じたあとは、この会話では郵便受けに触らない（移ったなら、移った先で読む）。",
      inputSchema: {
        seat: seatField,
        handover: z.string().optional().describe("引き継ぎ（Masterと話している途中のこと、決まったこと、Masterの好み、進めていた仕事の今）"),
        next_work: z.string().optional().describe("同じ会話のまま移る先の作業場"),
      },
      annotations,
    },
    async ({ seat: n, handover, next_work }) => {
      const { lines, seats } = now();
      const seat = seats.find(s => s.seat === n)!;
      if (!seat.since) return refuse(`席${n}はもう空いている。`);
      const at = new Date();
      if (!seat.work) {
        if (next_work !== undefined) return refuse("まだ結ばれていない席は、閉じずに bind_seat で作業場に結ぶ。");
        append(residentsRoot, MESSENGER, { kind: "seat", ts: at.toISOString(), seat: n, event: "close" });
        return text(`席${n}を空けた。この会話では、もう郵便受けに触らない。`);
      }
      if (!handover?.trim()) return refuse("handover に引き継ぎを書く（次にこの作業場で起きるHoloが続けるのに要ること）。");
      if (next_work !== undefined) {
        if (!validWork(next_work)) return refuse(`作業場の名前「${next_work}」は使えない。フォルダー名1つだけにする。`);
        if (trackKey(next_work) === trackKey(seat.work)) return refuse(`席${n}はもう ${seat.work} の席。`);
        const other = seatOf(seats, next_work);
        if (other) return refuse(`${other.work} は席${other.seat}で開いている。Masterに席${other.seat}へ移ってもらう（拡張の「行く」）。`);
      }
      const ts = at.toISOString();
      append(residentsRoot, MESSENGER, { kind: "seat", ts, seat: n, event: "close", work: seat.work, handover });
      // 席を閉じてほしい手紙は、閉じたことで済む（残すと、次の席が起こされる）
      for (const letter of unfinished(track(lines, seat.work)).filter(l => l.close === n)) {
        append(residentsRoot, MESSENGER, { kind: "done", ts, letter: letter.id, note: "席を閉じた" });
      }
      if (next_work === undefined) return text(`席${n}を閉じた。この会話では、もう郵便受けに触らない。`);
      append(residentsRoot, MESSENGER, {
        kind: "seat", ts: new Date(at.getTime() + 1).toISOString(), seat: n, event: "open", work: next_work,
        ...(seat.url ? { url: seat.url } : {}), ...(seat.started ? { started: seat.started } : {}),
      });
      return text(`席${n}を閉じて、同じ会話のまま ${next_work} に結び直した。read_mailbox で読む。`);
    },
  );
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

/** seatOf：席の番号から今の席（Holoだけ）。手の記録に席を残し、長いコマンドの結果をその席の作業場へ届ける。 */
function lendHands(
  server: McpServer, resident: string, hands: Hands, seatOf?: (n: number) => Seat | undefined,
  seatSchema: Record<string, z.ZodTypeAny> = {},
): void {
  const work = z.string().describe("作業場の名前（D:\\Products\\Work の下のフォルダー名。手紙の work と同じ）");
  const caller = (n?: number): Caller | undefined => {
    if (!seatOf || n === undefined) return undefined;
    const seat = seatOf(n);
    return { seat: n, ...(seat?.work ? { work: seat.work } : {}) };
  };
  // 任意コマンドとファイルの削除を含む手には、実際の権限を申告する。
  // annotationsは安全境界ではない。手元の権限と作業場のルールは別に守る。
  const annotations = { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false };
  const waitSec = Math.round(settings.hands.waitMs / 1000);
  const limitMin = Math.round(settings.hands.limitMs / 60_000);

  server.registerTool(
    "run",
    {
      description: `作業場からPowerShell 7を起動する。実行範囲はWindowsユーザーの権限に従い、作業場内に制限されない。ファイルを読む・探す（rg）・一覧・テスト・git もこれで。` +
        `${waitSec}秒で終わらなければ「続いている」と返し、終わったら結果を郵便局からの手紙で届ける。${limitMin}分たっても終わらなければ止める。` +
        "出力が長いと途中を省くので、全部要るときはファイルに書き出して少しずつ読む。",
      inputSchema: { ...seatSchema, work, command: z.string().min(1).describe("PowerShell 7 のコマンド") },
      annotations: { ...annotations, openWorldHint: true },
    },
    async ({ seat, work, command }) => {
      try {
        return text(await hands.run(resident, work, command, caller(seat)));
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
      inputSchema: { ...seatSchema, work, patch: z.string().min(1).describe("*** Begin Patch の行で始まり、*** End Patch の行で終わる差分") },
      annotations,
    },
    async ({ seat, work, patch }) => {
      try {
        return text(`当てた。\n${hands.patch(resident, work, patch, caller(seat)).join("\n")}`);
      } catch (error) {
        return refuse(`当てられなかった。${(error as Error).message}`);
      }
    },
  );

  server.registerTool(
    "look",
    {
      description: "指定した作業場の中のPNG・JPEG・WebP画像を、MCPの画像と短い文字で返す。画像は3MiB以下。画像が実際に見えたかは別に答え合わせする。",
      inputSchema: {
        ...seatSchema,
        work,
        path: z.string().min(1).describe("指定した作業場からの相対パス（例：checks/pose.png）。作業場の外には出られない"),
      },
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ seat, work, path }) => {
      try {
        const picture = hands.look(resident, work, path, caller(seat));
        return {
          content: [
            { type: "text" as const, text: `画像：${picture.name} (${picture.bytes} bytes, ${picture.mimeType})` },
            { type: "image" as const, data: picture.data, mimeType: picture.mimeType },
          ],
        };
      } catch (error) {
        return refuse(`絵を見せられなかった：${(error as Error).message}`);
      }
    },
  );
}
