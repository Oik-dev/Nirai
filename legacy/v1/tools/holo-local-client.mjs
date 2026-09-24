import { callHolo } from './holo-transport.mjs'
import { isWorkflowActivity, looksLikeDiveSessionId, resolveDiveContext, runWorkflowCommand, withWorkflowActivity, workflowForOwner } from './holo-workflow.mjs'

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
  if (command === 'conversation-stop') {
    const [conversationUrl, stoppedAt] = args
    if (typeof conversationUrl !== 'string' || !Number.isFinite(Date.parse(stoppedAt))) {
      throw new Error('conversation-stop requires an owner Conversation URL and stop time')
    }
    return {
      type: 'holo_conversation_stop_request',
      payload: { conversation_url: conversationUrl, stopped_at: stoppedAt },
      timeoutMs: 25000
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


try {
  const argv = process.argv.slice(2)
  let workflowId
  let observeOnly = false
  while (argv[0]?.startsWith('--')) {
    const option = argv.shift()
    if (option === '--workflow-id' && argv[0]?.trim()) workflowId = argv.shift()
    else if (option === '--observe-only') observeOnly = true
    else throw new Error(`Invalid Holo Local Client option: ${option}`)
  }
  const workflowCommand = await runWorkflowCommand(argv)
  if (workflowCommand.handled) {
    console.log(JSON.stringify({ ok: true, result: workflowCommand.result }))
  } else {
    const request = await commandRequest(argv)
    if (['holo_task_start_request', 'holo_integrated_audit_start_request', 'holo_cursor_review_start_request'].includes(request.type)) {
      request.payload.workflow_id = workflowId ?? await workflowForOwner(request.payload.dive_session_id, request.payload.conversation_url)
    }
    const result = await withWorkflowActivity({
      workflowId, enabled: !observeOnly && isWorkflowActivity(argv[0])
    }, () => callHolo(request))
    console.log(JSON.stringify({ ok: true, result }))
  }
} catch (error) {
  const rawMessage = error instanceof Error ? error.message : String(error)
  console.error(JSON.stringify({ ok: false, error: rawMessage, ...(error.result ? { result: error.result } : {}) }))
  process.exitCode = 1
}
