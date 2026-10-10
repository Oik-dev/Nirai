import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { test } from 'node:test';
import type { ChildProcess } from 'node:child_process';
import { openWorkshopGate } from './workshop-gate.ts';

test('工房関門は番号だけをHTTPで渡し、壊れた候補を置かず、Chromeを片付ける', async () => {
  const avatar = Buffer.from('disposable-avatar');
  const candidate = Buffer.from('disposable-motion');
  let address = '';
  let killed = false;
  const gate = await openWorkshopGate(avatar, {
    launch(url) {
      address = url;
      const process = new EventEmitter() as ChildProcess;
      process.kill = (() => { killed = true; process.emit('exit', 0); return true; }) as ChildProcess['kill'];
      void fetch(url + 'ready', { method: 'POST' });
      return process;
    },
  });
  try {
    assert.equal((await fetch(address + 'avatar.vrm')).status, 200);
    assert.deepEqual(Buffer.from(await (await fetch(address + 'avatar.vrm')).arrayBuffer()), avatar);
    assert.equal((await fetch(address + 'body/gate-samples.js')).status, 200);
    assert.equal((await fetch(address + 'body/../server.ts')).status, 404);
    const result = gate.check(candidate);
    const task = await (await fetch(address + 'task')).json();
    assert.deepEqual(task, { id: 1 });
    assert.deepEqual(Buffer.from(await (await fetch(address + 'motion.vrma')).arrayBuffer()), candidate);
    assert.equal((await fetch(address + 'result/1', { method: 'POST', body: '{}' })).status, 200);
    assert.equal(await result, false);
    assert.equal((await fetch(address + 'task')).status, 204);
    const aborted = new AbortController();
    const stopped = gate.check(candidate, aborted.signal);
    aborted.abort();
    await assert.rejects(stopped, /中断/);
    assert.equal((await fetch(address + 'motion.vrma')).status, 404);
  } finally {
    await gate.close();
  }
  assert.equal(killed, true);
});

test('closeはChromeの終了を待ってから完了する', async () => {
  let exit!: () => void;
  let killing = false;
  let exited = false;
  const gate = await openWorkshopGate(Buffer.from('disposable'), {
    launch(url) {
      const process = new EventEmitter() as ChildProcess;
      process.kill = (() => {
        killing = true;
        exit = () => { exited = true; process.emit('exit', 0); };
        return true;
      }) as ChildProcess['kill'];
      void fetch(url + 'ready', { method: 'POST' });
      return process;
    },
  });
  let completed = false;
  const closing = gate.close().then(() => { completed = true; });
  // 非同期のserver.closeが終わる前でも、closeが早期完了しないことを見る。
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(killing, true);
  assert.equal(completed, false);
  exit();
  await closing;
  assert.equal(exited, true);
  assert.equal(completed, true);
});

test('起動を中断したらChromeを終了させ、関門は使わせない', async () => {
  const controller = new AbortController();
  let killed = false;
  const opening = openWorkshopGate(Buffer.from('disposable'), {
    signal: controller.signal,
    launch() {
      const process = new EventEmitter() as ChildProcess;
      process.kill = (() => {
        killed = true;
        process.emit('exit', 0);
        return true;
      }) as ChildProcess['kill'];
      return process;
    },
  });
  // 偽Chromeはreadyを返さない。中断で待機を抜け、子を片付ける。
  setTimeout(() => controller.abort(), 40);
  await assert.rejects(opening, /中断/);
  assert.equal(killed, true);
});
