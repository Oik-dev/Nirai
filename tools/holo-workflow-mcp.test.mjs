import assert from 'node:assert/strict'
import test from 'node:test'
import { isMcpWorkflowActivity, workflowActivityTool } from './holo-workflow-mcp.mjs'

const stringSchema = { min() { return this }, max() { return this }, optional() { return this } }
const z = { string: () => stringSchema }

test('one MCP boundary covers new work tools and passes ownership separately from work arguments', async () => {
  const observed = []
  const action = async (args, extra) => ({ args, extra })
  const observe = async (context, handler) => { observed.push(context); return handler() }
  const tool = workflowActivityTool('future_work_operation', { inputSchema: {}, annotations: { readOnlyHint: true } }, action, z, observe)
  assert.ok(tool.config.inputSchema.workflowId)
  assert.equal(tool.config.annotations.readOnlyHint, false)
  const result = await tool.handler({ workflowId: 'wf-1', path: 'work.txt' }, 'context')
  assert.deepEqual(result, { args: { path: 'work.txt' }, extra: 'context' })
  assert.deepEqual(observed, [{ workflowId: 'wf-1', enabled: true }])
  await tool.handler({ path: 'monitor.txt' })
  assert.equal(observed[1].enabled, false)
})

test('MCP monitor, health, and nested Local Client commands never masquerade as work', () => {
  for (const name of ['health_check', 'list_process_jobs', 'list_allowed_directories', 'nirai_holo_workflow_status']) {
    assert.equal(isMcpWorkflowActivity(name, {}), false)
  }
  assert.equal(isMcpWorkflowActivity('run_process', { args: ['tools\\holo-local-client.mjs', 'workflow-status'] }), false)
  assert.equal(isMcpWorkflowActivity('get_process_job', { jobId: 'owned-job' }), true)
})
