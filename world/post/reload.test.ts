import { test } from "node:test";
import assert from "node:assert/strict";
import {
  clearHandoff, decodeRevision, encodeRevision, postIdle, readHandoff, type Candidate, type PostRevision,
  revisionKey, ReloadWatcher, writeHandoff,
} from "./reload.ts";
import { HoloRoom } from "./holo.ts";
import { append, readAll } from "./letters.ts";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const A: PostRevision = { head: "head-a", post: "post-a", lock: "lock-a" };
const B: PostRevision = { head: "head-b", post: "post-b", lock: "lock-a" };
const C: PostRevision = { head: "head-c", post: "post-c", lock: "lock-b" };
const candidate = (revision: PostRevision): Candidate => ({ root: `R:/${revision.head}`, revision });

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
  let current: PostRevision | undefined = B;
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
  let current: PostRevision = B;
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

test("拒否した版は、B→C→Bと戻っても二度試さず、通知も一度だけ", async () => {
  let current: PostRevision = B;
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

test("revisionは環境変数へ往復でき、runtime keyはpost・lockで決まる", () => {
  assert.deepEqual(decodeRevision(encodeRevision(B)), B);
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
