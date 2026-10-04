import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CliResident } from "./cli.ts";
import { readAll } from "./letters.ts";

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

test("上限を過ぎても終わらなければ止め、時間切れと書く", async () => {
  const { root, cli, stopped } = resident(`setTimeout(() => {}, 60_000)`, 500);
  cli.wake(["A"], "起きて", new Date());
  await stopped;
  const last = readAll(root, "Codex").at(-1);
  assert.equal(last?.kind === "stop" && last.how, "timeout");
});
