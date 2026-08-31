import { createServer } from 'node:http'
import { randomUUID, timingSafeEqual } from 'node:crypto'
import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { CoreHoloClient } from './coreClient.mjs'

const gate0FallbackNodeModules = process.env.NIRAI_HOLO_MCP_GATE0_NODE_MODULES
  ?? 'D:\\Products\\Elpis\\node_modules'

async function loadDependency(packageSpecifier, fallbackRelativePath) {
  try {
    return await import(packageSpecifier)
  } catch (error) {
    if (error?.code !== 'ERR_MODULE_NOT_FOUND') throw error
    return import(pathToFileURL(join(gate0FallbackNodeModules, fallbackRelativePath)).href)
  }
}

const { McpServer } = await loadDependency(
  '@modelcontextprotocol/sdk/server/mcp.js',
  '@modelcontextprotocol/sdk/dist/esm/server/mcp.js'
)
const { StreamableHTTPServerTransport } = await loadDependency(
  '@modelcontextprotocol/sdk/server/streamableHttp.js',
  '@modelcontextprotocol/sdk/dist/esm/server/streamableHttp.js'
)
const { isInitializeRequest } = await loadDependency(
  '@modelcontextprotocol/sdk/types.js',
  '@modelcontextprotocol/sdk/dist/esm/types.js'
)
const z = await loadDependency('zod/v4', 'zod/v4/index.js')

const HOST = '127.0.0.1'
const PORT = Number(process.env.NIRAI_HOLO_MCP_PORT ?? 8766)
const gate0Bearer = process.env.NIRAI_HOLO_MCP_GATE0_BEARER
const gate0ClientId = process.env.NIRAI_HOLO_MCP_GATE0_CLIENT_ID ?? 'nirai-holo-gate0-client'
const gate0Scopes = (process.env.NIRAI_HOLO_MCP_GATE0_SCOPES ?? 'read_snapshot world_action')
  .split(/\s+/)
  .filter(Boolean)
const gate0ExpiresAt = process.env.NIRAI_HOLO_MCP_GATE0_EXPIRES_AT
  ? Number(process.env.NIRAI_HOLO_MCP_GATE0_EXPIRES_AT)
  : undefined
const localBridgeFile = process.env.NIRAI_HOLO_LOCAL_BRIDGE_FILE
  ?? (process.env.LOCALAPPDATA
    ? join(process.env.LOCALAPPDATA, 'Nirai', 'holo-mcp-bridge.json')
    : null)

if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65535) {
  throw new Error('NIRAI_HOLO_MCP_PORT must be a valid TCP port')
}
if (!gate0Bearer || gate0Bearer.length < 32) {
  throw new Error('NIRAI_HOLO_MCP_GATE0_BEARER must be at least 32 characters')
}

const core = new CoreHoloClient()
const sessions = new Map()

async function publishLocalBridgeDescriptor() {
  if (!localBridgeFile) return
  const payload = {
    version: 1,
    url: `http://${HOST}:${PORT}/mcp`,
    bearer: gate0Bearer,
    client_id: gate0ClientId,
    scopes: gate0Scopes,
    server_pid: process.pid,
    started_at: new Date().toISOString()
  }
  await mkdir(dirname(localBridgeFile), { recursive: true })
  const temporary = `${localBridgeFile}.${process.pid}.tmp`
  await writeFile(temporary, `${JSON.stringify(payload)}\n`, {
    encoding: 'utf8',
    mode: 0o600
  })
  await rename(temporary, localBridgeFile)
}

async function removeLocalBridgeDescriptor() {
  if (!localBridgeFile) return
  try {
    const payload = JSON.parse(await readFile(localBridgeFile, 'utf8'))
    if (payload?.server_pid !== process.pid) return
    await unlink(localBridgeFile)
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      console.error('[nirai-holo-mcp] local bridge descriptor cleanup failed')
    }
  }
}

function constantTimeEquals(left, right) {
  const a = Buffer.from(left)
  const b = Buffer.from(right)
  return a.length === b.length && timingSafeEqual(a, b)
}

function requestAuth(req, res) {
  const expectedHosts = new Set([`${HOST}:${PORT}`, `localhost:${PORT}`])
  if (!expectedHosts.has(String(req.headers.host ?? '').toLowerCase())) {
    res.writeHead(403).end('Forbidden')
    return null
  }

  const origin = req.headers.origin
  if (origin) {
    const allowedOrigins = new Set([`http://${HOST}:${PORT}`, `http://localhost:${PORT}`])
    if (!allowedOrigins.has(origin)) {
      res.writeHead(403).end('Forbidden')
      return null
    }
  }

  const authorization = req.headers.authorization
  const token = typeof authorization === 'string' && authorization.startsWith('Bearer ')
    ? authorization.slice('Bearer '.length)
    : null
  if (!token || !constantTimeEquals(token, gate0Bearer)) {
    res.writeHead(401, { 'WWW-Authenticate': 'Bearer realm="nirai-holo-mcp"' }).end('Unauthorized')
    return null
  }
  if (gate0ExpiresAt !== undefined && (!Number.isFinite(gate0ExpiresAt) || gate0ExpiresAt <= Date.now() / 1000)) {
    res.writeHead(401, { 'WWW-Authenticate': 'Bearer realm="nirai-holo-mcp", error="invalid_token"' }).end('Unauthorized')
    return null
  }

  return {
    token,
    clientId: gate0ClientId,
    scopes: gate0Scopes,
    ...(gate0ExpiresAt !== undefined ? { expiresAt: gate0ExpiresAt } : {})
  }
}

function jsonToolResult(payload) {
  return {
    content: [{ type: 'text', text: JSON.stringify(payload) }]
  }
}

function createHoloMcpServer() {
  const server = new McpServer({
    name: 'nirai-holo-mcp',
    version: '0.1.0'
  })

  server.registerTool('holo_attach', {
    description: 'Attach the validated MCP identity to the one-time Dive window opened directly by Master in Nirai.',
    annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false }
  }, async (extra) => {
    const result = await core.attach(extra.authInfo, extra.signal)
    return jsonToolResult({
      attached: true,
      dive_session_id: result.dive_session_id,
      scopes: result.scopes
    })
  })

  server.registerTool('holo_get_snapshot', {
    description: 'Read the allowlisted public Nirai snapshot for the currently attached Holo Dive.',
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false }
  }, async (extra) => {
    const result = await core.snapshot(extra.authInfo, extra.signal)
    return jsonToolResult(result.snapshot)
  })

  server.registerTool('holo_say', {
    description: 'Publish a Holo-authored public World Say in Nirai. This is not an approval or decision tool.',
    inputSchema: {
      text: z.string().trim().min(1).max(4000),
      to: z.string().trim().min(1).max(100).optional()
    },
    annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false }
  }, async ({ text, to }, extra) => {
    const result = await core.worldSay(extra.authInfo, { text, to }, extra.signal)
    return jsonToolResult(result.entry)
  })

  server.registerTool('holo_wait_events', {
    description: 'Wait briefly for allowlisted Nirai semantic events after an event cursor. Cancellation closes the Core wait.',
    inputSchema: {
      after_event_id: z.number().int().nonnegative(),
      timeout_sec: z.number().min(0).max(15),
      limit: z.number().int().min(1).max(50).default(50)
    },
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false }
  }, async ({ after_event_id, timeout_sec, limit }, extra) => {
    const result = await core.waitEvents(extra.authInfo, {
      afterEventId: after_event_id,
      timeoutSec: timeout_sec,
      limit
    }, extra.signal)
    return jsonToolResult({
      events: result.events,
      latest_event_id: result.latest_event_id,
      timed_out: result.timed_out
    })
  })

  return server
}

async function readJsonBody(req) {
  const chunks = []
  let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > 1024 * 1024) throw new Error('MCP request body exceeds 1 MiB')
    chunks.push(chunk)
  }
  if (chunks.length === 0) return undefined
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

async function handleMcp(req, res) {
  const auth = requestAuth(req, res)
  if (!auth) return
  req.auth = auth

  try {
    const sessionId = typeof req.headers['mcp-session-id'] === 'string'
      ? req.headers['mcp-session-id']
      : null

    if (req.method === 'POST') {
      const body = await readJsonBody(req)
      let record = sessionId ? sessions.get(sessionId) : null
      if (!record) {
        if (sessionId || !isInitializeRequest(body)) {
          res.writeHead(400, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({
            jsonrpc: '2.0',
            error: { code: -32000, message: 'Invalid or missing MCP session' },
            id: null
          }))
          return
        }

        const mcpServer = createHoloMcpServer()
        const transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (newSessionId) => {
            sessions.set(newSessionId, { server: mcpServer, transport })
          }
        })
        transport.onclose = () => {
          const currentId = transport.sessionId
          if (currentId) sessions.delete(currentId)
        }
        await mcpServer.connect(transport)
        await transport.handleRequest(req, res, body)
        return
      }
      await record.transport.handleRequest(req, res, body)
      return
    }

    if (req.method === 'GET' || req.method === 'DELETE') {
      const record = sessionId ? sessions.get(sessionId) : null
      if (!record) {
        res.writeHead(400).end('Invalid or missing MCP session')
        return
      }
      await record.transport.handleRequest(req, res)
      return
    }

    res.writeHead(405, { Allow: 'GET, POST, DELETE' }).end('Method Not Allowed')
  } catch (error) {
    if (!res.headersSent) {
      res.writeHead(500, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({
        jsonrpc: '2.0',
        error: { code: -32603, message: 'Internal server error' },
        id: null
      }))
    }
    console.error('[nirai-holo-mcp] request failed:', error instanceof Error ? error.message : String(error))
  }
}

const httpServer = createServer((req, res) => {
  const url = new URL(req.url ?? '/', `http://${req.headers.host ?? `${HOST}:${PORT}`}`)
  if (url.pathname !== '/mcp') {
    res.writeHead(404).end('Not Found')
    return
  }
  void handleMcp(req, res)
})

httpServer.listen(PORT, HOST, () => {
  void publishLocalBridgeDescriptor()
    .then(() => {
      console.log(`[nirai-holo-mcp] listening on http://${HOST}:${PORT}/mcp`)
      if (localBridgeFile) console.log('[nirai-holo-mcp] local bridge ready')
    })
    .catch(() => {
      console.error('[nirai-holo-mcp] local bridge descriptor publish failed')
    })
})

let shutdownPromise = null
async function shutdown() {
  if (shutdownPromise) return shutdownPromise
  shutdownPromise = (async () => {
    for (const { server, transport } of sessions.values()) {
      await transport.close().catch(() => undefined)
      await server.close().catch(() => undefined)
    }
    sessions.clear()
    await new Promise((resolve) => httpServer.close(resolve))
    await removeLocalBridgeDescriptor()
  })()
  return shutdownPromise
}

process.on('SIGINT', () => void shutdown().finally(() => process.exit(0)))
process.on('SIGTERM', () => void shutdown().finally(() => process.exit(0)))
