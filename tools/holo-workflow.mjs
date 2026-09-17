import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { callHolo } from './holo-transport.mjs'

export const NIRAI_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const HOLO_RUNTIME_ROOT = join(NIRAI_ROOT, 'runtime', 'holo')
const HOLO_WORKFLOW_STATE_PATH = join(HOLO_RUNTIME_ROOT, 'workflow.json')
const HOLO_STATE_PATH = join(HOLO_RUNTIME_ROOT, 'state.json')

// Observation is opt-out only within the Holo work families. Unknown/global
// reads are not evidence of work. New task/review/conversation operations use
// the same policy and writer without another heartbeat implementation.
export function isWorkflowActivity(operation) {
  return /^(?:task-(?!targets$)|review(?:$|-)|conversation-|audit-start$|incident-resolve$|say$)/.test(operation)
}

export function isSuccessfulActivityResult(result) {
  const value = result?.structuredContent ?? result
  return result?.isError !== true && value?.ok !== false
    && value?.timedOut !== true && value?.timed_out !== true
    && (value?.exitCode == null || value.exitCode === 0)
}

// No current-Dive inference: a foreground tab is not the caller's identity.
// Task/Review results may carry a Core-verified owner; other callers carry the
// exact workflow ID with their ordinary work request. No extra liveness call.
export async function withWorkflowActivity({ workflowId, enabled = true, request = callHolo }, action) {
  if (!enabled) return action()
  const before = await readWorkflowLease().catch(() => null)
  if (!before || before.state !== 'active' || (workflowId && workflowId !== before.workflow_id)) {
    return action()
  }
  const result = await action()
  const owner = result?.workflow_owner
  const owned = workflowId === before.workflow_id || (
    owner?.workflow_id === before.workflow_id
    && owner.dive_session_id === before.dive_session_id
    && isSameConversationUrl(owner.conversation_url, before.conversation_url)
  )
  if (!owned || (owner?.workflow_id && owner.workflow_id !== before.workflow_id)
    || !isSuccessfulActivityResult(result)) return result
  try {
    await request({ type: 'holo_workflow_request', payload: {
      action: 'activity', workflow_id: before.workflow_id, dive_session_id: before.dive_session_id
    }, timeoutMs: 10_000 })
  } catch (error) {
    // The work succeeded. Never turn a receipt failure into an apparent task
    // failure that invites duplicate execution. Preserve the result and expose
    // the actual local persistence failure without another recovery mechanism.
    return { ...result, workflow_activity_warning: String(error?.message ?? error) }
  }
  return result
}

async function readJsonFile(path, { allowMissing = false } = {}) {
  try {
    const parsed = JSON.parse(await readFile(path, 'utf8'))
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error(`Invalid JSON object: ${path}`)
    }
    return parsed
  } catch (error) {
    if (allowMissing && error?.code === 'ENOENT') return null
    throw error
  }
}

function isConversationUrl(value) {
  if (typeof value !== 'string') return false
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && url.hostname === 'chatgpt.com' && /(?:^|\/)c\/[^/]+/.test(url.pathname)
  } catch {
    return false
  }
}

function isSameConversationUrl(left, right) {
  if (!isConversationUrl(left) || !isConversationUrl(right)) return false
  const id = (value) => new URL(value).pathname.match(/(?:^|\/)c\/([^/]+)/)[1].replace(/^WEB:/, '')
  return id(left) === id(right)
}

export function looksLikeDiveSessionId(value) {
  return typeof value === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.trim())
}

export async function resolveDiveContext(requestedDiveSessionId = null) {
  const state = await readJsonFile(HOLO_STATE_PATH)
  const currentDiveSessionId = typeof state.current_dive_session_id === 'string'
    ? state.current_dive_session_id.trim()
    : ''
  const diveSessionId = typeof requestedDiveSessionId === 'string' && requestedDiveSessionId.trim()
    ? requestedDiveSessionId.trim()
    : currentDiveSessionId
  if (!diveSessionId) {
    throw new Error('No Dive Session ID is available for a Holo workflow lease')
  }

  const knownDiveUrls = state.known_dive_urls && typeof state.known_dive_urls === 'object'
    ? state.known_dive_urls
    : {}
  const knownUrl = knownDiveUrls[diveSessionId]
  const currentUrl = diveSessionId === currentDiveSessionId ? state.current_dive_url : null
  // The visible Current Dive URL is the freshest browser-canonical identity.
  // ChatGPT can normalize a temporary /c/WEB:* route to its canonical /c/*
  // route after navigation; during that transition known_dive_urls may still
  // contain the older alias. Prefer current_dive_url only for the exact current
  // Dive, while historical Dive lookups remain pinned to known_dive_urls.
  const conversationUrl = isConversationUrl(currentUrl)
    ? currentUrl
    : isConversationUrl(knownUrl) ? knownUrl : null
  if (!conversationUrl) {
    throw new Error(`No Conversation URL is registered for Dive Session ID ${diveSessionId}`)
  }
  return { diveSessionId, conversationUrl }
}

function validateWorkflowLease(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  if (raw.version !== 1) return null
  if (typeof raw.workflow_id !== 'string' || !raw.workflow_id.trim()) return null
  if (typeof raw.dive_session_id !== 'string' || !raw.dive_session_id.trim()) return null
  if (raw.conversation_url !== undefined && !isConversationUrl(raw.conversation_url)) return null
  if (!['active', 'completed'].includes(raw.state)) return null
  if (typeof raw.started_at !== 'string' || typeof raw.updated_at !== 'string') return null
  return raw
}

export async function readWorkflowLease() {
  const raw = await readJsonFile(HOLO_WORKFLOW_STATE_PATH, { allowMissing: true })
  if (raw === null) return null
  const lease = validateWorkflowLease(raw)
  if (lease === null) throw new Error('Invalid Nirai Holo workflow lease')
  return lease
}

function parseWorkflowMutationOwner(existing, args) {
  const first = typeof args[0] === 'string' && args[0].trim() ? args[0].trim() : null
  let explicitDiveSessionId = null
  let requestedWorkflowId = null
  if (first !== null) {
    if (first === existing.workflow_id || !looksLikeDiveSessionId(first)) {
      requestedWorkflowId = first
    } else {
      explicitDiveSessionId = first
      requestedWorkflowId = typeof args[1] === 'string' && args[1].trim() ? args[1].trim() : null
    }
  }
  return { explicitDiveSessionId, requestedWorkflowId }
}

// Capture the lease before Task launch; a replacement created during the request
// must never inherit work from the old caller.
export async function workflowForOwner(diveId, url) {
  const workflow = await readWorkflowLease()
  return workflow?.state === 'active' && workflow.dive_session_id === diveId && isSameConversationUrl(workflow.conversation_url, url)
    ? workflow.workflow_id : undefined
}

export async function runWorkflowCommand(argv, request = callHolo) {
  const [command, ...args] = argv
  if (!['workflow-start', 'workflow-heartbeat', 'workflow-complete', 'workflow-cancel', 'workflow-status', 'workflow-resolve'].includes(command)) {
    return { handled: false, result: null }
  }
  const action = command.slice('workflow-'.length)
  const payload = { action }
  if (action === 'start') {
    const explicitDiveSessionId = looksLikeDiveSessionId(args[0]) ? args[0].trim() : null
    const context = await resolveDiveContext(explicitDiveSessionId)
    payload.label = (explicitDiveSessionId ? args.slice(1) : args).join(' ').trim()
    if (!payload.label) throw new Error('workflow-start requires a short non-empty label')
    payload.dive_session_id = context.diveSessionId
    payload.conversation_url = context.conversationUrl
  } else if (action === 'status') {
    if (args[0]?.trim()) payload.dive_session_id = args[0].trim()
  } else if (action === 'resolve') {
    const [workflowId, taskId, resolution, replacement, ...note] = args
    Object.assign(payload, { workflow_id: workflowId, task_id: taskId, resolution,
      replacement_task_id: replacement === '-' ? null : replacement, note: note.join(' ') })
  } else {
    const existing = await readWorkflowLease()
    if (!existing) throw new Error(`No Holo workflow is available for ${command}`)
    const { explicitDiveSessionId, requestedWorkflowId } = parseWorkflowMutationOwner(existing, args)
    if ((action === 'complete' || action === 'cancel') && !requestedWorkflowId) {
      throw new Error(`${command} requires the exact workflow_id`)
    }
    if (!explicitDiveSessionId && !requestedWorkflowId) {
      const context = await resolveDiveContext()
      if (context.diveSessionId !== existing.dive_session_id
        || !isSameConversationUrl(context.conversationUrl, existing.conversation_url)) {
        throw new Error('Workflow belongs to a different Dive or Conversation; supply its workflow_id explicitly')
      }
    }
    payload.workflow_id = requestedWorkflowId ?? existing.workflow_id
    if (explicitDiveSessionId) payload.dive_session_id = explicitDiveSessionId
  }
  const result = await request({ type: 'holo_workflow_request', payload, timeoutMs: 60_000 })
  return { handled: true, result }
}
