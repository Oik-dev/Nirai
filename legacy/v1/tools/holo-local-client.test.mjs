import assert from 'node:assert/strict'
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'

const source = dirname(fileURLToPath(import.meta.url))
const diveA = '11111111-1111-4111-8111-111111111111'
const diveB = '22222222-2222-4222-8222-222222222222'

async function activityFixture(t) {
  const root = await mkdtemp(join(os.tmpdir(), 'nirai-workflow-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  await mkdir(join(root, 'tools'), { recursive: true })
  await mkdir(join(root, 'runtime', 'holo'), { recursive: true })
  for (const name of ['holo-workflow.mjs', 'holo-transport.mjs']) {
    await copyFile(join(source, name), join(root, 'tools', name))
  }
  const workflowPath = join(root, 'runtime', 'holo', 'workflow.json')
  const setDive = (id, url = `https://chatgpt.com/c/${id}`) => writeFile(
    join(root, 'runtime', 'holo', 'state.json'),
    JSON.stringify({ current_dive_session_id: id, current_dive_url: url })
  )
  const lease = { version: 1, workflow_id: '33333333-3333-4333-8333-333333333333',
    dive_session_id: diveA, conversation_url: `https://chatgpt.com/c/${diveA}`,
    state: 'active', started_at: new Date().toISOString(), updated_at: new Date().toISOString() }
  await writeFile(workflowPath, JSON.stringify(lease))
  await setDive(diveA)
  const requests = []
  const request = async (value) => { requests.push(value); return { ok: true, operation: value.payload.action } }
  const store = await import(pathToFileURL(join(root, 'tools', 'holo-workflow.mjs')).href)
  return { root, lease, setDive, requests, request, ...store,
    readLease: async () => JSON.parse(await readFile(workflowPath, 'utf8')),
    withWorkflowActivity: (context, action) => store.withWorkflowActivity({ request, ...context }, action),
    client: (...args) => store.runWorkflowCommand(args, request) }
}

test('current Dive URL wins over a stale known_dive_urls alias when routing new work', async (t) => {
  const f = await activityFixture(t)
  const canonicalUrl = 'https://chatgpt.com/c/browser-canonical-current'
  const staleAlias = 'https://chatgpt.com/c/WEB:stale-routing-alias'
  await writeFile(join(f.root, 'runtime', 'holo', 'state.json'), JSON.stringify({
    current_dive_session_id: diveA,
    current_dive_url: canonicalUrl,
    known_dive_urls: { [diveA]: staleAlias }
  }), 'utf8')
  const context = await f.resolveDiveContext(diveA)
  assert.equal(context.diveSessionId, diveA)
  assert.equal(context.conversationUrl, canonicalUrl)
})


test('commands forward exact ownership to Core and never write the lease', async (t) => {
  const f = await activityFixture(t)
  for (const args of [
    ['workflow-start', diveA, 'Work'], ['workflow-status', diveA],
    ['workflow-complete', f.lease.workflow_id], ['workflow-cancel', diveA, f.lease.workflow_id],
    ['workflow-resolve', f.lease.workflow_id, 'T-A', 'superseded', 'T-B', 'Verified repair']
  ]) assert.equal((await f.client(...args)).handled, true)
  assert.deepEqual(f.requests.map(x => x.payload.action), ['start', 'status', 'complete', 'cancel', 'resolve'])
  assert.deepEqual(f.requests.at(-1).payload, { action: 'resolve', workflow_id: f.lease.workflow_id,
    task_id: 'T-A', resolution: 'superseded', replacement_task_id: 'T-B', note: 'Verified repair' })
  assert.equal(f.requests[2].payload.workflow_id, f.lease.workflow_id)
  assert.equal(f.requests[3].payload.dive_session_id, diveA)
  assert.deepEqual(await f.readLease(), f.lease)
})

test('completion and cancellation require exact ID; implicit heartbeat verifies owner', async (t) => {
  const f = await activityFixture(t)
  for (const action of ['workflow-complete', 'workflow-cancel']) {
    for (const args of [[], [diveA]]) await assert.rejects(f.client(action, ...args), /exact workflow_id/)
  }
  await f.setDive(diveB)
  await assert.rejects(f.client('workflow-heartbeat'), /different Dive/)
  await f.setDive(diveA, `https://chatgpt.com/c/WEB:${diveA}?model=test#latest`)
  await f.client('workflow-heartbeat')
  assert.equal(f.requests.at(-1).payload.workflow_id, f.lease.workflow_id)
  await f.setDive(diveA, 'https://chatgpt.com/c/different')
  await assert.rejects(f.client('workflow-heartbeat'), /different Dive/)
})

test('monitoring and unsuccessful work cannot prolong a lease', async (t) => {
  const f = await activityFixture(t)
  const workflowId = f.lease.workflow_id
  for (const operation of ['workflow-status', 'snapshot', 'wait', 'skills', 'incidents', 'task-targets']) {
    assert.equal(f.isWorkflowActivity(operation), false)
    await f.withWorkflowActivity({ workflowId, enabled: false }, async () => ({ ok: true }))
  }
  for (const result of [{ ok: false }, { isError: true }, { exitCode: 1 }, { timedOut: true }, { timed_out: true },
    { structuredContent: { exitCode: 9 } }]) {
    await f.withWorkflowActivity({ workflowId }, async () => result)
  }
  await assert.rejects(f.withWorkflowActivity({ workflowId }, async () => { throw new Error('rejected') }))
  assert.equal(f.requests.length, 0)
})

test('only explicit identity or a verified owner records activity', async (t) => {
  const f = await activityFixture(t)
  const owner = { workflow_id: f.lease.workflow_id, dive_session_id: diveA, conversation_url: f.lease.conversation_url }
  for (const workflow_owner of [undefined, { ...owner, workflow_id: 'old' }, { ...owner, dive_session_id: diveB },
    { ...owner, conversation_url: 'https://chatgpt.com/c/foreign' }]) {
    await f.withWorkflowActivity({}, async () => ({ ok: true, workflow_owner }))
  }
  assert.equal(f.requests.length, 0)
  await f.withWorkflowActivity({}, async () => ({ ok: true, workflow_owner: owner }))
  await f.withWorkflowActivity({ workflowId: f.lease.workflow_id }, async () => ({ ok: true }))
  assert.equal(f.requests.length, 2)
  assert.ok(f.requests.every(x => x.payload.workflow_id === f.lease.workflow_id))
  assert.deepEqual(await f.readLease(), f.lease)
})

test('activity captures the old identity while work runs and preserves work on receipt failure', async (t) => {
  const f = await activityFixture(t)
  const workflowId = f.lease.workflow_id
  await f.withWorkflowActivity({ workflowId }, async () => {
    await writeFile(join(f.root, 'runtime/holo/workflow.json'), JSON.stringify({ ...f.lease, workflow_id: 'replacement' }))
    return { ok: true }
  })
  assert.equal(f.requests.at(-1).payload.workflow_id, workflowId) // Core rejects the stale receipt.
  await writeFile(join(f.root, 'runtime/holo/workflow.json'), JSON.stringify(f.lease))
  let executions = 0
  const result = await f.withWorkflowActivity({ workflowId, request: async () => { throw new Error('offline') } }, async () => {
    executions += 1
    return { ok: true, task: { task_id: 'T-SUCCEEDED' } }
  })
  assert.equal(result.task.task_id, 'T-SUCCEEDED')
  assert.equal(executions, 1)
  assert.equal(result.workflow_activity_warning, 'offline')
})

test('Task admission captures the same saved owner and does not infer another Dive', async (t) => {
  const f = await activityFixture(t)
  assert.equal(await f.workflowForOwner(diveA, `https://chatgpt.com/c/WEB:${diveA}`), f.lease.workflow_id)
  assert.equal(await f.workflowForOwner(diveB, f.lease.conversation_url), undefined)
  assert.equal(await f.workflowForOwner(diveA, 'https://chatgpt.com/c/foreign'), undefined)
  await writeFile(join(f.root, 'runtime/holo/workflow.json'), JSON.stringify({ ...f.lease, state: 'completed' }))
  assert.equal(await f.workflowForOwner(diveA, f.lease.conversation_url), undefined)
})
