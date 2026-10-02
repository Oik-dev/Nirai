// Claude Codeの会話記録を、住人Claudeの生ログへ写す（Stopフックから毎ターン呼ばれる）。
// 写すのは会話本体（<session>.jsonl）と、そのセッションのフォルダー（サブエージェントの記録、別保存された大きなツール出力）。
// 生ログは追記のみ：写し元が写し先より長くなったファイルだけを上書きコピーし、短くなったファイルで上書きしない。
// 全記録をなめるので、取りこぼしたターンがあっても次の呼び出しで回収される。失敗してもClaude Codeは止めない。
import { appendFileSync, copyFileSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const LIFELOG = "D:\\Products\\Residents\\Claude\\lifelog\\claude-code";
const PROJECTS = join(homedir(), ".claude", "projects");
const SESSION = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}(\.jsonl)?$/; // memory/などセッション以外は写さない

function sizeOf(path) {
  try {
    return statSync(path).size;
  } catch {
    return -1;
  }
}

function logError(error) {
  try {
    appendFileSync(join(LIFELOG, "..", "sync-errors.log"), `${new Date().toISOString()} ${error}\n`);
  } catch {}
}

// 一つのファイルの失敗（書き込み中のロックなど）で、ほかのファイルを止めない。
function mirror(src, dst) {
  try {
    if (statSync(src).isDirectory()) {
      for (const name of readdirSync(src)) mirror(join(src, name), join(dst, name));
    } else if (sizeOf(src) > sizeOf(dst)) {
      mkdirSync(dirname(dst), { recursive: true });
      copyFileSync(src, dst);
    }
  } catch (error) {
    logError(error);
  }
}

try {
  for (const project of readdirSync(PROJECTS)) {
    if (!project.startsWith("D--Products")) continue; // D:\Products配下のセッションだけ
    for (const name of readdirSync(join(PROJECTS, project))) {
      if (SESSION.test(name)) mirror(join(PROJECTS, project, name), join(LIFELOG, name));
    }
  }
} catch (error) {
  logError(error);
}
