import assert from 'node:assert/strict'
import fs from 'node:fs'
import { syncBuiltinESMExports } from 'node:module'
import { execFile } from 'node:child_process'
import { renameSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, utimes, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { promisify } from 'node:util'
import test from 'node:test'

import { acquireDirectoryLock, withDirectoryLock } from './file-lock.mjs'

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'nirai-file-lock-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  return path.join(root, 'workflow.lock')
}

test('directory lock serializes competing workflow writers and fails closed on timeout', async (t) => {
  const lockPath = await fixture(t)
  const release = await acquireDirectoryLock(lockPath, { timeoutMs: 100 })
  await assert.rejects(
    acquireDirectoryLock(lockPath, { timeoutMs: 20, retryMs: 2 }),
    /Timed out waiting for lock/
  )
  await release()
  await withDirectoryLock(lockPath, async () => undefined, { timeoutMs: 100 })
})

test('a dead stale owner can be reclaimed without stealing a live lock', async (t) => {
  const lockPath = await fixture(t)
  await mkdir(lockPath, { recursive: true })
  await writeFile(path.join(lockPath, 'owner.json'), JSON.stringify({
    version: 1,
    token: 'dead-owner',
    pid: 2147483647,
    acquired_at: '2000-01-01T00:00:00.000Z'
  }))
  const old = new Date('2000-01-01T00:00:00.000Z')
  await utimes(lockPath, old, old)
  const release = await acquireDirectoryLock(lockPath, { timeoutMs: 100, staleMs: 1, retryMs: 2 })
  await release()
})

test('a recorded dead owner is reclaimable without waiting for the orphan grace period', async (t) => {
  const lockPath = await fixture(t)
  await mkdir(lockPath, { recursive: true })
  await writeFile(path.join(lockPath, 'owner.json'), JSON.stringify({
    version: 1,
    token: 'fresh-dead-owner',
    pid: 2147483647,
    acquired_at: new Date().toISOString()
  }))
  const release = await acquireDirectoryLock(lockPath, {
    timeoutMs: 100,
    staleMs: 300_000,
    retryMs: 2
  })
  await release()
})

test('an interrupted owner write can be reclaimed after the orphan grace period', async (t) => {
  const lockPath = await fixture(t)
  await mkdir(lockPath, { recursive: true })
  await writeFile(path.join(lockPath, 'owner.json'), '{"pid":')
  const old = new Date('2000-01-01T00:00:00.000Z')
  await utimes(lockPath, old, old)
  await withDirectoryLock(lockPath, async () => undefined, { timeoutMs: 100, staleMs: 1 })
})

test('an incomplete legacy owner keeps its orphan grace period', async (t) => {
  const lockPath = await fixture(t)
  await mkdir(lockPath, { recursive: true })
  await writeFile(path.join(lockPath, 'owner.json'), '{"pid":')
  await assert.rejects(acquireDirectoryLock(lockPath, {
    timeoutMs: 20, staleMs: 300_000, retryMs: 2
  }), /Timed out waiting for lock/)
})

test('an interrupted release leaves an empty directory that is immediately reclaimable', async (t) => {
  const lockPath = await fixture(t)
  await mkdir(lockPath, { recursive: true })
  await withDirectoryLock(lockPath, async () => undefined, { timeoutMs: 100, staleMs: 300_000 })
})

test('a stale observation cannot reclaim a replacement live owner', async (t) => {
  const lockPath = await fixture(t)
  await mkdir(lockPath, { recursive: true })
  await writeFile(path.join(lockPath, 'owner.json'), JSON.stringify({ pid: 2147483647, token: 'dead' }))

  // Prepare the replacement using the real acquisition format. Another
  // process publishes it just after this contender observes the dead PID.
  const replacement = `${lockPath}.replacement`
  await acquireDirectoryLock(replacement)
  const originalKill = process.kill
  let replaced = false
  t.mock.method(process, 'kill', (pid, signal) => {
    if (pid !== 2147483647) return originalKill.call(process, pid, signal)
    if (!replaced) {
      replaced = true
      renameSync(lockPath, `${lockPath}.old`)
      renameSync(replacement, lockPath)
    }
    throw Object.assign(new Error('No such process'), { code: 'ESRCH' })
  })
  let entered = false
  await assert.rejects(withDirectoryLock(lockPath, async () => { entered = true }, {
    timeoutMs: 30, retryMs: 2
  }), /Timed out waiting for lock/)
  assert.equal(replaced, true)
  assert.equal(entered, false)
})

test('a published owner is reclaimable after its process exits', async (t) => {
  const lockPath = await fixture(t)
  const moduleUrl = new URL('./file-lock.mjs', import.meta.url).href
  await promisify(execFile)(process.execPath, ['--input-type=module', '-e', `
    const { acquireDirectoryLock } = await import(process.argv[1]);
    await acquireDirectoryLock(process.argv[2]);
  `, moduleUrl, lockPath], { timeout: 5_000, windowsHide: true })
  await withDirectoryLock(lockPath, async () => undefined, { timeoutMs: 100 })
})

test('a transient Windows directory-list denial during lock release retries the whole observation', async (t) => {
  const lockPath = await fixture(t)
  const release = await acquireDirectoryLock(lockPath)
  const original = fs.promises.readdir
  let denied = 0
  const mocked = t.mock.method(fs.promises, 'readdir', async (...args) => {
    if (args[0] === lockPath && denied++ < 2) throw Object.assign(new Error('Windows hand-off'), { code: 'EPERM' })
    return original(...args)
  })
  syncBuiltinESMExports()
  try {
    await release()
    assert.equal(denied, 3)
  } finally {
    mocked.mock.restore()
    syncBuiltinESMExports()
  }
})

test('independent processes serialize read-modify-write operations through the lock', async (t) => {
  const lockPath = await fixture(t)
  const counterPath = path.join(path.dirname(lockPath), 'counter.json')
  await writeFile(counterPath, '0')
  const moduleUrl = new URL('./file-lock.mjs', import.meta.url).href
  const operation = `
    const { withDirectoryLock } = await import(process.argv[1]);
    const { readFile, writeFile } = await import('node:fs/promises');
    for (let index = 0; index < 5; index += 1) {
      await withDirectoryLock(process.argv[2], async () => {
        const before = JSON.parse(await readFile(process.argv[3], 'utf8'));
        await new Promise((resolve) => setTimeout(resolve, 5));
        await writeFile(process.argv[3], JSON.stringify(before + 1));
      }, { timeoutMs: 5_000, retryMs: 1 });
    }
  `
  const results = await Promise.allSettled(Array.from({ length: 4 }, () => (
    promisify(execFile)(process.execPath, ['--input-type=module', '-e', operation,
      moduleUrl, lockPath, counterPath], { timeout: 10_000, windowsHide: true })
  )))
  for (const result of results) assert.equal(result.status, 'fulfilled', result.reason?.stack)
  assert.equal(JSON.parse(await readFile(counterPath, 'utf8')), 20)
})
