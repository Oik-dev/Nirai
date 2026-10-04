// 住人の郵便受けを、郵便局を通さずに生ログから直接のぞく。Claude のセッションの始め（SessionStart フック）に使う。
// 使い方: node world/post/peek.ts <住人>
// 済んでいない手紙があれば、Claude Code がセッションの文脈に加える形（JSON）で出す。なければ何も出さない。

import { readAll, unfinished } from "./letters.ts";
import { resolveResident, settings } from "./settings.ts";

const resident = resolveResident(process.argv[2] ?? "");
if (!resident) throw new Error(`郵便受けのない住人: ${process.argv[2]}`);

const letters = unfinished(readAll(settings.residentsRoot, resident));
if (letters.length > 0) {
  const lines = letters.map(l => `- ${l.id}（${l.from}から${l.work ? `、作業場 ${l.work}` : ""}）：${l.body.replace(/\s+/g, " ").slice(0, 100)}`);
  const context = [
    `${resident}の郵便受けに、済んでいない手紙が${letters.length}通ある。Niraiの郵便局（MCP「nirai」の read_mailbox）で読み、済んだら mark_done する。`,
    ...lines,
  ].join("\n");
  process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: context } }));
}
