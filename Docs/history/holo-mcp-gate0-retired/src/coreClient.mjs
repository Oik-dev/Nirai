import { randomUUID } from 'node:crypto'

const DEFAULT_CORE_URL = 'ws://127.0.0.1:8765'

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

export function identityFromAuthInfo(authInfo) {
  if (!authInfo || typeof authInfo.clientId !== 'string' || !authInfo.clientId) {
    throw new Error('Validated MCP authInfo is required')
  }
  if (!Array.isArray(authInfo.scopes)) {
    throw new Error('Validated MCP authInfo scopes are required')
  }
  return {
    client_id: authInfo.clientId,
    scopes: authInfo.scopes.filter((scope) => typeof scope === 'string'),
    ...(typeof authInfo.expiresAt === 'number' ? { expires_at: authInfo.expiresAt } : {})
  }
}

export class CoreHoloClient {
  constructor(options = {}) {
    this.url = options.url ?? process.env.NIRAI_CORE_URL ?? DEFAULT_CORE_URL
    this.internalSecret = options.internalSecret ?? process.env.NIRAI_HOLO_ADAPTER_SECRET
    if (!this.internalSecret) {
      throw new Error('NIRAI_HOLO_ADAPTER_SECRET is required')
    }
    if (typeof globalThis.WebSocket !== 'function') {
      throw new Error('This Nirai Holo MCP adapter requires a Node runtime with the standard WebSocket API')
    }
  }

  async attach(authInfo, signal) {
    return this.request('holo_attach_request', {}, authInfo, { signal, timeoutMs: 5000 })
  }

  async snapshot(authInfo, signal) {
    return this.request('holo_snapshot_request', {}, authInfo, { signal, timeoutMs: 5000 })
  }

  async worldSay(authInfo, { text, to }, signal) {
    return this.request(
      'holo_world_say_request',
      { text, ...(to ? { to } : {}) },
      authInfo,
      { signal, timeoutMs: 5000 }
    )
  }

  async waitEvents(authInfo, { afterEventId, timeoutSec, limit }, signal) {
    const timeoutMs = Math.max(3000, Math.ceil(timeoutSec * 1000) + 3000)
    return this.request(
      'holo_wait_events_request',
      {
        after_event_id: afterEventId,
        timeout_sec: timeoutSec,
        limit
      },
      authInfo,
      { signal, timeoutMs }
    )
  }

  request(type, payload, authInfo, { signal, timeoutMs }) {
    const identity = identityFromAuthInfo(authInfo)
    const requestId = randomUUID()
    const helloId = randomUUID()

    return new Promise((resolve, reject) => {
      let settled = false
      let helloAccepted = false
      const socket = new globalThis.WebSocket(this.url)
      const timer = setTimeout(() => {
        finish(new Error(`Nirai Core request timed out: ${type}`))
      }, timeoutMs)

      function cleanup() {
        clearTimeout(timer)
        signal?.removeEventListener('abort', abort)
      }

      function finish(error, value) {
        if (settled) return
        settled = true
        cleanup()
        if (socket.readyState === globalThis.WebSocket.OPEN || socket.readyState === globalThis.WebSocket.CONNECTING) {
          try { socket.close() } catch { /* already closing */ }
        }
        if (error) reject(error)
        else resolve(value)
      }

      const abort = () => finish(signal?.reason instanceof Error ? signal.reason : new Error('MCP request cancelled'))
      if (signal?.aborted) {
        abort()
        return
      }
      signal?.addEventListener('abort', abort, { once: true })

      socket.addEventListener('open', () => {
        socket.send(message('hello', {
          role: 'holo_adapter',
          secret: this.internalSecret
        }, helloId))
      })

      socket.addEventListener('message', (event) => {
        const response = parse(event.data)
        if (!response) return
        if (!helloAccepted && response.type === 'holo_adapter_hello_ack' && response.id === helloId) {
          helloAccepted = true
          socket.send(message(type, { identity, ...payload }, requestId))
          return
        }
        if (response.type !== 'holo_adapter_result' || response.id !== requestId) return
        if (response.payload?.ok !== true) {
          finish(new Error(response.payload?.error || `Nirai Core rejected ${type}`))
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
}
