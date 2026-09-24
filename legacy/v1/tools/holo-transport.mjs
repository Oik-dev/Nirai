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
        const error = new Error(response.payload?.error || 'Nirai rejected Holo local request')
        error.result = response.payload
        finish(error)
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

export async function callHolo(request) {
  const descriptor = await readDescriptor()
  try {
    return await callCore(descriptor, request)
  } catch (error) {
    error.message = String(error.message).split(descriptor.secret).join('[redacted]')
    throw error
  }
}
