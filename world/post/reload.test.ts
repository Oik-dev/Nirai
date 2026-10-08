import { test } from "node:test";
import assert from "node:assert/strict";
import {
  clearHandoff, decodeRevision, encodeRevision, postIdle, readHandoff, type Candidate, type Revision,
  revisionKey, sameRevision, readRevision, readRevisionNow, ReloadWatcher, writeHandoff,
} from "./reload.ts";
import { HoloRoom } from "./holo.ts";
import { append, readAll } from "./letters.ts";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';

const A: Revision = { head: "head-a", post: "post-a", sea: "sea-a", window: "window-a", lock: "lock-a" };
const B: Revision = { head: "head-b", post: "post-b", sea: "sea-a", window: "window-a", lock: "lock-a" };
const C: Revision = { head: "head-c", post: "post-c", sea: "sea-b", window: "window-a", lock: "lock-b" };
const candidate = (revision: Revision): Candidate => ({ root: `R:/${revision.head}`, revision });

test('海が中継中でも候補は別環境で試し、海や郵便局が忙しい間は切り替えない', async () => {
  let seaIdle = false;
  let postIsIdle = true;
  let probes = 0;
  let ready = 0;
  const watcher = new ReloadWatcher({
    initial: A, read: async () => B, idle: () => postIsIdle,
    seaIdle: async () => seaIdle,
    probe: async (_from, to) => { probes++; return { ok: true, candidate: candidate(to) }; },
    ready: () => ready++, rejected: () => assert.fail('reject'),
  });
  await watcher.check();
  assert.equal(probes, 1, 'probeは本番の手すきを待たない');
  assert.equal(ready, 0, '海が忙しい間は切り替えない');
  seaIdle = true;
  await watcher.check();
  assert.equal(ready, 1);
  assert.equal(probes, 1, '合格済み候補を再利用');

  let checks = 0;
  const raced = new ReloadWatcher({
    initial: A, read: async () => B, idle: () => postIsIdle,
    seaIdle: async () => { if (++checks === 1) postIsIdle = false; return true; },
    probe: async (_from, to) => ({ ok: true, candidate: candidate(to) }),
    ready: () => assert.fail('海の問い合わせ後、郵便局のbusyを再確認する'), rejected: () => assert.fail('reject'),
  });
  await raced.check();
  assert.equal(checks, 1);
});

test("新版の検証中と手すき待ちは起床を保留し、仕事中も別環境でprobeする", async () => {
  let latest = B;
  let idle = false;
  let finish!: () => void;
  const gate = new Promise<void>(resolve => (finish = resolve));
  const seen: string[] = [];
  let ready = 0;
  const watcher = new ReloadWatcher({
    initial: A, read: async () => latest, readNow: () => latest, idle: () => idle,
    probe: async () => { seen.push("probe"); await gate; return { ok: true, candidate: candidate(B) }; },
    ready: () => ready++, rejected: () => assert.fail("拒否しない"),
  });
  assert.equal(watcher.waiting()?.phase, "probing");
  const ongoing = watcher.check();
  await Promise.resolve();
  assert.deepEqual(seen, ["probe"]);
  assert.equal(watcher.waiting()?.phase, "probing");
  finish();
  await ongoing;
  assert.equal(watcher.waiting()?.phase, "ready");
  assert.equal(ready, 0, "既存の仕事を止めない");
  idle = true;
  await watcher.check();
  assert.equal(ready, 1);
});

test("却下した新版は起床を再開し、新しい版を検出すればまた保留する", async () => {
  let latest = B;
  let attempts = 0;
  const watcher = new ReloadWatcher({
    initial: A, read: async () => latest, readNow: () => latest, idle: () => false,
    probe: async () => { attempts++; return { ok: false, detail: "broken" }; },
    ready: () => assert.fail("切り替えない"), rejected: () => {},
  });
  assert.equal(watcher.waiting()?.revision.head, B.head);
  await watcher.check();
  assert.equal(watcher.waiting(), undefined, "拒否した版で起床を妨げない");
  await watcher.check();
  assert.equal(attempts, 1);
  latest = C;
  assert.equal(watcher.waiting()?.revision.head, C.head, "次の版を待つ");
  latest = A;
  assert.equal(watcher.waiting(), undefined, "稼働版には保留を設けない");
});

test("Gitの新版確認に失敗したときや文書だけが変わったときは起床を止めない", () => {
  let latest: Revision | undefined = undefined;
  const watcher = new ReloadWatcher({
    initial: A, read: async () => latest, readNow: () => latest, idle: () => true,
    probe: async () => assert.fail("probeしない"), ready: () => {}, rejected: () => {},
  });
  assert.equal(watcher.waiting(), undefined);
  latest = { ...A, head: "docs-only" };
  assert.equal(watcher.waiting(), undefined);
});

test('Gitからworld全体の版を読み、docs-onlyでは同じ版、旧status照合もpost:lockのまま', async () => {
  const root = mkdtempSync(join(tmpdir(), 'nirai-revision-'));
  const git = (...args: string[]) => {
    const result = spawnSync('git', ['-C', root, ...args], { windowsHide: true, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
  };
  try {
    git('init', '-q');
    for (const part of ['post', 'sea', 'window', 'docs']) {
      mkdirSync(join(root, 'world', part), { recursive: true });
      writeFileSync(join(root, 'world', part, 'file'), part);
    }
    writeFileSync(join(root, 'world', 'package-lock.json'), '{}');
    git('add', '.'); git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'initial');
    const before = await readRevision(root);
    assert.ok(before);
    assert.deepEqual(readRevisionNow(root), before);
    writeFileSync(join(root, 'world', 'docs', 'file'), 'changed');
    git('add', '.'); git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'docs');
    const after = await readRevision(root);
    assert.ok(after);
    assert.deepEqual(readRevisionNow(root), after);
    assert.notEqual(before.head, after.head);
    assert.equal(sameRevision(before, after), true);
    assert.deepEqual(await readRevision(root, before.head), before);
    for (const part of ['post', 'sea', 'window', 'lock'] as const) {
      assert.equal(sameRevision(before, { ...before, [part]: 'changed' }), false);
    }
    const oldEnv = Buffer.from(JSON.stringify({ head: before.head, post: 'wrong', lock: 'wrong' })).toString('base64url');
    assert.deepEqual(await readRevision(root, decodeRevision(oldEnv)?.head), before);
    assert.equal(`${after.post}:${after.lock}`, `${before.post}:${before.lock}`);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("Holo・Codex・Claude・手・HTTPのどれかが動いていれば、郵便局は暇ではない", () => {
  assert.equal(postIdle(false, [false, false], new Set(), 0), true);
  assert.equal(postIdle(true, [false, false], new Set(), 0), false, "Holo");
  assert.equal(postIdle(false, [true, false], new Set(), 0), false, "Codex");
  assert.equal(postIdle(false, [false, true], new Set(), 0), false, "Claude");
  assert.equal(postIdle(false, [false, false], new Set(["job"]), 0), false, "Holoの手");
  assert.equal(postIdle(false, [false, false], new Set(), 1), false, "HTTP受付中");
});

test("Holoへ一言を渡した直後とresume中・終了直後は版替えせず、休みが明けてから暇になる", () => {
  const root = mkdtempSync(join(tmpdir(), "nirai-reload-holo-"));
  try {
    const holo = new HoloRoom(root, {
      restMs: 60_000,
      busyLimitMs: 30 * 60_000,
      replyPath: /^\/backend-api\/(f\/)?conversation(?:\/resume)?$/,
    });
    const at = (seconds: number) => new Date(2026, 9, 5, 1, 0, seconds, 0);
    const report = (phase: "start" | "end" | "error", id: string, path: string, error?: string) => ({
      phase, id, method: "POST", path, ...(error ? { error } : {}),
    });

    append(root, "Holo", { kind: "room", ts: at(0).toISOString(), url: "https://chatgpt.com/c/11111111-1111-1111-1111-111111111111" });
    append(root, "Holo", { kind: "letter", ts: at(0).toISOString(), id: "A", from: "Codex", to: "Holo", body: "x" });
    assert.deepEqual(holo.next(at(1))?.letters, ["A"]);
    const afterWake = new Date(at(1).getTime() + 500);
    assert.equal(holo.awake(afterWake), true, "start前でも一言を渡した直後はHoloRoom自身がbusy");
    assert.equal(holo.next(afterWake), undefined, "送信中に二重で一言を渡さない");
    assert.equal(readAll(root, "Holo").some(line => line.kind === "wake"), false, "送信確認前はwake成功として数えない");
    assert.equal(postIdle(holo.awake(afterWake), [false, false], new Set(), 0), false, "一言を渡した直後は版替えしない");

    holo.net(report("start", "conversation", "/backend-api/f/conversation"), at(2));
    holo.net(report("error", "conversation", "/backend-api/f/conversation", "net::ERR_HTTP2_PROTOCOL_ERROR"), at(3));
    holo.net(report("start", "resume", "/backend-api/f/conversation/resume"), at(4));

    assert.equal(holo.awake(at(5)), true, "resume通信を起きていると認識する");
    assert.equal(postIdle(holo.awake(at(5)), [false, false], new Set(), 0), false, "resume中は版替えしない");

    holo.net(report("end", "resume", "/backend-api/f/conversation/resume"), at(90));
    assert.equal(holo.awake(at(91)), true, "resume終了直後はHoloRoomの休み");
    assert.equal(postIdle(holo.awake(at(91)), [false, false], new Set(), 0), false, "resume終了直後は版替えしない");
    assert.equal(holo.awake(at(151)), false);
    assert.equal(postIdle(false, [false, false], new Set(), 0), true, "最後の通信終了からrestMs後は版替え可能");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("版が変わり、暇で、固定候補が起きれば、その候補だけを入れ替え対象にする", async () => {
  let current: Revision | undefined = B;
  let ready: Candidate | undefined;
  let probes = 0;
  const watcher = new ReloadWatcher({
    initial: A,
    read: async () => current,
    idle: () => true,
    probe: async (_from, to) => { probes++; return { ok: true, candidate: candidate(to) }; },
    ready: value => (ready = value),
    rejected: () => assert.fail("rejectしない"),
  });

  await watcher.check();
  assert.equal(probes, 1);
  assert.deepEqual(ready, candidate(B));
});

test("候補版を試している間に仕事が来たら、合格しても待ち、暇になった次の見回りで同じ固定候補を使う", async () => {
  let idle = true;
  let finishProbe!: () => void;
  let ready: Candidate | undefined;
  let probes = 0;
  const gate = new Promise<void>(resolve => (finishProbe = resolve));
  const watcher = new ReloadWatcher({
    initial: A,
    read: async () => B,
    idle: () => idle,
    probe: async (_from, to) => { probes++; await gate; return { ok: true, candidate: candidate(to) }; },
    ready: value => (ready = value),
    rejected: () => assert.fail("rejectしない"),
  });

  const checking = watcher.check();
  idle = false;
  finishProbe();
  await checking;
  assert.equal(ready, undefined);
  idle = true;
  await watcher.check();
  assert.equal(probes, 1, "再試験しない");
  assert.deepEqual(ready, candidate(B));
});

test("試験中にHEADが別の版へ進んだら、古い候補を入れ替え対象にしない", async () => {
  let current: Revision = B;
  const discarded: Candidate[] = [];
  let ready = 0;
  const watcher = new ReloadWatcher({
    initial: A,
    read: async () => current,
    idle: () => true,
    probe: async (_from, to) => {
      current = C;
      return { ok: true, candidate: candidate(to) };
    },
    ready: () => ready++,
    rejected: () => assert.fail("rejectしない"),
    discard: value => discarded.push(value),
  });
  await watcher.check();
  assert.equal(ready, 0);
  assert.deepEqual(discarded, [candidate(B)]);
});

test("B合格後にCを試してBへ戻っても、掃除されたBを再利用しない", async () => {
  let current = B;
  const probes: string[] = [];
  const present = new Set<string>();
  const watcher = new ReloadWatcher({
    initial: A,
    read: async () => current,
    readNow: () => current,
    idle: () => false,
    probe: async (_from, to) => {
      probes.push(to.head);
      present.clear(); // 次の版を試すと、古い候補はディスクから掃除される
      present.add(to.head);
      return { ok: true, candidate: candidate(to) };
    },
    ready: () => assert.fail("まだ作業中なので切り替えない"),
    rejected: () => assert.fail("拒否しない"),
  });
  await watcher.check();
  assert.equal(watcher.waiting()?.phase, "ready");
  current = C;
  await watcher.check();
  assert.deepEqual([...present], [C.head]);
  current = B;
  assert.equal(watcher.waiting()?.phase, "probing", "古いBの検証を使わない");
  await watcher.check();
  assert.deepEqual(probes, [B.head, C.head, B.head]);
  assert.deepEqual([...present], [B.head]);
});

test("拒否した版は、B→C→Bと戻っても二度試さず、通知も一度だけ", async () => {
  let current: Revision = B;
  const rejected: string[] = [];
  let probes = 0;
  const watcher = new ReloadWatcher({
    initial: A,
    read: async () => current,
    idle: () => true,
    probe: async () => { probes++; return { ok: false, detail: "broken" }; },
    ready: () => assert.fail("readyにしない"),
    rejected: revision => rejected.push(revision.post),
  });

  await watcher.check();
  current = C;
  await watcher.check();
  current = B;
  await watcher.check();
  assert.equal(probes, 2);
  assert.deepEqual(rejected, ["post-b", "post-c"]);
});

test("probeが例外でも外へ投げず、拒否として一度だけ扱う", async () => {
  let rejected = 0;
  const watcher = new ReloadWatcher({
    initial: A,
    read: async () => B,
    idle: () => true,
    probe: async () => { throw new Error("temp broken"); },
    ready: () => assert.fail("readyにしない"),
    rejected: (_revision, detail) => { rejected++; assert.match(detail, /temp broken/); },
  });
  await watcher.check();
  await watcher.check();
  assert.equal(rejected, 1);
});

test("見回りが重なっても、同じ候補の試験は1本だけ走る", async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => (release = resolve));
  let probes = 0;
  const rejected: string[] = [];
  const watcher = new ReloadWatcher({
    initial: A,
    read: async () => B,
    idle: () => true,
    probe: async () => { probes++; await gate; return { ok: false, detail: "broken" }; },
    ready: () => assert.fail("readyにしない"),
    rejected: revision => rejected.push(revision.post),
  });
  const first = watcher.check();
  await Promise.all([watcher.check(), watcher.check()]);
  release();
  await first;
  assert.equal(probes, 1);
  assert.deepEqual(rejected, ["post-b"]);
});

test("gitの版を読めなければ、変わったと決めつけない", async () => {
  let probes = 0;
  const watcher = new ReloadWatcher({
    initial: A,
    read: async () => undefined,
    idle: () => true,
    probe: async () => { probes++; return { ok: true, candidate: candidate(B) }; },
    ready: () => assert.fail("readyにしない"),
    rejected: () => assert.fail("rejectしない"),
  });
  await watcher.check();
  assert.equal(probes, 0);
});

test("revisionは環境変数へ往復でき、環境からはheadだけを受け取り、文書だけの変更ではruntime keyは変わらない", () => {
  assert.deepEqual(decodeRevision(encodeRevision(B)), { head: B.head });
  assert.equal(revisionKey({ ...B, head: "unrelated-doc-commit" }), revisionKey(B));
});

test("番人への引継ぎは、その後HEADが別版へ進んでも、確認済み候補そのものを保持する", () => {
  const runtime = mkdtempSync(join(tmpdir(), "nirai-post-handoff-"));
  try {
    const verifiedB = candidate(B);
    writeHandoff(runtime, verifiedB);
    const sourceHasMovedToC = C;
    assert.notEqual(revisionKey(sourceHasMovedToC), revisionKey(B));
    assert.deepEqual(readHandoff(runtime), verifiedB, "可変なHEADではなく、確認済みBを読む");
    clearHandoff(runtime);
    assert.equal(readHandoff(runtime), undefined);
  } finally {
    rmSync(runtime, { recursive: true, force: true });
  }
});
