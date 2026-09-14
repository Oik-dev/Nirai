import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { withDirectoryLock } from './file-lock.mjs'
import { assertWorldBuildReadyForWorkflowCompletion, computeWorldBuildFingerprint } from './world-build-state.mjs'

export const NIRAI_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const HOLO_RUNTIME_ROOT = join(NIRAI_ROOT, 'runtime', 'holo')
const HOLO_WORKFLOW_STATE_PATH = join(HOLO_RUNTIME_ROOT, 'workflow.json')
const HOLO_WORKFLOW_LOCK_PATH = join(HOLO_RUNTIME_ROOT, 'workflow.lock')
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
    && value?.timedOut !== true && (value?.exitCode == null || value.exitCode === 0)
}

// No current-Dive inference: a foreground tab is not the caller's identity.
// Task/Review results may carry a Core-verified owner; other callers carry the
// exact workflow ID with their ordinary work request. No extra liveness call.
export async function withWorkflowActivity({ workflowId, enabled = true }, action) {
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
    await withDirectoryLock(HOLO_WORKFLOW_LOCK_PATH, async () => {
      const current = await readWorkflowLease()
      if (current?.state !== 'active' || current.workflow_id !== before.workflow_id
        || current.dive_session_id !== before.dive_session_id
        || !isSameConversationUrl(current.conversation_url, before.conversation_url)) return
      await writeJsonAtomically(HOLO_WORKFLOW_STATE_PATH, {
        ...current,
        // Monotonic revision, including parallel operations in one millisecond.
        updated_at: new Date(Math.max(Date.now(), Date.parse(current.updated_at) + 1)).toISOString()
      })
    })
  } catch (error) {
    // The work succeeded. Never turn a receipt failure into an apparent task
    // failure that invites duplicate execution. Preserve the result and expose
    // the actual local persistence failure without another recovery mechanism.
    return { ...result, workflow_activity_warning: String(error?.message ?? error) }
  }
  return result
}

async function delay(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms))
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

async function writeJsonAtomically(path, payload) {
  await mkdir(dirname(path), { recursive: true })
  const temporary = `${path}.${randomUUID()}.tmp`
  try {
    await writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
    for (let attempt = 0; ; attempt += 1) {
      try {
        await rename(temporary, path)
        return
      } catch (error) {
        const retryable = ['EPERM', 'EACCES', 'EBUSY'].includes(error?.code)
        if (!retryable || attempt >= 7) throw error
        await delay((attempt + 1) * 10)
      }
    }
  } finally {
    await unlink(temporary).catch(() => undefined)
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
  const fallbackCurrentUrl = diveSessionId === currentDiveSessionId ? state.current_dive_url : null
  const conversationUrl = isConversationUrl(knownUrl)
    ? knownUrl
    : isConversationUrl(fallbackCurrentUrl) ? fallbackCurrentUrl : null
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

async function assertWorkflowRevisionUnchanged(expected) {
  const current = await readWorkflowLease()
  if (!current || current.state !== 'active'
    || current.workflow_id !== expected.workflow_id
    || current.dive_session_id !== expected.dive_session_id
    || current.updated_at !== expected.updated_at) {
    throw new Error('Active Holo workflow lease changed while the operation was in progress')
  }
  return current
}

export async function runWorkflowCommand(argv) {
  const [command, ...args] = argv
  if (!['workflow-start', 'workflow-heartbeat', 'workflow-complete', 'workflow-cancel', 'workflow-status'].includes(command)) {
    return { handled: false, result: null }
  }

  if (command === 'workflow-status') {
    const existing = await readWorkflowLease()
    const requestedDiveSessionId = typeof args[0] === 'string' && args[0].trim() ? args[0].trim() : null
    const workflow = requestedDiveSessionId && existing?.dive_session_id !== requestedDiveSessionId
      ? null
      : existing
    return { handled: true, result: { operation: 'workflow_status', ok: true, workflow } }
  }

  return {
    handled: true,
    result: await withDirectoryLock(HOLO_WORKFLOW_LOCK_PATH, async () => {
      const existing = await readWorkflowLease()
      const now = new Date().toISOString()

      if (command === 'workflow-start') {
        const explicitDiveSessionId = looksLikeDiveSessionId(args[0]) ? args[0].trim() : null
        const context = await resolveDiveContext(explicitDiveSessionId)
        const labelParts = explicitDiveSessionId ? args.slice(1) : args
        const label = labelParts.join(' ').trim()
        if (!label) throw new Error('workflow-start requires a short non-empty label')
        if (existing?.state === 'active' && existing.dive_session_id !== context.diveSessionId) {
          throw new Error('Another active Holo workflow lease belongs to a different Dive')
        }
        if (existing?.state === 'active') {
          if (!isSameConversationUrl(existing.conversation_url, context.conversationUrl)) {
            throw new Error('Active Holo workflow lease is bound to a different Conversation')
          }
          const refreshed = { ...existing, label, updated_at: now }
          await writeJsonAtomically(HOLO_WORKFLOW_STATE_PATH, refreshed)
          return { operation: 'workflow_start', ok: true, resumed: true, workflow: refreshed }
        }
        const workflow = {
          version: 1,
          workflow_id: randomUUID(),
          dive_session_id: context.diveSessionId,
          conversation_url: context.conversationUrl,
          label,
          state: 'active',
          started_at: now,
          updated_at: now,
          world_build_fingerprint_at_start: await computeWorldBuildFingerprint(NIRAI_ROOT)
        }
        await writeJsonAtomically(HOLO_WORKFLOW_STATE_PATH, workflow)
        return { operation: 'workflow_start', ok: true, resumed: false, workflow }
      }

      if (!existing || existing.state !== 'active') {
        throw new Error(`No active Holo workflow lease is available for ${command}`)
      }
      const { explicitDiveSessionId, requestedWorkflowId } = parseWorkflowMutationOwner(existing, args)
      if (explicitDiveSessionId !== null && existing.dive_session_id !== explicitDiveSessionId) {
        throw new Error('Active Holo workflow lease belongs to a different Dive')
      }
      if (requestedWorkflowId !== null && requestedWorkflowId !== existing.workflow_id) {
        throw new Error('workflow_id does not match the active Holo workflow lease')
      }
      if ((command === 'workflow-complete' || command === 'workflow-cancel') && requestedWorkflowId === null) {
        throw new Error(`${command} requires the exact workflow_id`)
      }

      if (explicitDiveSessionId === null && requestedWorkflowId === null) {
        const context = await resolveDiveContext()
        if (context.diveSessionId !== existing.dive_session_id
          || !isSameConversationUrl(context.conversationUrl, existing.conversation_url)) {
          throw new Error('Active Holo workflow lease belongs to a different Dive or Conversation; supply its workflow_id explicitly')
        }
      }

      if (command === 'workflow-heartbeat') {
        const workflow = { ...existing, updated_at: now }
        await writeJsonAtomically(HOLO_WORKFLOW_STATE_PATH, workflow)
        return { operation: 'workflow_heartbeat', ok: true, workflow }
      }

      if (command === 'workflow-complete') {
        await assertWorldBuildReadyForWorkflowCompletion(existing.world_build_fingerprint_at_start, NIRAI_ROOT)
        await assertWorkflowRevisionUnchanged(existing)
      }

      const workflow = {
        ...existing,
        state: 'completed',
        updated_at: now,
        completed_at: now,
        ...(command === 'workflow-cancel' ? { completion_reason: 'cancelled_by_master' } : {})
      }
      await writeJsonAtomically(HOLO_WORKFLOW_STATE_PATH, workflow)
      return {
        operation: command === 'workflow-cancel' ? 'workflow_cancel' : 'workflow_complete',
        ok: true,
        workflow
      }
    })
  }
}

