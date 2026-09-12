import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'

const NIRAI_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const HOLO_RUNTIME_ROOT = join(NIRAI_ROOT, 'runtime', 'holo')
const HOLO_WORKFLOW_STATE_PATH = join(HOLO_RUNTIME_ROOT, 'workflow.json')
const HOLO_STATE_PATH = join(HOLO_RUNTIME_ROOT, 'state.json')

function bridgeFilePath() {
  if (process.env.NIRAI_HOLO_LOCAL_BRIDGE_FILE) return process.env.NIRAI_HOLO_LOCAL_BRIDGE_FILE
  const localAppData = process.env.LOCALAPPDATA
  if (!localAppData) throw new Error('Nirai Holo local bridge is unavailable: LOCALAPPDATA is not set')
  return join(localAppData, 'Nirai', 'holo-local-bridge.json')
}

function validateDescriptor(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('Invalid Nirai Holo local bridge descriptor')
  if (raw.version !== 1) throw new Error('Unsupported Nirai Holo local bridge descriptor version')
  if (typeof raw.url !== 'string') throw new Error('Nirai Holo local bridge URL is missing')
  if (typeof raw.secret !== 'string' || raw.secret.length < 32) {
    throw new Error('Nirai Holo local bridge credential is invalid')
  }
  const url = new URL(raw.url)
  if (url.protocol !== 'ws:' || !['127.0.0.1', 'localhost'].includes(url.hostname)) {
    throw new Error('Nirai Holo local bridge must point to localhost WebSocket')
  }
  return { url, secret: raw.secret }
}

async function readDescriptor() {
  try {
    return validateDescriptor(JSON.parse(await readFile(bridgeFilePath(), 'utf8')))
  } catch (error) {
    if (error?.code === 'ENOENT') {
      throw new Error('Nirai Holo local bridge is not ready. Restart Nirai and try again.')
    }
    throw error
  }
}

function message(type, payload, id) {
  return JSON.stringify({
    type,
    ts: new Date().toISOString(),
    ...(id ? { id } : {}),
    payload
  })
}

function parse(raw) {
  try {
    const parsed = JSON.parse(String(raw))
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

function parseInteger(value, name, { min, max }) {
  const number = Number(value)
  if (!Number.isInteger(number) || number < min || number > max) {
    throw new Error(`${name} must be an integer between ${min} and ${max}`)
  }
  return number
}

function parseNumber(value, name, { min, max }) {
  const number = Number(value)
  if (!Number.isFinite(number) || number < min || number > max) {
    throw new Error(`${name} must be a number between ${min} and ${max}`)
  }
  return number
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

function looksLikeDiveSessionId(value) {
  return typeof value === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.trim())
}

async function resolveDiveContext(requestedDiveSessionId = null) {
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

async function readWorkflowLease() {
  const raw = await readJsonFile(HOLO_WORKFLOW_STATE_PATH, { allowMissing: true })
  if (raw === null) return null
  const lease = validateWorkflowLease(raw)
  if (lease === null) throw new Error('Invalid Nirai Holo workflow lease')
  return lease
}

async function runWorkflowCommand(argv) {
  const [command, ...args] = argv
  if (!['workflow-start', 'workflow-heartbeat', 'workflow-complete', 'workflow-status'].includes(command)) {
    return { handled: false, result: null }
  }

  const existing = await readWorkflowLease()

  if (command === 'workflow-status') {
    const requestedDiveSessionId = typeof args[0] === 'string' && args[0].trim() ? args[0].trim() : null
    const workflow = requestedDiveSessionId && existing?.dive_session_id !== requestedDiveSessionId
      ? null
      : existing
    return { handled: true, result: { operation: 'workflow_status', ok: true, workflow } }
  }

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
      if (existing.conversation_url !== context.conversationUrl) {
        throw new Error('Active Holo workflow lease is bound to a different Conversation')
      }
      const refreshed = {
        ...existing,
        label,
        updated_at: now
      }
      await writeJsonAtomically(HOLO_WORKFLOW_STATE_PATH, refreshed)
      return { handled: true, result: { operation: 'workflow_start', ok: true, resumed: true, workflow: refreshed } }
    }
    const workflow = {
      version: 1,
      workflow_id: randomUUID(),
      dive_session_id: context.diveSessionId,
      conversation_url: context.conversationUrl,
      label,
      state: 'active',
      started_at: now,
      updated_at: now
    }
    await writeJsonAtomically(HOLO_WORKFLOW_STATE_PATH, workflow)
    return { handled: true, result: { operation: 'workflow_start', ok: true, resumed: false, workflow } }
  }

  if (!existing || existing.state !== 'active') {
    throw new Error(`No active Holo workflow lease is available for ${command}`)
  }
  const explicitDiveSessionId = looksLikeDiveSessionId(args[0]) ? args[0].trim() : null
  if (explicitDiveSessionId !== null && existing.dive_session_id !== explicitDiveSessionId) {
    throw new Error('Active Holo workflow lease belongs to a different Dive')
  }
  const requestedWorkflowIdIndex = explicitDiveSessionId ? 1 : 0
  const requestedWorkflowId = typeof args[requestedWorkflowIdIndex] === 'string' && args[requestedWorkflowIdIndex].trim()
    ? args[requestedWorkflowIdIndex].trim()
    : null
  if (requestedWorkflowId !== null && requestedWorkflowId !== existing.workflow_id) {
    throw new Error('workflow_id does not match the active Holo workflow lease')
  }

  if (command === 'workflow-heartbeat') {
    const workflow = { ...existing, updated_at: now }
    await writeJsonAtomically(HOLO_WORKFLOW_STATE_PATH, workflow)
    return { handled: true, result: { operation: 'workflow_heartbeat', ok: true, workflow } }
  }

  const workflow = { ...existing, state: 'completed', updated_at: now, completed_at: now }
  await writeJsonAtomically(HOLO_WORKFLOW_STATE_PATH, workflow)
  return { handled: true, result: { operation: 'workflow_complete', ok: true, workflow } }
}

async function commandRequest(argv) {
  const [command, ...args] = argv
  if (command === 'attach') {
    return { type: 'holo_attach_request', payload: {}, timeoutMs: 5000 }
  }
  if (command === 'snapshot') {
    return { type: 'holo_snapshot_request', payload: {}, timeoutMs: 5000 }
  }
  if (command === 'skills') {
    return { type: 'holo_skills_request', payload: {}, timeoutMs: 5000 }
  }
  if (command === 'incidents') {
    const limit = args[0] === undefined ? 20 : parseInteger(args[0], 'limit', { min: 1, max: 50 })
    return { type: 'holo_incidents_request', payload: { limit }, timeoutMs: 5000 }
  }
  if (command === 'incident-resolve') {
    const [incidentId, note] = args
    if (typeof incidentId !== 'string' || !incidentId.trim()) {
      throw new Error('incident-resolve requires an incident_id')
    }
    return {
      type: 'holo_incident_resolve_request',
      payload: {
        incident_id: incidentId.trim(),
        ...(typeof note === 'string' && note.trim() ? { note: note.trim() } : {})
      },
      timeoutMs: 5000
    }
  }
  if (command === 'say') {
    const [text, to] = args
    if (typeof text !== 'string' || !text.trim()) throw new Error('say requires a non-empty text argument')
    return {
      type: 'holo_world_say_request',
      payload: { text, ...(typeof to === 'string' && to.trim() ? { to } : {}) },
      timeoutMs: 5000
    }
  }
  if (command === 'wait') {
    const afterEventId = parseInteger(args[0], 'after_event_id', { min: 0, max: Number.MAX_SAFE_INTEGER })
    const timeoutSec = parseNumber(args[1], 'timeout_sec', { min: 0, max: 15 })
    const limit = args[2] === undefined ? 50 : parseInteger(args[2], 'limit', { min: 1, max: 50 })
    const eventEpoch = typeof args[3] === 'string' && args[3].trim() ? args[3].trim() : undefined
    return {
      type: 'holo_wait_events_request',
      payload: {
        after_event_id: afterEventId,
        timeout_sec: timeoutSec,
        limit,
        ...(eventEpoch ? { event_epoch: eventEpoch } : {})
      },
      timeoutMs: Math.max(3000, Math.ceil(timeoutSec * 1000) + 3000)
    }
  }
  if (command === 'task-targets') {
    return {
      type: 'holo_task_targets_request',
      payload: {},
      timeoutMs: 5000
    }
  }
  if (command === 'task-start') {
    const explicitDiveSessionId = looksLikeDiveSessionId(args[0]) ? args[0].trim() : null
    const taskArgs = explicitDiveSessionId ? args.slice(1) : args
    const [target, resident, text] = taskArgs
    if (typeof text !== 'string' || !text.trim()) {
      throw new Error('task-start requires [dive_session_id], target, resident, and non-empty text')
    }
    const owner = await resolveDiveContext(explicitDiveSessionId)
    return {
      type: 'holo_task_start_request',
      payload: {
        text,
        dive_session_id: owner.diveSessionId,
        conversation_url: owner.conversationUrl,
        ...(typeof target === 'string' && target.trim() && target !== '-' ? { target: target.trim() } : {}),
        ...(typeof resident === 'string' && resident.trim() && resident !== '-' ? { resident: resident.trim() } : {})
      },
      timeoutMs: 10000
    }
  }
  if (command === 'audit-start') {
    const explicitDiveSessionId = looksLikeDiveSessionId(args[0]) ? args[0].trim() : null
    const auditArgs = explicitDiveSessionId ? args.slice(1) : args
    const [target, text] = auditArgs
    if (typeof target !== 'string' || !target.trim() || target === '-') {
      throw new Error('audit-start requires [dive_session_id], a named target, and non-empty text')
    }
    if (typeof text !== 'string' || !text.trim()) {
      throw new Error('audit-start requires [dive_session_id], a named target, and non-empty text')
    }
    const owner = await resolveDiveContext(explicitDiveSessionId)
    return {
      type: 'holo_integrated_audit_start_request',
      payload: {
        target: target.trim(),
        text,
        dive_session_id: owner.diveSessionId,
        conversation_url: owner.conversationUrl
      },
      // Audit selection awaits fresh usage plus provider cleanup before it
      // can return a Task ID. Leave room beyond the bounded usage deadline.
      timeoutMs: 60000
    }
  }
  if (command === 'task-snapshot') {
    const [taskId] = args
    if (typeof taskId !== 'string' || !taskId.trim()) {
      throw new Error('task-snapshot requires a task_id')
    }
    return {
      type: 'holo_task_snapshot_request',
      payload: { task_id: taskId.trim() },
      timeoutMs: 5000
    }
  }
  if (command === 'task-wait') {
    const [taskId, timeoutRaw] = args
    if (typeof taskId !== 'string' || !taskId.trim()) {
      throw new Error('task-wait requires a task_id')
    }
    const timeoutSec = parseNumber(timeoutRaw, 'timeout_sec', { min: 0, max: 15 })
    return {
      type: 'holo_task_wait_request',
      payload: { task_id: taskId.trim(), timeout_sec: timeoutSec },
      timeoutMs: Math.max(3000, Math.ceil(timeoutSec * 1000) + 3000)
    }
  }
  if (command === 'task-cancel') {
    const [agentSessionId] = args
    if (typeof agentSessionId !== 'string' || !agentSessionId.trim()) {
      throw new Error('task-cancel requires an agent_session_id')
    }
    return {
      type: 'holo_task_cancel_request',
      payload: { agent_session_id: agentSessionId.trim() },
      timeoutMs: 5000
    }
  }
  if (command === 'task-recover') {
    const [agentSessionId, action] = args
    if (typeof agentSessionId !== 'string' || !agentSessionId.trim()) {
      throw new Error('task-recover requires an agent_session_id')
    }
    if (!['resume', 'rerun', 'abandon'].includes(action)) {
      throw new Error('task-recover action must be resume, rerun, or abandon')
    }
    return {
      type: 'holo_task_recover_request',
      payload: { agent_session_id: agentSessionId.trim(), action },
      timeoutMs: 10000
    }
  }
  if (command === 'task-respond') {
    const [agentSessionId, requestId, kind, responseJson] = args
    if (typeof agentSessionId !== 'string' || !agentSessionId.trim()) {
      throw new Error('task-respond requires an agent_session_id')
    }
    if (typeof requestId !== 'string' || !requestId.trim()) {
      throw new Error('task-respond requires a request_id')
    }
    if (kind !== 'question') {
      throw new Error('task-respond supports non-privileged question answers only; Approval/Plan decisions must use the Nirai Master UI')
    }
    let response
    try {
      response = JSON.parse(responseJson)
    } catch {
      throw new Error('task-respond response must be valid JSON')
    }
    if (!response || typeof response !== 'object' || Array.isArray(response)) {
      throw new Error('task-respond response must be a JSON object')
    }
    return {
      type: 'holo_task_respond_request',
      payload: {
        agent_session_id: agentSessionId.trim(),
        request_id: requestId.trim(),
        kind,
        response
      },
      timeoutMs: 10000
    }
  }
  if (command === 'conversation-start') {
    const [participantKind, participant, mode, target, model, reasoningEffort] = args
    if (!['resident', 'provider'].includes(participantKind)) {
      throw new Error('conversation-start participant_kind must be resident or provider')
    }
    if (typeof participant !== 'string' || !participant.trim()) {
      throw new Error('conversation-start requires a participant')
    }
    if (!['talk', 'brainstorm', 'consult', 'review'].includes(mode)) {
      throw new Error('conversation-start mode must be talk, brainstorm, consult, or review')
    }
    return {
      type: 'holo_conversation_start_request',
      payload: {
        participant_kind: participantKind,
        participant,
        mode,
        ...(typeof target === 'string' && target.trim() ? { target } : {}),
        ...(typeof model === 'string' && model.trim() ? { model } : {}),
        ...(typeof reasoningEffort === 'string' && reasoningEffort.trim() ? { reasoning_effort: reasoningEffort } : {})
      },
      timeoutMs: 5000
    }
  }
  if (command === 'conversation-send') {
    const [conversationId, text] = args
    if (typeof conversationId !== 'string' || !conversationId.trim()) {
      throw new Error('conversation-send requires a conversation_id')
    }
    if (typeof text !== 'string' || !text.trim()) {
      throw new Error('conversation-send requires non-empty text')
    }
    return {
      type: 'holo_conversation_send_request',
      payload: { conversation_id: conversationId, text },
      timeoutMs: 10000
    }
  }
  if (command === 'conversation-wait') {
    const [conversationId, timeoutRaw] = args
    if (typeof conversationId !== 'string' || !conversationId.trim()) {
      throw new Error('conversation-wait requires a conversation_id')
    }
    const timeoutSec = parseNumber(timeoutRaw, 'timeout_sec', { min: 0, max: 15 })
    return {
      type: 'holo_conversation_wait_request',
      payload: { conversation_id: conversationId, timeout_sec: timeoutSec },
      timeoutMs: Math.max(3000, Math.ceil(timeoutSec * 1000) + 3000)
    }
  }
  if (command === 'conversation-cancel') {
    const [conversationId] = args
    if (typeof conversationId !== 'string' || !conversationId.trim()) {
      throw new Error('conversation-cancel requires a conversation_id')
    }
    return {
      type: 'holo_conversation_cancel_request',
      payload: { conversation_id: conversationId },
      timeoutMs: 5000
    }
  }
  if (command === 'conversation-close') {
    const [conversationId] = args
    if (typeof conversationId !== 'string' || !conversationId.trim()) {
      throw new Error('conversation-close requires a conversation_id')
    }
    return {
      type: 'holo_conversation_close_request',
      payload: { conversation_id: conversationId },
      timeoutMs: 5000
    }
  }
  if (command === 'review') {
    const [target, prompt, model, reasoningEffort] = args
    if (typeof target !== 'string' || !target.trim()) {
      throw new Error('review requires a non-empty target folder name')
    }
    if (typeof prompt !== 'string' || !prompt.trim()) {
      throw new Error('review requires a non-empty prompt')
    }
    const owner = await resolveDiveContext()
    return {
      type: 'holo_cursor_review_start_request',
      payload: {
        target,
        prompt,
        dive_session_id: owner.diveSessionId,
        conversation_url: owner.conversationUrl,
        ...(typeof model === 'string' && model.trim() ? { model } : {}),
        ...(typeof reasoningEffort === 'string' && reasoningEffort.trim() ? { reasoning_effort: reasoningEffort } : {})
      },
      timeoutMs: 10000
    }
  }
  if (command === 'review-wait') {
    const [agentSessionId, timeoutRaw] = args
    if (typeof agentSessionId !== 'string' || !agentSessionId.trim()) {
      throw new Error('review-wait requires an agent_session_id')
    }
    const timeoutSec = parseNumber(timeoutRaw, 'timeout_sec', { min: 0, max: 15 })
    return {
      type: 'holo_cursor_review_wait_request',
      payload: { agent_session_id: agentSessionId, timeout_sec: timeoutSec },
      timeoutMs: Math.max(3000, Math.ceil(timeoutSec * 1000) + 3000)
    }
  }
  if (command === 'review-cancel') {
    const [agentSessionId] = args
    if (typeof agentSessionId !== 'string' || !agentSessionId.trim()) {
      throw new Error('review-cancel requires an agent_session_id')
    }
    return {
      type: 'holo_cursor_review_cancel_request',
      payload: { agent_session_id: agentSessionId },
      timeoutMs: 5000
    }
  }
  if (command === 'review-recover') {
    const [agentSessionId, action] = args
    if (typeof agentSessionId !== 'string' || !agentSessionId.trim()) {
      throw new Error('review-recover requires an agent_session_id')
    }
    if (!['resume', 'rerun', 'abandon'].includes(action)) {
      throw new Error('review-recover action must be resume, rerun, or abandon')
    }
    return {
      type: 'holo_cursor_review_recover_request',
      payload: { agent_session_id: agentSessionId, action },
      timeoutMs: 10000
    }
  }
  throw new Error('Usage: holo-local-client.mjs <attach|snapshot|skills|say|wait|workflow-start|workflow-heartbeat|workflow-complete|workflow-status|task-targets|task-start|audit-start|task-snapshot|task-wait|task-cancel|task-recover|task-respond|conversation-start|conversation-send|conversation-wait|conversation-cancel|conversation-close|review|review-wait|review-cancel|review-recover> [...args] (workflow-start: [dive_session_id] short_label; workflow-status: [dive_session_id]; workflow-heartbeat/workflow-complete: [dive_session_id] [workflow_id]; task-start: [dive_session_id] target|- resident|- text; audit-start: [dive_session_id] target text; task-respond: agent_session_id request_id question response_json; wait: after_event_id timeout_sec [limit] [event_epoch])')
}

async function callCore(descriptor, request) {
  if (typeof globalThis.WebSocket !== 'function') {
    throw new Error('Node.js WebSocket API is unavailable')
  }
  const socket = new globalThis.WebSocket(descriptor.url)
  const helloId = randomUUID()
  const requestId = randomUUID()

  return new Promise((resolve, reject) => {
    let settled = false
    let authenticated = false
    const timer = setTimeout(() => finish(new Error('Nirai Holo local request timed out')), request.timeoutMs)

    function finish(error, value) {
      if (settled) return
      settled = true
      clearTimeout(timer)
      try { socket.close() } catch { /* already closed */ }
      if (error) reject(error)
      else resolve(value)
    }

    socket.addEventListener('open', () => {
      socket.send(message('hello', { role: 'holo_local', secret: descriptor.secret }, helloId))
    })

    socket.addEventListener('message', (event) => {
      const response = parse(event.data)
      if (!response) return
      if (!authenticated && response.type === 'holo_local_hello_ack' && response.id === helloId) {
        authenticated = true
        socket.send(message(request.type, request.payload, requestId))
        return
      }
      if (response.type !== 'holo_local_result' || response.id !== requestId) return
      if (response.payload?.ok !== true) {
        finish(new Error(response.payload?.error || 'Nirai rejected Holo local request'))
        return
      }
      finish(null, response.payload)
    })

    socket.addEventListener('error', () => finish(new Error('Nirai Core WebSocket error')))
    socket.addEventListener('close', (event) => {
      if (!settled) finish(new Error(`Nirai Core connection closed (${event.code})`))
    })
  })
}

let descriptor
try {
  const argv = process.argv.slice(2)
  const workflowCommand = await runWorkflowCommand(argv)
  if (workflowCommand.handled) {
    console.log(JSON.stringify({ ok: true, result: workflowCommand.result }))
  } else {
    descriptor = await readDescriptor()
    const request = await commandRequest(argv)
    const result = await callCore(descriptor, request)
    console.log(JSON.stringify({ ok: true, result }))
  }
} catch (error) {
  const rawMessage = error instanceof Error ? error.message : String(error)
  const safeMessage = descriptor?.secret
    ? rawMessage.split(descriptor.secret).join('[redacted]')
    : rawMessage
  console.error(JSON.stringify({ ok: false, error: safeMessage }))
  process.exitCode = 1
}
