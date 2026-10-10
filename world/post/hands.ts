// 郵便局が貸す手。Holoの脳はChatGPTの中にあり、Masterの手元のファイルにもコマンドにも届かない。
// 手は、GPTが慣れているCodexの2つ（コマンドと apply_patch）にそろえる。読む・探す・一覧は、コマンドでできる。
// 手が届くのは作業場（D:\Products\Work\<名前>）。作業場は、名前を付けた手紙を出すと郵便局が作る。
// やったことは、その住人の生ログ（lifelog/hands/<日本時間の日付>.jsonl）に残す。

import { spawn } from "node:child_process";
import { appendFileSync, existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { applyDiff } from "@openai/agents-core";
import { killTree } from "./cli.ts";
import { append, JST_DAY, type Letter, newLetterId, trackKey } from "./letters.ts";
import { POST_OFFICE } from "./waker.ts";
import { workPath } from "./work.ts";

/** 手を呼んだHoloの席と、その席の作業場。長いコマンドの結果の手紙は、その作業場へ届ける */
export type Caller = { seat: number; work?: string };

export type HandsSettings = {
  /** これより長いコマンドは「続いている」と返し、結果は手紙で届ける */
  waitMs: number;
  /** これを過ぎても終わらないコマンドは止める */
  limitMs: number;
};

// 出力の受け渡しをUTF-8にする（日本語版のWindowsの既定はShift-JIS）。色や進み具合の表示は出さない
const PRELUDE = [
  "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)",
  "$OutputEncoding = [Console]::OutputEncoding",
  "$PSStyle.OutputRendering = 'PlainText'",
  "$ProgressPreference = 'SilentlyContinue'",
].join("\n");

// MCPに画像を返す上限。Cloudへ渡すものは作業場の絵だけ（イデアと生ログは対象外）。
export const LOOK_MAX_BYTES = 3 * 1024 * 1024;
const LOOK_TYPES: Record<string, string> = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp" };

function imageType(name: string, data: Buffer): string | undefined {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  const type = LOOK_TYPES[ext];
  if (type === "image/png" && data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) return type;
  if (type === "image/jpeg" && data.subarray(0, 3).equals(Buffer.from([255, 216, 255]))) return type;
  if (type === "image/webp" && data.toString("ascii", 0, 4) === "RIFF" && data.toString("ascii", 8, 12) === "WEBP") return type;
  return undefined;
}

export class Hands {
  private residentsRoot: string;
  private workRoot: string;
  private settings: HandsSettings;
  private onLetter: (letter: Letter) => void;
  private running = new Map<string, number>(); // 作業場 → 動いているコマンドの数
  private destinations = new Map<string, number>(); // 結果の手紙が届く作業場 → 動いているコマンドの数

  /** onLetter：結果を手紙で届けたあとに郵便局がすること（すぐに見直して、起こす） */
  constructor(residentsRoot: string, workRoot: string, settings: HandsSettings, onLetter: (letter: Letter) => void) {
    this.residentsRoot = residentsRoot;
    this.workRoot = workRoot;
    this.settings = settings;
    this.onLetter = onLetter;
  }

  /** コマンドが動いている作業場（trackKey）。手紙が全部済んでも、ここにあるうちは片付けない。 */
  busy(): Set<string> {
    return new Set(this.running.keys());
  }

  /** 長いコマンドの結果を待っている作業場（trackKey）。結果の手紙が届くまでは、その作業場では起こさない。 */
  awaiting(): Set<string> {
    return new Set(this.destinations.keys());
  }

  /** 作業場でPowerShell 7のコマンドを実行する。waitMs までに終われば結果を返す。
   *  終わらなければ「続いている」と返し、終わったら結果を郵便局からの手紙で届ける。どちらで届けるかは、先に起きたほうで1度だけ決める。 */
  async run(resident: string, work: string, command: string, caller?: Caller): Promise<string> {
    const cwd = this.place(work);
    const id = `R${newLetterId().slice(1)}`;
    const started = Date.now();
    const output = new Clip(2_000, 10_000);
    const script = `${PRELUDE}\n${command}`;
    const child = spawn("pwsh", ["-NoLogo", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text",
      "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64")], {
      cwd, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, NO_COLOR: "1", GIT_TERMINAL_PROMPT: "0" },
    });
    for (const stream of [child.stdout, child.stderr]) {
      stream.setEncoding("utf8");
      stream.on("data", (chunk: string) => output.push(chunk));
    }
    this.hold(work, 1);
    const destination = caller?.work ?? work;
    this.holdDestination(destination, 1);
    let timedOut = false;
    const limit = setTimeout(() => {
      timedOut = true;
      killTree(child.pid);
    }, this.settings.limitMs);

    return new Promise(answer => {
      let answered = false;
      const wait = setTimeout(() => {
        answered = true;
        answer(`まだ続いている（実行 ${id}）。終わったら、結果を郵便局からの手紙で届ける。その手紙で、また起こされる。` +
          "待つだけなら、何を待っているかを今の手紙に書き残して終わってよい（結果が届くまで、この作業場では起こさない）。");
      }, this.settings.waitMs);
      let ended = false;
      const end = (code: number | null, error?: string) => {
        if (ended) return;
        ended = true;
        // ここで投げると郵便局ごと落ちるので、届けられなかったことは記録に残し、実行中の印は必ず外す
        try {
          clearTimeout(limit);
          child.stdout.destroy();
          child.stderr.destroy();
          const ms = Date.now() - started;
          const how = error ? "error" : timedOut ? "timeout" : "exit";
          const result = [
            how === "error" ? `実行できなかった：${error}` :
            how === "timeout" ? `${Math.round(this.settings.limitMs / 60_000)}分たっても終わらないので止めた。` : `終了コード ${code}`,
            `（${(ms / 1000).toFixed(1)}秒）`,
            `\n${output.text() || "（出力なし）"}`,
          ].join("");
          this.record(resident, {
            kind: "run", ts: new Date(started).toISOString(), id, work, ...(caller ? { seat: caller.seat } : {}), command, how, code, ms,
            output: output.text(), reply: answered ? "letter" : "tool",
          });
          if (answered) {
            // 実行場所と結果を届ける作業場は別。呼んだ席の作業場へ届ける。
            this.letter(resident, destination, id,
              `作業場 ${work} で始めたコマンド（実行 ${id}）が終わった。\n\nコマンド:\n${command}\n\n${result}`);
          } else {
            clearTimeout(wait);
            answered = true;
            answer(result);
          }
        } catch (failure) {
          console.error(`${new Date().toISOString()} hands: 実行 ${id} の結果を ${resident} に届けられなかった：${(failure as Error).message}`);
        } finally {
          this.hold(work, -1);
          this.holdDestination(destination, -1);
        }
      };
      child.on("close", code => end(code));
      // 裏で動き続ける孫プロセスが出力の管を握ったままでも、本体が終わったら少し待って終える
      child.on("exit", code => setTimeout(() => end(code), 2_000).unref());
      child.on("error", error => end(null, error.message));
    });
  }

  /** Codexの形の差分を作業場に当てる。返すのは変えたファイルの一覧。当たらなければ投げる（何も書かない）。 */
  patch(resident: string, work: string, patch: string, caller?: Caller): string[] {
    const ts = new Date().toISOString();
    try {
      const changed = applyPatch(this.place(work), patch);
      this.record(resident, { kind: "patch", ts, work, ...(caller ? { seat: caller.seat } : {}), patch, changed });
      return changed;
    } catch (error) {
      this.record(resident, { kind: "patch", ts, work, ...(caller ? { seat: caller.seat } : {}), patch, error: (error as Error).message });
      throw error;
    }
  }

  /** 画像を返すだけの手。名前とリンクの実体をともに確かめ、選んだ作業場の外へ出さない。 */
  look(resident: string, work: string, path: string, caller?: Caller): { name: string; bytes: number; mimeType: string; data: string } {
    const ts = new Date().toISOString();
    try {
      const dir = realpathSync.native(this.place(work));
      if (!path || isAbsolute(path) || path.split(/[\\/]/).includes("..")) throw new Error("作業場の中の相対パスを指定する。");
      const target = realpathSync.native(resolve(dir, path));
      const rel = relative(dir, target);
      if (!rel || isAbsolute(rel) || rel.split(/[\\/]/)[0] === "..") throw new Error("画像は指定した作業場の外にある。");
      const info = statSync(target);
      if (!info.isFile()) throw new Error("画像ファイルではない。");
      if (info.size > LOOK_MAX_BYTES) throw new Error(`画像が大きすぎる（上限 ${LOOK_MAX_BYTES} bytes）。`);
      const bytes = readFileSync(target);
      if (bytes.byteLength > LOOK_MAX_BYTES) throw new Error(`画像が大きすぎる（上限 ${LOOK_MAX_BYTES} bytes）。`);
      const mimeType = imageType(target, bytes);
      if (!mimeType) throw new Error("PNG・JPEG・WebP画像だけが使える。拡張子と中身を確かめる。");
      const name = relative(dir, target);
      this.record(resident, { kind: "look", ts, work, ...(caller ? { seat: caller.seat } : {}), path, bytes: bytes.byteLength, result: "image" });
      return { name, bytes: bytes.byteLength, mimeType, data: bytes.toString("base64") };
    } catch (error) {
      this.record(resident, { kind: "look", ts, work, ...(caller ? { seat: caller.seat } : {}), path, error: (error as Error).message });
      throw error;
    }
  }

  private place(work: string): string {
    const dir = workPath(this.workRoot, work);
    const entry = lstatSync(dir, { throwIfNoEntry: false });
    if (!entry) throw new Error(`作業場「${work}」はまだない。work に名前を付けた手紙（自分宛てでもよい）を出すと、郵便局が作る。`);
    // 作業場そのものがリンクだと、その先（作業場の外かもしれない）に手が届いてしまう
    if (entry.isSymbolicLink()) throw new Error(`作業場「${work}」はリンクになっている。手は、リンクではない作業場でだけ使える。`);
    return dir;
  }

  private hold(work: string, delta: number): void {
    const key = trackKey(work);
    const count = (this.running.get(key) ?? 0) + delta;
    if (count > 0) this.running.set(key, count);
    else this.running.delete(key);
  }

  private holdDestination(work: string, delta: number): void {
    const key = trackKey(work);
    const count = (this.destinations.get(key) ?? 0) + delta;
    if (count > 0) this.destinations.set(key, count);
    else this.destinations.delete(key);
  }

  /** 生ログに残す。書けなくても、手でしたこと（と道具の返事）は変わらないので、投げずに郵便局の記録に残す。 */
  private record(resident: string, line: { ts: string } & Record<string, unknown>): void {
    try {
      const dir = join(this.residentsRoot, resident, "lifelog", "hands");
      mkdirSync(dir, { recursive: true });
      appendFileSync(join(dir, `${JST_DAY.format(new Date(line.ts))}.jsonl`), JSON.stringify(line) + "\n", "utf8");
    } catch (error) {
      console.error(`${new Date().toISOString()} hands: ${resident} の生ログに書けなかった：${(error as Error).message}`);
    }
  }

  private letter(resident: string, work: string, run: string, body: string): void {
    const letter: Letter = {
      kind: "letter", ts: new Date().toISOString(), id: newLetterId(), from: POST_OFFICE, to: resident, body,
      work, based_on: run,
    };
    append(this.residentsRoot, resident, letter);
    this.onLetter(letter);
  }
}

const FILE_HEADER = /^\*\*\* (Add|Update|Delete) File: (.+)$/;
const MOVE_TO = "*** Move to: ";

/** Codexの形の差分（*** Begin Patch … *** End Patch）を dir に当てる。返すのは「A 作った・M 書き換えた・D 消した」の一覧。
 *  先に全部の書き換え後の中身を作り、1か所でも当たらなければ何も書かない。書いている途中で失敗したら、元に戻す。 */
export function applyPatch(dir: string, patch: string): string[] {
  let plan: Plan;
  try {
    plan = planPatch(dir, patch);
  } catch (error) {
    // GPTは、Windowsのパスなどの \ を2つ重ねて書くことがある（2026-10-04、Holo）。そのままでは当たらず、
    // 差分全体の \\ を \ に戻すと当たるなら、全体が重ねて書かれたとみなして、戻したほうを当てる（揺れは入口で整える）
    const single = patch.replaceAll("\\\\", "\\");
    try {
      if (single === patch) throw error;
      plan = planPatch(dir, single);
      plan.changed.push("（\\ が2つ重なっていたので、1つに戻して当てた）");
    } catch {
      throw new Error(`どのファイルも変えていない。${(error as Error).message}`);
    }
  }
  write(plan.after);
  return plan.changed;
}

type Plan = { after: Map<string, string | null>; changed: string[] };

/** 書き換え後の中身を作る（まだ書かない）。ファイルごとの差分は OpenAI の applyDiff で当てる。 */
function planPatch(dir: string, patch: string): Plan {
  const lines = patch.replace(/\r\n/g, "\n").trim().split("\n");
  if (lines[0] !== "*** Begin Patch" || lines.at(-1) !== "*** End Patch") {
    throw new Error("差分は「*** Begin Patch」の行で始め、「*** End Patch」の行で終える。");
  }
  const after = new Map<string, string | null>(); // ファイル → 当てた後の中身（null は消す）
  const current = (path: string) => (after.has(path) ? after.get(path)! : existsSync(path) ? readFileSync(path, "utf8") : null);
  const changed: string[] = [];
  let i = 1;
  while (i < lines.length - 1) {
    const header = FILE_HEADER.exec(lines[i]);
    if (!header) throw new Error(`ファイルの見出し（*** Add File: など）の前に、次の行がある：${lines[i]}`);
    const [, verb, name] = header;
    const path = fileIn(dir, name.trim());
    i += 1;
    let moveTo: string | undefined;
    if (verb === "Update" && lines[i]?.startsWith(MOVE_TO)) {
      moveTo = lines[i].slice(MOVE_TO.length).trim();
      i += 1;
    }
    const start = i;
    while (i < lines.length - 1 && !FILE_HEADER.test(lines[i])) i += 1;
    const diff = lines.slice(start, i);
    while (diff.at(-1) === "") diff.pop(); // ファイルとファイルの間の空行

    try {
      if (verb === "Add") {
        // Codexと同じく、作ったファイルは改行で終える
        after.set(path, diff.length ? `${applyDiff("", diff.join("\n"), "create")}\n` : "");
        changed.push(`A ${name}`);
        continue;
      }
      const before = current(path);
      if (before === null) throw new Error("ファイルがない");
      if (verb === "Delete") {
        after.set(path, null);
        changed.push(`D ${name}`);
        continue;
      }
      // @@ に書いた行（関数やクラスの行）は、ファイルになければならない。applyDiff は @@ が1つのとき、
      // その行がなくても先へ進み、別の場所を書き換えてしまう（本物のCodexは断る。2026-10-04、Codexのレビュー）
      const present = new Set(before.split(/\r?\n/).map(line => line.trim()));
      for (const line of diff) {
        const anchor = line.startsWith("@@") ? line.slice(2).trim() : "";
        if (anchor && !present.has(anchor)) throw new Error(`@@ の行「${anchor}」がファイルにない`);
      }
      const content = diff.length ? applyDiff(before, diff.join("\n")) : before;
      if (moveTo) {
        after.set(path, null);
        after.set(fileIn(dir, moveTo), content);
        changed.push(`M ${name} → ${moveTo}`);
      } else {
        after.set(path, content);
        changed.push(`M ${name}`);
      }
    } catch (error) {
      throw new Error(`${name}：${(error as Error).message}`);
    }
  }
  return { after, changed };
}

/** 書く。消すのは書き終えてから（名前を変えるとき、新しいほうを書けなければ元が残る）。
 *  途中で失敗したら、それまでに変えたものを元の中身に戻す。 */
function write(after: Map<string, string | null>): void {
  const order = [...after].sort(([, a], [, b]) => Number(a === null) - Number(b === null));
  const touched: [string, Buffer | null][] = []; // 変える前の中身（null は、なかった）
  try {
    for (const [path, content] of order) {
      touched.push([path, existsSync(path) ? readFileSync(path) : null]);
      if (content === null) {
        rmSync(path, { force: true });
      } else {
        mkdirSync(dirname(path), { recursive: true });
        writeFileSync(path, content, "utf8");
      }
    }
  } catch (error) {
    const unrestored = touched.reverse().filter(([path, previous]) => {
      try {
        if (previous === null) rmSync(path, { force: true });
        else writeFileSync(path, previous);
        return false;
      } catch {
        return true;
      }
    }).map(([path]) => path);
    const reason = (error as Error).message;
    throw new Error(unrestored.length
      ? `書いている途中で失敗し、元に戻せなかったファイルがある（${unrestored.join("、")}）。${reason}`
      : `書いている途中で失敗したので、元に戻した。どのファイルも変えていない。${reason}`);
  }
}

/** 差分に書かれたファイルの場所。作業場の外は受け付けない。リンク（ジャンクション・シンボリックリンク）は、たどった先で確かめる。 */
function fileIn(dir: string, name: string): string {
  const path = resolve(dir, name);
  if (!within(dir, path)) throw new Error(`作業場の外には書けない：${name}`);
  let existing = path;
  while (!lstatSync(existing, { throwIfNoEntry: false })) existing = dirname(existing);
  let real: string | undefined;
  try {
    real = realpathSync.native(existing);
  } catch {
    // 先のないリンク
  }
  if (!real || !within(realpathSync.native(dir), real, true)) throw new Error(`作業場の外には書けない（リンクの先が外）：${name}`);
  return path;
}

function within(dir: string, path: string, orSame = false): boolean {
  const rel = relative(dir, path);
  if (!rel) return orSame;
  return !isAbsolute(rel) && rel.split(/[\\/]/)[0] !== "..";
}

/** 長い出力の、最初と最後だけを持つ。 */
class Clip {
  private headMax: number;
  private tailMax: number;
  private head = "";
  private tail = "";
  private total = 0;

  constructor(headMax: number, tailMax: number) {
    this.headMax = headMax;
    this.tailMax = tailMax;
  }

  push(chunk: string): void {
    this.total += chunk.length;
    if (this.head.length < this.headMax) {
      const taken = chunk.slice(0, this.headMax - this.head.length);
      this.head += taken;
      chunk = chunk.slice(taken.length);
    }
    this.tail = (this.tail + chunk).slice(-this.tailMax);
  }

  text(): string {
    const skipped = this.total - this.head.length - this.tail.length;
    const body = skipped > 0 ? `${this.head}\n…（長いので途中の${skipped}文字を省いた）…\n${this.tail}` : this.head + this.tail;
    // 進み具合の表示（\r で同じ行を書き直すもの。git clone など）は、画面と同じく最後の書き直しだけ残す
    return body.replace(/\r\n/g, "\n").split("\n").map(line => line.split("\r").filter(Boolean).at(-1) ?? "").join("\n").trimEnd();
  }
}
