import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { claudeCommand, CliResident, codexCommand, findClaude, parseUsageLimit } from "./cli.ts";
import { readAll } from "./letters.ts";
import { instructions } from "./mcp.ts";

// 本物の脳の代わりに、node の小さなスクリプトを起こす
function resident(script: string, limitMs = 30_000) {
  const root = mkdtempSync(join(tmpdir(), "nirai-cli-"));
  let resolveStop: () => void;
  const stopped = new Promise<void>(resolve => (resolveStop = resolve));
  const cli = new CliResident("Codex", root, () => ({ file: process.execPath, args: ["-e", script], cwd: root }), limitMs, () => resolveStop());
  return { root, cli, stopped };
}

test("起こしたと書いてから起こし、終わったら止まったと書く。脳の出力は生ログに残る", async () => {
  const { root, cli, stopped } = resident(`console.log(JSON.stringify({ type: "turn.completed" }))`);
  cli.wake(["A"], "起きて", new Date());
  assert.equal(cli.awake(), true);
  assert.deepEqual(readAll(root, "Codex").map(l => l.kind), ["wake"]);
  await stopped;
  assert.equal(cli.awake(), false);
  const last = readAll(root, "Codex").at(-1);
  assert.equal(last?.kind === "stop" && last.how, "exit");
  const logDir = join(root, "Codex", "lifelog", "codex-cli");
  assert.match(readFileSync(join(logDir, readdirSync(logDir)[0]), "utf8"), /turn\.completed/);
});

test("失敗して終わったら、終わり方と最後のエラーを残す", async () => {
  const { root, cli, stopped } = resident(`console.error("auth failed"); process.exit(3)`);
  cli.wake(["A"], "起きて", new Date());
  await stopped;
  const last = readAll(root, "Codex").at(-1);
  assert.equal(last?.kind === "stop" && last.how, "error");
  assert.match(last?.kind === "stop" ? (last.detail ?? "") : "", /code 3 auth failed/);
});

test("Codex/Claudeの上限文から、時刻だけ・日付つき・読めない場合をそろえて読む", () => {
  const now = new Date(2026, 9, 5, 14, 0, 0);
  const clock = parseUsageLimit("You’ve hit your usage limit. try again at 3:02 PM.", now);
  assert.equal(clock?.known, true);
  assert.deepEqual([clock?.until.getFullYear(), clock?.until.getMonth(), clock?.until.getDate(), clock?.until.getHours(), clock?.until.getMinutes()], [2026, 9, 5, 15, 2]);

  const dated = parseUsageLimit("You've hit your limit · limit resets at October 12 at 7:16 AM.", now);
  assert.equal(dated?.known, true);
  assert.deepEqual([dated?.until.getFullYear(), dated?.until.getMonth(), dated?.until.getDate(), dated?.until.getHours(), dated?.until.getMinutes()], [2026, 9, 12, 7, 16]);

  const epoch = Math.floor(new Date(2026, 9, 6, 9, 30, 0).getTime() / 1000);
  const structured = parseUsageLimit(`You've reached your usage limit. {"resetsAt":${epoch}}`, now);
  assert.equal(structured?.until.getTime(), epoch * 1000);

  const unknown = parseUsageLimit("You're out of usage credits. reset time unavailable.", now, 60 * 60_000);
  assert.equal(unknown?.known, false);
  assert.equal(unknown?.until.getTime(), now.getTime() + 60 * 60_000);
});

test("CLIが上限で終わったらlimitとuntilを書き、ふつうのerrorに数えない", async () => {
  const message = "You’ve hit your usage limit. try again at December 31, 2099 at 11:59 PM.";
  const { root, cli, stopped } = resident(`console.log(JSON.stringify({type:"turn.failed",error:{message:${JSON.stringify(message)}}})); process.exit(1)`);
  cli.wake(["A"], "起きて", new Date());
  await stopped;
  const last = readAll(root, "Codex").at(-1);
  assert.equal(last?.kind === "stop" && last.how, "limit");
  assert.equal(last?.kind === "stop" && last.untilKnown, true);
  assert.match(last?.kind === "stop" ? (last.until ?? "") : "", /^2099-12-31T/);
});

test("Claude型のis_error=trueが終了コード0でも、上限ならlimitとして扱う", async () => {
  const message = "You've hit your limit · limit resets at December 31, 2099 at 11:59 PM.";
  const { root, cli, stopped } = resident(`console.log(JSON.stringify({is_error:true,result:${JSON.stringify(message)}})); process.exit(0)`);
  cli.wake(["A"], "起きて", new Date());
  await stopped;
  const last = readAll(root, "Codex").at(-1);
  assert.equal(last?.kind === "stop" && last.how, "limit");
  assert.equal(last?.kind === "stop" && last.untilKnown, true);
});

test("上限を過ぎても終わらなければ止め、時間切れと書く", async () => {
  const { root, cli, stopped } = resident(`setTimeout(() => {}, 60_000)`, 500);
  cli.wake(["A"], "起きて", new Date());
  await stopped;
  const last = readAll(root, "Codex").at(-1);
  assert.equal(last?.kind === "stop" && last.how, "timeout");
});

test("Codexの場所は、起こすたびに探し直す（郵便局が動いている間に、更新で場所が変わる）", () => {
  let found = 0;
  const command = codexCommand({ model: "m", effort: "low", port: 1, workRoot: "W" }, () => `codex-${++found}.exe`);
  assert.equal(command("1回目").file, "codex-1.exe");
  assert.equal(command("2回目").file, "codex-2.exe");
  assert.equal(command("起こす一言").args.at(-1), "起こす一言");
});

test("Claudeへの一言は、値をいくつも取る指定より前に置き、引用符や日本語も崩れずに届く", async () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-cli-"));
  const { args, cwd } = claudeCommand(
    {
      model: "claude-opus-5-5", effort: "xhigh", autoCompact: "200k", port: 47801, home: "H", workRoot: "W",
      residentsRoot: root, scratch: join(root, "scratch"),
    },
    () => "claude.exe",
  )("Claude、郵便局から：\"手紙\"が1通");
  assert.equal(cwd, "H");
  assert.deepEqual(args.slice(0, 12), [
    "-p", "Claude、郵便局から：\"手紙\"が1通", "--output-format", "json", "--permission-mode", "auto",
    "--model", "claude-opus-5-5", "--effort", "xhigh", "--autocompact", "200k",
  ]);
  assert.equal(args[args.indexOf("-p") + 1], "Claude、郵便局から：\"手紙\"が1通");
  for (const many of ["--tools", "--mcp-config", "--add-dir"]) assert.ok(args.indexOf("-p") < args.indexOf(many), many);
  assert.match(args[args.indexOf("--mcp-config") + 1], /"url":"http:\/\/127\.0\.0\.1:47801\/mcp\/claude"/);
  // 同じ引数で node を起こし、受け取った引数を書き出させる（-- より後ろは、node 自身への指定として読まれない）
  let resolveStop: () => void;
  const stopped = new Promise<void>(resolve => (resolveStop = resolve));
  const echo = new CliResident("Claude", root, () => ({ file: process.execPath, args: ["-e", "console.log(JSON.stringify(process.argv.slice(1)))", "--", ...args], cwd: root }), 30_000, () => resolveStop());
  echo.wake(["A"], "起きて", new Date());
  await stopped;
  const logDir = join(root, "Claude", "lifelog", "claude-cli");
  assert.deepEqual(JSON.parse(readFileSync(join(logDir, readdirSync(logDir)[0]), "utf8")), args);
});

test("起こしたClaudeには、MCPの説明の2,048字で切られない決まりと人格の全文が、起こすたびの最新で届く", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-cli-"));
  mkdirSync(join(root, "Claude"));
  const persona = join(root, "Claude", "persona.md");
  const start = claudeCommand(
    {
      model: "claude-opus-5-5", effort: "xhigh", autoCompact: "200k", port: 47801, home: "H", workRoot: "W",
      residentsRoot: root, scratch: join(root, "scratch"),
    },
    () => "claude.exe",
  );
  const systemOf = (args: string[]) => readFileSync(args[args.indexOf("--append-system-prompt-file") + 1], "utf8");

  writeFileSync(persona, "人格の最後の合言葉：芽吹く海。");
  const first = systemOf(start("起きて").args);
  assert.equal(first, instructions("Claude", root));
  assert.ok(first.length > 2048, `${first.length}字`);
  assert.match(first, /あなたはClaude。/);
  assert.match(first, /芽吹く海/);
  assert.doesNotMatch(first, /^## Holoだけ$/m);

  writeFileSync(persona, "人格の最後の合言葉：満ちる潮。");
  const second = systemOf(start("また起きて").args);
  assert.match(second, /満ちる潮/);
  assert.doesNotMatch(second, /芽吹く海/);
});

test("Claudeの場所は、アプリのパッケージの中の、いちばん新しい版", () => {
  const localAppData = mkdtempSync(join(tmpdir(), "nirai-localappdata-"));
  const put = (version: string, seconds: number) => {
    const dir = join(localAppData, "Packages", "Claude_pzs8sxrjxfjjc", "LocalCache", "Roaming", "Claude", "claude-code", version, "abc");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "claude.exe"), "");
    utimesSync(join(dir, "claude.exe"), seconds, seconds);
    return join(dir, "claude.exe");
  };
  put("2.1.1", 1_000);
  const latest = put("2.1.2", 2_000);
  const saved = process.env.LOCALAPPDATA;
  process.env.LOCALAPPDATA = localAppData;
  try {
    assert.equal(findClaude(), latest);
  } finally {
    process.env.LOCALAPPDATA = saved;
  }
});
