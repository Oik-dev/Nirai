// 郵便局が貸す手。Holoの脳はChatGPTの中にあり、Masterの手元のファイルにもコマンドにも届かない。
// 手は、GPTが慣れているCodexの2つ（コマンドと apply_patch）にそろえる。読む・探す・一覧は、コマンドでできる。
// 手が届くのは作業場（D:\Products\Work\<名前>）。作業場は、名前を付けた手紙を出すと郵便局が作る。
// やったことは、その住人の生ログ（lifelog/hands/<日本時間の日付>.jsonl）に残す。

import { spawn } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { applyDiff } from "@openai/agents-core";
import { killTree } from "./cli.ts";
import { append, JST_DAY, type Letter, newLetterId } from "./letters.ts";
import { POST_OFFICE } from "./waker.ts";
import { workPath } from "./work.ts";

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

export class Hands {
  private residentsRoot: string;
  private workRoot: string;
  private settings: HandsSettings;
  private onLetter: (letter: Letter) => void;
  private running = new Map<string, number>(); // 作業場 → 動いているコマンドの数

  /** onLetter：結果を手紙で届けたあとに郵便局がすること（すぐに見直して、起こす） */
  constructor(residentsRoot: string, workRoot: string, settings: HandsSettings, onLetter: (letter: Letter) => void) {
    this.residentsRoot = residentsRoot;
    this.workRoot = workRoot;
    this.settings = settings;
    this.onLetter = onLetter;
  }

  /** コマンドが動いている作業場。手紙が全部済んでも、ここにあるうちは片付けない。 */
  busy(): Set<string> {
    return new Set(this.running.keys());
  }

  /** 作業場でPowerShell 7のコマンドを実行する。waitMs までに終われば結果を返す。
   *  終わらなければ「続いている」と返し、終わったら結果を郵便局からの手紙で届ける。どちらで届けるかは、先に起きたほうで1度だけ決める。 */
  async run(resident: string, work: string, command: string): Promise<string> {
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
          "待つだけなら、返事を待つときと同じく、今の手紙に印を付けて終わってよい。");
      }, this.settings.waitMs);
      let ended = false;
      const end = (code: number | null, error?: string) => {
        if (ended) return;
        ended = true;
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
          kind: "run", ts: new Date(started).toISOString(), id, work, command, how, code, ms,
          output: output.text(), reply: answered ? "letter" : "tool",
        });
        if (answered) {
          this.letter(resident, work, id, `作業場 ${work} で始めたコマンド（実行 ${id}）が終わった。\n\nコマンド:\n${command}\n\n${result}`);
        } else {
          clearTimeout(wait);
          answered = true;
          answer(result);
        }
        this.hold(work, -1);
      };
      child.on("close", code => end(code));
      // 裏で動き続ける孫プロセスが出力の管を握ったままでも、本体が終わったら少し待って終える
      child.on("exit", code => setTimeout(() => end(code), 2_000).unref());
      child.on("error", error => end(null, error.message));
    });
  }

  /** Codexの形の差分を作業場に当てる。返すのは変えたファイルの一覧。当たらなければ投げる（何も書かない）。 */
  patch(resident: string, work: string, patch: string): string[] {
    const ts = new Date().toISOString();
    try {
      const changed = applyPatch(this.place(work), patch);
      this.record(resident, { kind: "patch", ts, work, patch, changed });
      return changed;
    } catch (error) {
      this.record(resident, { kind: "patch", ts, work, patch, error: (error as Error).message });
      throw error;
    }
  }

  private place(work: string): string {
    const dir = workPath(this.workRoot, work);
    if (!existsSync(dir)) {
      throw new Error(`作業場「${work}」はまだない。work に名前を付けた手紙（自分宛てでもよい）を出すと、郵便局が作る。`);
    }
    return dir;
  }

  private hold(work: string, delta: number): void {
    const count = (this.running.get(work) ?? 0) + delta;
    if (count > 0) this.running.set(work, count);
    else this.running.delete(work);
  }

  private record(resident: string, line: { ts: string } & Record<string, unknown>): void {
    const dir = join(this.residentsRoot, resident, "lifelog", "hands");
    mkdirSync(dir, { recursive: true });
    appendFileSync(join(dir, `${JST_DAY.format(new Date(line.ts))}.jsonl`), JSON.stringify(line) + "\n", "utf8");
  }

  private letter(resident: string, work: string, run: string, body: string): void {
    const letter: Letter = {
      kind: "letter", ts: new Date().toISOString(), id: newLetterId(), from: POST_OFFICE, to: resident, body, work, based_on: run,
    };
    append(this.residentsRoot, resident, letter);
    this.onLetter(letter);
  }
}

const FILE_HEADER = /^\*\*\* (Add|Update|Delete) File: (.+)$/;
const MOVE_TO = "*** Move to: ";

/** Codexの形の差分（*** Begin Patch … *** End Patch）を dir に当てる。ファイルごとの差分は OpenAI の applyDiff で当てる。
 *  先に全部の書き換え後の中身を作り、1か所でも当たらなければ何も書かない。返すのは「A 作った・M 書き換えた・D 消した」の一覧。 */
export function applyPatch(dir: string, patch: string): string[] {
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
        const content = applyDiff("", diff.join("\n"), "create");
        after.set(path, content && `${content}\n`);
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
  for (const [path, content] of after) {
    if (content === null) {
      rmSync(path, { force: true });
    } else {
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, content, "utf8");
    }
  }
  return changed;
}

/** 差分に書かれたファイルの場所。作業場の外は受け付けない。 */
function fileIn(dir: string, name: string): string {
  const path = resolve(dir, name);
  const rel = relative(dir, path);
  if (!rel || isAbsolute(rel) || rel.split(/[\\/]/)[0] === "..") throw new Error(`作業場の外には書けない：${name}`);
  return path;
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
    return body.replace(/\r\n/g, "\n").trimEnd();
  }
}
