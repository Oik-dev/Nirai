import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, rename, symlink, utimes, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  assertWorldBuildReadyForWorkflowCompletion,
  computeWorldBuildFingerprint,
  readWorldBuildStamp,
  runWorldBuild
} from './world-build-state.mjs'

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'nirai-world-build-'))
  t.after(async () => {
    const { rm } = await import('node:fs/promises')
    await rm(root, { recursive: true, force: true })
  })
  await mkdir(path.join(root, 'world', 'src'), { recursive: true })
  await mkdir(path.join(root, 'world', 'public'), { recursive: true })
  await mkdir(path.join(root, 'world', 'resources'), { recursive: true })
  await writeFile(path.join(root, 'world', 'src', 'main.ts'), 'export const value = 1\n')
  await writeFile(path.join(root, 'world', 'package.json'), '{"name":"test"}\n')
  await writeFile(path.join(root, 'world', 'package-lock.json'), '{}\n')
  await writeFile(path.join(root, 'world', 'tsconfig.json'), '{}\n')
  await writeFile(path.join(root, 'world', 'electron.vite.config.ts'), 'export default {}\n')
  return root
}

const fakeSuccessfulBuild = async () => undefined

test('workflow completion does not require build when World inputs are unchanged', async (t) => {
  const root = await fixture(t)
  const start = await computeWorldBuildFingerprint(root)
  const result = await assertWorldBuildReadyForWorkflowCompletion(start, root)
  assert.equal(result.required, false)
})

test('workflow completion requires one successful final build after World inputs change', async (t) => {
  const root = await fixture(t)
  const start = await computeWorldBuildFingerprint(root)
  await writeFile(path.join(root, 'world', 'src', 'main.ts'), 'export const value = 2\n')

  await assert.rejects(
    assertWorldBuildReadyForWorkflowCompletion(start, root),
    /Finish implementation and verification first, then run npm run build once/i
  )

  const stamp = await runWorldBuild(root, {
    builder: fakeSuccessfulBuild,
    now: () => new Date('2026-09-13T00:00:00Z')
  })
  assert.equal(stamp.version, 2)
  const ready = await assertWorldBuildReadyForWorkflowCompletion(start, root)
  assert.equal(ready.required, true)

  await writeFile(path.join(root, 'world', 'src', 'main.ts'), 'export const value = 3\n')
  await assert.rejects(assertWorldBuildReadyForWorkflowCompletion(start, root), /workflow-complete blocked/i)
})

test('same-size edits with preserved timestamps still invalidate a successful build', async (t) => {
  const root = await fixture(t)
  const source = path.join(root, 'world', 'src', 'main.ts')
  const timestamp = new Date('2026-09-13T00:00:00Z')
  await utimes(source, timestamp, timestamp)
  const start = await computeWorldBuildFingerprint(root)
  await runWorldBuild(root, { builder: fakeSuccessfulBuild })

  await writeFile(source, 'export const value = 2\n')
  await utimes(source, timestamp, timestamp)

  assert.notEqual(await computeWorldBuildFingerprint(root), start)
  await assert.rejects(assertWorldBuildReadyForWorkflowCompletion(start, root), /workflow-complete blocked/i)
})

test('a source edit after build consumption cannot be certified by the success stamp', async (t) => {
  const root = await fixture(t)
  const source = path.join(root, 'world', 'src', 'main.ts')
  const output = path.join(root, 'world', 'built-main.ts')
  await runWorldBuild(root, { builder: fakeSuccessfulBuild })

  await assert.rejects(runWorldBuild(root, {
    builder: async () => {
      await writeFile(output, await readFile(source))
      await writeFile(source, 'export const value = 9\n')
    }
  }), /inputs changed while electron-vite was building/i)
  assert.equal(await readWorldBuildStamp(root), null)
  assert.equal(await readFile(output, 'utf8'), 'export const value = 1\n')
  assert.equal(await readFile(source, 'utf8'), 'export const value = 9\n')
})

test('a source edit reverted before build exit still cannot be certified', async (t) => {
  const root = await fixture(t)
  const source = path.join(root, 'world', 'src', 'main.ts')
  const original = await readFile(source, 'utf8')
  const before = await computeWorldBuildFingerprint(root)

  await assert.rejects(runWorldBuild(root, {
    builder: async () => {
      await writeFile(source, 'export const value = 7\n')
      await new Promise((resolve) => setTimeout(resolve, 5))
      await writeFile(source, original)
    }
  }), /inputs changed while electron-vite was building/i)

  assert.equal(await readFile(source, 'utf8'), original)
  assert.notEqual(await computeWorldBuildFingerprint(root), before)
  assert.equal(await readWorldBuildStamp(root), null)
})

test('a conflicting World build is refused while another build owns the lock', async (t) => {
  const root = await fixture(t)
  let enteredResolve
  let releaseResolve
  const entered = new Promise((resolve) => { enteredResolve = resolve })
  const release = new Promise((resolve) => { releaseResolve = resolve })
  const first = runWorldBuild(root, {
    builder: async () => {
      enteredResolve()
      await release
    },
    lockTimeoutMs: 1_000
  })
  await entered
  await assert.rejects(
    runWorldBuild(root, { builder: fakeSuccessfulBuild, lockTimeoutMs: 20 }),
    /Timed out waiting for lock/
  )
  releaseResolve()
  await first
})

test('fingerprinting refuses directory links and does not traverse cycles', async (t) => {
  const root = await fixture(t)
  await symlink(root, path.join(root, 'world', 'src', 'cycle'), process.platform === 'win32' ? 'junction' : 'dir')
  await assert.rejects(computeWorldBuildFingerprint(root), /without symbolic links/)
})

test('fingerprinting rejects an ancestor World link escaping the project', async (t) => {
  const root = await fixture(t)
  const outside = await fixture(t)
  await rename(path.join(root, 'world'), path.join(root, 'saved-world'))
  await symlink(path.join(outside, 'world'), path.join(root, 'world'), process.platform === 'win32' ? 'junction' : 'dir')
  await assert.rejects(computeWorldBuildFingerprint(root), /must stay inside the project/)
})
