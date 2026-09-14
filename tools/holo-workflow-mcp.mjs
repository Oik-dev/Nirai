import { withWorkflowActivity } from './holo-workflow.mjs'

const MONITOR_TOOLS = new Set([
  'health_check', 'list_process_jobs', 'list_allowed_directories', 'list_npm_scripts'
])

export function isMcpWorkflowActivity(name, args) {
  if (name.startsWith('nirai_holo_') || MONITOR_TOOLS.has(name)) return false
  // Delegate Local Client calls to its semantic operation policy. Otherwise
  // `run_process workflow-status` would turn pure monitoring into activity.
  if (Array.isArray(args.args) && args.args.some((arg) =>
    typeof arg === 'string' && /(?:^|[\\/])holo-local-client\.mjs$/i.test(arg))) return false
  return true
}

// Installed at the existing MCP registration boundary, after permission/path
// guards. No per-tool lease writers, implicit session binding, or timer worker.
export function workflowActivityTool(name, config, handler, z, observe = withWorkflowActivity) {
  if (name.startsWith('nirai_holo_') || MONITOR_TOOLS.has(name)) return { config, handler }
  return {
    config: {
      ...config,
      description: `${config.description ?? ''} During owned Nirai work, supply workflowId to record successful work as activity. Omit it for monitoring.`,
      annotations: { ...config.annotations, readOnlyHint: false },
      inputSchema: {
        ...config.inputSchema,
        workflowId: z.string().min(1).max(128).optional()
      }
    },
    handler: async (args, extra) => {
      const { workflowId, ...workArgs } = args
      return observe({ workflowId, enabled: Boolean(workflowId) && isMcpWorkflowActivity(name, workArgs) },
        () => handler(workArgs, extra))
    }
  }
}
