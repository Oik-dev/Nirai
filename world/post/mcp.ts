// 住人ひとりぶんの郵便受け（MCPの道具）。入口（/mcp/<住人>[/<作業場>]）で差出人と筋が決まる。
// Holoは席（Room。ChatGPTの会話1つ。seats.ts）ごとに働くので、道具に席の札を添え、その席の作業場の筋だけを扱う。席を結ぶ道具もHoloだけ。
// 閉じた席の古い札では、郵便受けにも手にも触れない（2つの会話のHoloが同じ手紙を進めない）。
// 郵便局が手を貸す住人（Holo）には、作業の手（run・apply_patch・look。hands.ts）も足す。

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { Caller, Hands } from "./hands.ts";
import {
  append, findLetter, type Letter, type Line, MASTER, newLetterId, readAll, track, trackKey, tracksOf, unfinished, waitRefusal, waits,
} from "./letters.ts";
import { type Seat, seatOf, seatOfTag, seatsOf } from "./seats.ts";
import { resolveResident, settings } from "./settings.ts";
import { MESSENGER } from "./waker.ts";
import { ACTIVITIES } from '../window/body/activities.js';
import { appendBodyPastimes } from '../sea/body.ts';
import { minds } from '../sea/settings.ts';

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
 *  MCPのサーバー説明として渡す。説明を途中で切る脳（Claude Code）を郵便局が起こすときは、説明の代わりに全文を渡す（cli.ts の claudeCommand）。 */
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
  // 起こしたClaude（作業場の筋で起こされたClaude）は、全文を起こすときに受け取っている。説明にも載せると、切られた頭の2,048字を2回読む
  const wokenClaude = resident === "Claude" && scope !== undefined;
  const server = new McpServer(
    { name: "nirai-post", version: "0.1.0" },
    wokenClaude ? {} : { instructions: instructions(resident, residentsRoot) },
  );
  const seated = resident === MESSENGER;
  const count = settings.holo.seats;
  const roomField = z.string().min(1).describe("今いる部屋の札（郵便局が起こした一言や、Masterの最初の言葉の［Room 1-3］なら \"1-3\"）");
  const seatSchema = seated ? { room: roomField } : {};
  const team = () => settings.team.map(r => readAll(residentsRoot, r));

  /** 居場所を決める。Holoは札が今の席のものでなければ居場所にならない（refused に理由）。 */
  const place = (tag?: string): Place & { refused?: string } => {
    const lines = readAll(residentsRoot, resident);
    if (!seated) return { lines, work: scope, mine: scope === undefined ? lines : track(lines, scope) };
    const seat = seatOfTag(seatsOf(lines, count), tag ?? "");
    if (!seat) return { lines, refused: roomGone(tag) };
    return { lines, seat, work: seat.work, mine: seat.work ? track(lines, seat.work) : undefined };
  };
  const unbound = (seat?: Seat) => `Room ${seat?.seat}はまだ作業場に結ばれていない。Masterの言葉から${WORK_HINT}を決めて bind_room で結ぶ。`;

  server.registerTool(
    "read_mailbox",
    {
      description: seated
        ? "今いる部屋の作業場の、まだ済んでいない手紙を、届いた順に、書き残しと一緒に読む。"
        : "まだ済んでいない手紙を、届いた順に、書き残しと一緒に読む。",
      ...(seated ? { inputSchema: seatSchema } : {}),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ room }: { room?: string }) => {
      const here = place(room);
      if (here.refused) return refuse(here.refused);
      if (!here.mine) {
        const open = tracksOf(unfinished(here.lines));
        return text(unbound(here.seat) + "結んだら、もう一度 read_mailbox を読む。" + (open.length ? `済んでいない手紙のある作業場：${open.join("・")}` : ""));
      }
      const { seat, work, mine } = here;
      const waiting = waits(team());
      const letters = unfinished(mine).map(letter => waiting.has(letter.id) ? { ...letter, waiting_for: waiting.get(letter.id) } : letter);
      if (work !== undefined) {
        append(residentsRoot, resident, { kind: "read", ts: new Date().toISOString(), work, ...(seat ? { seat: seat.seat } : {}) });
      }
      return text(letters.length ? JSON.stringify(letters, null, 2) : "郵便受けは空。済んでいない手紙はない。");
    },
  );

  server.registerTool(
    "send_letter",
    {
      description: "住人に手紙を出す。頼みごと、返事、知らせ、自分宛ての段取りも手紙。",
      inputSchema: {
        ...seatSchema,
        to: z.string().describe(`宛先の住人（${settings.team.join("・")}）。自分宛てもよい`),
        body: z.string().min(1).describe("本文"),
        work: z.string().optional().describe(`${WORK_HINT}。省くと、返事なら元の手紙の、そうでなければ今いる作業場になる`),
        reply_to: z.string().optional().describe("返事なら、元の手紙の番号"),
        based_on: z.string().optional().describe("何を見て書いたか"),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    async ({ room, to, body, work, reply_to, based_on }) => {
      const receiver = resolveResident(to);
      if (!receiver) return refuse(`${to} には届けられない。宛先は ${settings.team.join("・")} のどれか。`);
      const here = place(room);
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
        "ほかの手紙が済むのやMasterの返事を待つなら waiting_for を付ける。Masterに聞く場合はbodyをそのまま問いとして書く。海の窓に確認チケットとして表示され、Masterからの返事の手紙で待ちが解ける。",
      inputSchema: {
        ...seatSchema,
        letter: z.string().describe("手紙の番号"),
        body: z.string().min(1).describe("書き残すこと"),
        waiting_for: z.string().optional().describe(`待つもの：済むのを待つ手紙の番号か、Masterの返事なら「${MASTER}」。Master待ちのbodyは海の窓へ表示する問いの全文`),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    async ({ room, letter, body, waiting_for }) => {
      const here = place(room);
      if (here.refused) return refuse(here.refused);
      if (!here.mine) return refuse(unbound(here.seat));
      if (!unfinished(here.mine).some(l => l.id === letter)) return refuse(`${letter} は、ここの済んでいない手紙にない。`);
      const target = waiting_for?.toLowerCase() === MASTER.toLowerCase() ? MASTER : waiting_for;
      if (target) {
        const why = waitRefusal(team(), letter, target);
        if (why) return refuse(why);
      }
      append(residentsRoot, resident, { kind: "note", ts: new Date().toISOString(), letter, body, ...(target ? { waiting_for: target } : {}) });
      if (target === MASTER) {
        return text(`書き残した。問いの本文は海の窓の確認チケットとして表示される。Masterが窓で答えると、原文のまま返事の手紙が届き、${letter} の待ちが解けて起こされる。部屋で回答を受けた場合は、その言葉を原文のままwaiting_forなしの書き残しに写して待ちを解く。`);
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
    async ({ room, letter, note }) => {
      const here = place(room);
      if (here.refused) return refuse(here.refused);
      if (!here.mine) return refuse(unbound(here.seat));
      if (!findLetter(here.mine, letter)) return refuse(`${letter} は、ここの郵便受けにない。`);
      if (!unfinished(here.mine).some(l => l.id === letter)) return text(`${letter} は、もう済んでいる。`);
      append(residentsRoot, resident, { kind: "done", ts: new Date().toISOString(), letter, ...(note ? { note } : {}) });
      return text(`${letter} に「済んだ」の印を付けた。`);
    },
  );

  server.registerTool(
    'set_pastimes',
    {
      description: '仕事で起きているついでに、気が向いたら自分の好きな過ごし方を残す。',
      inputSchema: { ...seatSchema,
        activities: z.array(z.string()).min(1).max(32).describe('好きな活動の名前の並び（1つ以上）') },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    async ({ room, activities }) => {
      const here = place(room);
      if (here.refused) return refuse(here.refused);
      if (seated && !here.mine) return refuse(unbound(here.seat));
      const available = Object.keys(ACTIVITIES).filter(name => name !== '窓辺にいる');
      if (Object.hasOwn(minds, resident)) return refuse('精神のある住人は自分で活動を選びます。');
      if (activities.some(name => !available.includes(name))) {
        return refuse(`使えない活動があります。選べる名前: ${available.join('・')}`);
      }
      try {
        await appendBodyPastimes(join(residentsRoot, resident), activities);
        return text('好きな過ごし方を体の記録に残しました。');
      } catch (error) {
        return refuse((error as Error).message);
      }
    },
  );

  if (seated) lendRooms(server, residentsRoot, roomField, place);
  if (hands) lendHands(server, resident, hands, seated ? place : undefined, seatSchema);
  return server;
}

/** 閉じた席の古い札（または読めない札）で呼ばれたとき。今の札は教えない（教えると、古い会話のHoloが新しい部屋で働いてしまう）。 */
const roomGone = (tag?: string) => `札「${tag ?? ""}」の部屋は開いていない（閉じた部屋か、札の書き違い）。` +
  "札は、郵便局が起こした一言かMasterの最初の言葉の［Room 1-3］の「1-3」。閉じた部屋の会話では、郵便受けにも手にも触らない（続きは郵便局が開く部屋で進む）。";

/** Holoだけの道具：部屋を作業場に結ぶ・結び直す。 */
function lendRooms(
  server: McpServer, residentsRoot: string, roomField: z.ZodTypeAny, place: (tag?: string) => Place & { refused?: string },
): void {
  server.registerTool(
    "bind_room",
    {
      description: `今いる部屋を、${WORK_HINT}に結ぶ。Masterと別の作業場の話に移るときも結び直す（会話はそのまま続く）。1つの作業場に部屋は1つ。結んだら read_mailbox で読む。`,
      inputSchema: { room: roomField, work: z.string().describe(WORK_HINT) },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ room, work }) => {
      const here = place(room);
      if (here.refused) return refuse(here.refused);
      const seat = here.seat!;
      if (seat.work && trackKey(seat.work) === trackKey(work)) return text(`Room ${seat.seat}はもう ${seat.work} に結ばれている。read_mailbox で読む。`);
      if (!validWork(work)) return refuse(`作業場の名前「${work}」は使えない。フォルダー名1つだけにする。`);
      const other = seatOf(seatsOf(here.lines, settings.holo.seats), work);
      if (other) return refuse(`${other.work} はRoom ${other.seat}で開いている。1つの作業場に部屋は1つなので、MasterにRoom ${other.seat}へ移ってもらう。`);
      append(residentsRoot, MESSENGER, { kind: "seat", ts: new Date().toISOString(), seat: seat.seat, event: "bind", work });
      return text(seat.work
        ? `Room ${seat.seat}を ${seat.work} から ${work} に結び直した。${seat.work} の手紙は、郵便局が別の部屋で進める。read_mailbox で読む。`
        : `Room ${seat.seat}を ${work} に結んだ。read_mailbox で読む。`);
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

/** placeOf：札から今の席（Holoだけ）。手の記録に席を残し、長いコマンドの結果をその席の作業場へ届ける。閉じた席の札では手を貸さない。 */
function lendHands(
  server: McpServer, resident: string, hands: Hands, placeOf?: (tag?: string) => Place & { refused?: string },
  seatSchema: Record<string, z.ZodTypeAny> = {},
): void {
  const work = z.string().describe("作業場の名前（D:\\Products\\Work の下のフォルダー名。手紙の work と同じ）");
  const caller = (tag?: string): Caller | undefined => {
    if (!placeOf) return undefined;
    const here = placeOf(tag);
    if (here.refused) throw new Error(here.refused);
    return { seat: here.seat!.seat, ...(here.work ? { work: here.work } : {}) };
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
    async ({ room, work, command }) => {
      try {
        return text(await hands.run(resident, work, command, caller(room)));
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
    async ({ room, work, patch }) => {
      try {
        return text(`当てた。\n${hands.patch(resident, work, patch, caller(room)).join("\n")}`);
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
    async ({ room, work, path }) => {
      try {
        const picture = hands.look(resident, work, path, caller(room));
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
