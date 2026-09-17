// Semantic MCP entrypoints. Core owns all Workflow state transitions.
export function registerWorkflowTools(server, z, run) {
  const id = () => z.string().min(1).max(128)
  const register = (name, title, description, inputSchema, action, args, readOnly = false) => {
    server.registerTool(name, {
      title, description, inputSchema,
      annotations: { readOnlyHint: readOnly, destructiveHint: false, idempotentHint: true, openWorldHint: false }
    }, async (input) => {
      const result = await run(`workflow-${action}`, args(input))
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
        structuredContent: result, ...(result?.ok === false ? { isError: true } : {}) }
    })
  }
  const ownerArgs = ({ diveSessionId, workflowId }) => [diveSessionId, workflowId].filter(Boolean)
  register('nirai_holo_workflow_status', 'Read Nirai Holo Workflow',
    'Read the current Core Workflow and unfinished Task blockers. Observation never refreshes activity.',
    { diveSessionId: id().optional() }, 'status', ({ diveSessionId }) => [diveSessionId].filter(Boolean), true)
  register('nirai_holo_workflow_start', 'Start Nirai Holo Workflow',
    'Create or refresh the Workflow owned by this Dive Conversation. Keep its workflow_id for subsequent work. Core records Task membership; this operation does not start an Agent.',
    { diveSessionId: id().optional(), label: z.string().min(1).max(200) },
    'start', ({ diveSessionId, label }) => [...[diveSessionId].filter(Boolean), label])
  for (const name of ['nirai_holo_workflow_heartbeat', 'nirai_holo_heartbeat']) {
    register(name, 'Legacy Nirai Holo Workflow Heartbeat',
      'Compatibility only. Ordinary owned work records activity automatically. Do not use heartbeat from Dive or Auto Resume.',
      { diveSessionId: id().optional(), workflowId: id().optional() }, 'heartbeat', ownerArgs)
  }
  register('nirai_holo_workflow_complete', 'Complete Nirai Holo Workflow',
    'Complete the exact workflow_id after all work is handled. Core refuses queued, running, waiting, interrupted, unresolved failed, unknown or finalizing Tasks and returns blockers. Retrieve results and resolve the work before retrying. World changes require the final successful build.',
    { diveSessionId: id().optional(), workflowId: id() }, 'complete', ownerArgs)
  register('nirai_holo_workflow_resolve', 'Resolve a Nirai Holo Workflow Task',
    'Record how stopped work was handled: superseded by another successfully completed Task in this Workflow, or explicitly abandoned within the approved scope. Supply a reason. Interrupted attempts are retired through Agent Runtime. This keeps failure history and never changes it to success; running Tasks must first be stopped.',
    { workflowId: id(), taskId: id(), resolution: z.enum(['superseded', 'abandoned']),
      replacementTaskId: id().optional(), note: z.string().min(1).max(2000) }, 'resolve',
    ({ workflowId, taskId, resolution, replacementTaskId, note }) =>
      [workflowId, taskId, resolution, replacementTaskId ?? '-', note])
}
