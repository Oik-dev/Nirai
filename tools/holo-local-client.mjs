import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'

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

function commandRequest(argv) {
  const [command, ...args] = argv
  if (command === 'attach') {
    return { type: 'holo_attach_request', payload: {}, timeoutMs: 5000 }
  }
  if (command === 'snapshot') {
    return { type: 'holo_snapshot_request', payload: {}, timeoutMs: 5000 }
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
    return {
      type: 'holo_wait_events_request',
      payload: { after_event_id: afterEventId, timeout_sec: timeoutSec, limit },
      timeoutMs: Math.max(3000, Math.ceil(timeoutSec * 1000) + 3000)
    }
  }
  throw new Error('Usage: holo-local-client.mjs <attach|snapshot|say|wait> [...args]')
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
  descriptor = await readDescriptor()
  const request = commandRequest(process.argv.slice(2))
  const result = await callCore(descriptor, request)
  console.log(JSON.stringify({ ok: true, result }))
} catch (error) {
  const rawMessage = error instanceof Error ? error.message : String(error)
  const safeMessage = descriptor?.secret
    ? rawMessage.split(descriptor.secret).join('[redacted]')
    : rawMessage
  console.error(JSON.stringify({ ok: false, error: safeMessage }))
  process.exitCode = 1
}
