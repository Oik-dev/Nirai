import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { copyFile, mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { promisify } from 'node:util'
import test from 'node:test'

const source = dirname(fileURLToPath(import.meta.url))
const diveA = '11111111-1111-4111-8111-111111111111'
const diveB = '22222222-2222-4222-8222-222222222222'

async function fixture(t) {
  const root = await mkdtemp(join(os.tmpdir(), 'nirai-workflow-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  await mkdir(join(root, 'tools'), { recursive: true })
  await mkdir(join(root, 'runtime', 'holo'), { recursive: true })
  for (const name of ['holo-local-client.mjs', 'holo-workflow.mjs', 'world-build-state.mjs', 'file-lock.mjs']) {
    await copyFile(join(source, name), join(root, 'tools', name))
  }
  const workflowPath = join(root, 'runtime', 'holo', 'workflow.json')
  const setDive = (id, url = `https://chatgpt.com/c/${id}`) => writeFile(
    join(root, 'runtime', 'holo', 'state.json'),
    JSON.stringify({ current_dive_session_id: id, current_dive_url: url })
  )
  const readLease = async () => JSON.parse(await readFile(workflowPath, 'utf8'))
  const client = async (...args) => {
    try {
      const { stdout } = await promisify(execFile)(process.execPath,
        [join(root, 'tools', 'holo-local-client.mjs'), ...args], { cwd: root, timeout: 15_000 })
      return JSON.parse(stdout)
    } catch (error) {
      if (typeof error.stderr !== 'string' || !error.stderr.trim()) throw error
      return JSON.parse(error.stderr.trim())
    }
  }
  await setDive(diveA)
  const started = await client('workflow-start', diveA, 'Test workflow')
  assert.equal(started.ok, true)
  return { root, client, setDive, readLease, lease: started.result.workflow }
}

async function activityFixture(t) {
  const f = await fixture(t)
  const store = await import(pathToFileURL(join(f.root, 'tools', 'holo-workflow.mjs')).href)
  return { ...f, ...store }
}

test('owned work maintains a long workflow without a dedicated heartbeat and survives restart', async (t) => {
  const f = await activityFixture(t)
  const workflowId = f.lease.workflow_id
  let now = Date.parse(f.lease.updated_at)
  t.mock.method(Date, 'now', () => now)
  for (const operation of ['task-start', 'task-snapshot', 'task-wait', 'review', 'review-wait', 'task-respond']) {
    now += 25_000
    assert.equal(f.isWorkflowActivity(operation), true)
    await f.withWorkflowActivity({ workflowId }, async () => ({ ok: true, operation }))
    const status = await f.client('workflow-status', diveA)
    assert.equal(status.result.workflow.updated_at, new Date(now).toISOString())
    assert.equal(status.result.workflow.workflow_id, workflowId)
  }
  assert.equal((await f.readLease()).state, 'active')
})

test('monitoring and unsuccessful work cannot prolong a lease', async (t) => {
  const f = await activityFixture(t)
  const before = await f.readLease()
  for (const operation of ['workflow-status', 'snapshot', 'wait', 'skills', 'incidents', 'task-targets']) {
    assert.equal(f.isWorkflowActivity(operation), false)
    await f.withWorkflowActivity({ workflowId: before.workflow_id, enabled: f.isWorkflowActivity(operation) },
      async () => ({ ok: true }))
  }
  for (const result of [{ ok: false }, { isError: true }, { exitCode: 1 }, { timedOut: true },
    { structuredContent: { exitCode: 9 } }]) {
    await f.withWorkflowActivity({ workflowId: before.workflow_id }, async () => result)
  }
  await assert.rejects(f.withWorkflowActivity({ workflowId: before.workflow_id }, async () => { throw new Error('rejected') }))
  await f.withWorkflowActivity({ workflowId: before.workflow_id, enabled: false }, async () => ({ ok: true }))
  assert.deepEqual(await f.readLease(), before)
})

test('only a verified work owner can infer activity; foreground Dive is never sufficient', async (t) => {
  const f = await activityFixture(t)
  const before = await f.readLease()
  const owner = { workflow_id: before.workflow_id, dive_session_id: diveA, conversation_url: before.conversation_url }
  for (const workflow_owner of [undefined, { ...owner, workflow_id: 'old' }, { ...owner, dive_session_id: diveB },
    { ...owner, conversation_url: 'https://chatgpt.com/c/foreign' }]) {
    await f.withWorkflowActivity({}, async () => ({ ok: true, workflow_owner }))
  }
  assert.deepEqual(await f.readLease(), before)
  await f.withWorkflowActivity({}, async () => ({ ok: true, workflow_owner: owner }))
  assert.notEqual((await f.readLease()).updated_at, before.updated_at)
})

test('a delayed activity cannot resurrect completed work or touch its replacement', async (t) => {
  const f = await activityFixture(t)
  const workflowId = f.lease.workflow_id
  await f.withWorkflowActivity({ workflowId }, async () => {
    await f.client('workflow-complete', workflowId)
    return { ok: true }
  })
  assert.equal((await f.readLease()).state, 'completed')
  await f.client('workflow-start', diveA, 'Replacement')
  const replacement = await f.readLease()
  await f.withWorkflowActivity({ workflowId }, async () => ({ ok: true }))
  assert.deepEqual(await f.readLease(), replacement)
  await f.withWorkflowActivity({ workflowId: replacement.workflow_id }, async () => {
    await f.client('workflow-cancel', replacement.workflow_id)
    await f.client('workflow-start', diveA, 'Next replacement')
    return { ok: true }
  })
  const last = await f.readLease()
  assert.notEqual(last.workflow_id, replacement.workflow_id)
  assert.equal(last.state, 'active')
})

test('parallel activities serialize revision updates without replacing the workflow identity', async (t) => {
  const f = await activityFixture(t)
  const before = await f.readLease()
  t.mock.method(Date, 'now', () => Date.parse(before.updated_at))
  await Promise.all(Array.from({ length: 5 }, () => f.withWorkflowActivity({ workflowId: before.workflow_id }, async () => ({ ok: true }))))
  const after = await f.readLease()
  assert.equal(Date.parse(after.updated_at), Date.parse(before.updated_at) + 5)
  assert.equal(after.workflow_id, before.workflow_id)
  assert.equal(after.started_at, before.started_at)
})

test('a lease persistence failure preserves successful work instead of inviting a duplicate execution', async (t) => {
  const f = await activityFixture(t)
  let executions = 0
  const workflowPath = join(f.root, 'runtime', 'holo', 'workflow.json')
  const result = await f.withWorkflowActivity({ workflowId: f.lease.workflow_id }, async () => {
    executions += 1
    await rename(workflowPath, `${workflowPath}.saved`)
    await mkdir(workflowPath) // Deterministically make the receipt path unreadable as JSON.
    return { ok: true, task: { task_id: 'T-SUCCEEDED' } }
  })
  assert.equal(result.ok, true)
  assert.equal(result.task.task_id, 'T-SUCCEEDED')
  assert.equal(executions, 1)
  assert.equal(typeof result.workflow_activity_warning, 'string')
})

test('simultaneous workflow starts share one active workflow and refuse another Dive', async (t) => {
  const f = await activityFixture(t)
  const results = await Promise.all(Array.from({ length: 4 }, () => f.client('workflow-start', diveA, 'same request')))
  assert.ok(results.every((result) => result.result.workflow.workflow_id === f.lease.workflow_id))
  await f.setDive(diveB)
  assert.equal((await f.client('workflow-start', diveB, 'other')).ok, false)
})

test('workflowId-only heartbeat and completion accept the generated UUID', async (t) => {
  const f = await fixture(t)
  assert.equal((await f.client('workflow-heartbeat', f.lease.workflow_id)).ok, true)
  const refreshed = await f.readLease()
  assert.equal((await f.client('workflow-complete', refreshed.workflow_id)).ok, true)
  assert.equal((await f.readLease()).state, 'completed')
})

test('workflow completion requires the exact workflow ID even in the owning Dive', async (t) => {
  const f = await fixture(t)
  for (const args of [[], [diveA]]) {
    const before = await f.readLease()
    const result = await f.client('workflow-complete', ...args)
    assert.equal(result.ok, false)
    assert.match(result.error, /requires the exact workflow_id/)
    assert.deepEqual(await f.readLease(), before)
  }
})

test('a delayed completion from an older workflow cannot complete its replacement in the same Dive', async (t) => {
  const f = await fixture(t)
  const oldWorkflowId = f.lease.workflow_id
  assert.equal((await f.client('workflow-complete', diveA, oldWorkflowId)).ok, true)
  const next = await f.client('workflow-start', diveA, 'Next workflow')
  assert.equal(next.ok, true)
  const newWorkflowId = next.result.workflow.workflow_id
  assert.notEqual(newWorkflowId, oldWorkflowId)

  const diveOnlyRetry = await f.client('workflow-complete', diveA)
  assert.equal(diveOnlyRetry.ok, false)
  assert.match(diveOnlyRetry.error, /requires the exact workflow_id/)
  const oldIdRetry = await f.client('workflow-complete', diveA, oldWorkflowId)
  assert.equal(oldIdRetry.ok, false)
  assert.match(oldIdRetry.error, /workflow_id does not match/)
  assert.equal((await f.readLease()).workflow_id, newWorkflowId)
  assert.equal((await f.readLease()).state, 'active')
})

test('implicit heartbeat cannot mutate another current Dive', async (t) => {
  const f = await fixture(t)
  await f.setDive(diveB)
  const before = await f.readLease()
  const result = await f.client('workflow-heartbeat')
  assert.equal(result.ok, false)
  assert.match(result.error, /different Dive or Conversation/)
  assert.deepEqual(await f.readLease(), before)
  assert.equal((await f.client('workflow-heartbeat', f.lease.workflow_id)).ok, true)
})

test('mismatched Dive and workflow IDs leave the active lease intact', async (t) => {
  const f = await fixture(t)
  for (const args of [[diveB, f.lease.workflow_id], [diveA, 'wrong-workflow']]) {
    const before = await f.readLease()
    assert.equal((await f.client('workflow-complete', ...args)).ok, false)
    assert.deepEqual(await f.readLease(), before)
  }
})

test('default heartbeat accepts Conversation URL aliases but refuses a different Conversation in the same Dive', async (t) => {
  const f = await fixture(t)
  await f.setDive(diveA, `https://chatgpt.com/c/WEB:${diveA}?model=test#latest`)
  assert.equal((await f.client('workflow-heartbeat')).ok, true)
  assert.equal((await f.client('workflow-start', diveA, 'Resume same Conversation')).ok, true)
  const before = await f.readLease()
  await f.setDive(diveA, 'https://chatgpt.com/c/different')
  assert.equal((await f.client('workflow-heartbeat')).ok, false)
  assert.deepEqual(await f.readLease(), before)
})

test('Master cancellation requires the exact workflow ID and does not run the build guard', async (t) => {
  const f = await fixture(t)
  await mkdir(join(f.root, 'world', 'src'), { recursive: true })
  await writeFile(join(f.root, 'world', 'src', 'main.ts'), 'changed after workflow start\n')
  assert.equal((await f.client('workflow-cancel', diveA)).ok, false)
  const cancelled = await f.client('workflow-cancel', diveA, f.lease.workflow_id)
  assert.equal(cancelled.ok, true)
  assert.equal(cancelled.result.workflow.state, 'completed')
  assert.equal(cancelled.result.workflow.completion_reason, 'cancelled_by_master')
})

test('blocked completion leaves the workflow active and keeps its original build baseline', async (t) => {
  const f = await fixture(t)
  await mkdir(join(f.root, 'world', 'src'), { recursive: true })
  await writeFile(join(f.root, 'world', 'src', 'main.ts'), 'export const changed = true\n')
  const resumed = await f.client('workflow-start', diveA, 'Continue test workflow')
  assert.equal(resumed.result.workflow.world_build_fingerprint_at_start, f.lease.world_build_fingerprint_at_start)
  const before = await f.readLease()
  const result = await f.client('workflow-complete', diveA, f.lease.workflow_id)
  assert.equal(result.ok, false)
  assert.match(result.error, /workflow-complete blocked/)
  assert.deepEqual(await f.readLease(), before)
})
