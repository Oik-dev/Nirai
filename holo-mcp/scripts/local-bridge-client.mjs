import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const fallbackNodeModules = process.env.NIRAI_HOLO_MCP_GATE0_NODE_MODULES
  ?? 'D:\\Products\\Elpis\\node_modules'

async function loadDependency(packageSpecifier, fallbackRelativePath) {
  try {
    return await import(packageSpecifier)
  } catch (error) {
    if (error?.code !== 'ERR_MODULE_NOT_FOUND') throw error
    return import(pathToFileURL(join(fallbackNodeModules, fallbackRelativePath)).href)
  }
}

const { Client } = await loadDependency(
  '@modelcontextprotocol/sdk/client/index.js',
  '@modelcontextprotocol/sdk/dist/esm/client/index.js'
)
const { StreamableHTTPClientTransport } = await loadDependency(
  '@modelcontextprotocol/sdk/client/streamableHttp.js',
  '@modelcontextprotocol/sdk/dist/esm/client/streamableHttp.js'
)

function bridgeFilePath() {
  if (process.env.NIRAI_HOLO_LOCAL_BRIDGE_FILE) {
    return process.env.NIRAI_HOLO_LOCAL_BRIDGE_FILE
  }
  const localAppData = process.env.LOCALAPPDATA
  if (!localAppData) {
    throw new Error('Nirai Holo local bridge is unavailable: LOCALAPPDATA is not set')
  }
  return join(localAppData, 'Nirai', 'holo-mcp-bridge.json')
}

function validateDescriptor(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('Invalid Nirai Holo local bridge descriptor')
  if (raw.version !== 1) throw new Error('Unsupported Nirai Holo local bridge descriptor version')
  if (typeof raw.url !== 'string') throw new Error('Nirai Holo local bridge URL is missing')
  if (typeof raw.bearer !== 'string' || raw.bearer.length < 32) {
    throw new Error('Nirai Holo local bridge credential is invalid')
  }

  const url = new URL(raw.url)
  const allowedHosts = new Set(['127.0.0.1', 'localhost'])
  if (url.protocol !== 'http:' || !allowedHosts.has(url.hostname) || url.pathname !== '/mcp') {
    throw new Error('Nirai Holo local bridge descriptor points outside localhost MCP')
  }
  return { url, bearer: raw.bearer }
}

async function readDescriptor() {
  let raw
  try {
    raw = JSON.parse(await readFile(bridgeFilePath(), 'utf8'))
  } catch (error) {
    if (error?.code === 'ENOENT') {
      throw new Error('Nirai Holo local bridge is not ready. Restart Nirai and try again.')
    }
    throw error
  }
  return validateDescriptor(raw)
}

function payload(result) {
  const text = result?.content?.find((item) => item?.type === 'text')?.text
  if (typeof text !== 'string') throw new Error('Holo MCP result has no text payload')
  return JSON.parse(text)
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

async function callCommand(client, argv) {
  const [command, ...args] = argv
  if (command === 'attach') {
    return payload(await client.callTool({ name: 'holo_attach', arguments: {} }))
  }
  if (command === 'snapshot') {
    return payload(await client.callTool({ name: 'holo_get_snapshot', arguments: {} }))
  }
  if (command === 'say') {
    const [text, to] = args
    if (typeof text !== 'string' || !text.trim()) {
      throw new Error('say requires a non-empty text argument')
    }
    return payload(await client.callTool({
      name: 'holo_say',
      arguments: { text, ...(typeof to === 'string' && to.trim() ? { to } : {}) }
    }))
  }
  if (command === 'wait') {
    const afterEventId = parseInteger(args[0], 'after_event_id', { min: 0, max: Number.MAX_SAFE_INTEGER })
    const timeoutSec = parseNumber(args[1], 'timeout_sec', { min: 0, max: 15 })
    const limit = args[2] === undefined
      ? 50
      : parseInteger(args[2], 'limit', { min: 1, max: 50 })
    return payload(await client.callTool({
      name: 'holo_wait_events',
      arguments: {
        after_event_id: afterEventId,
        timeout_sec: timeoutSec,
        limit
      }
    }))
  }
  throw new Error('Usage: local-bridge-client.mjs <attach|snapshot|say|wait> [...args]')
}

let descriptor
try {
  descriptor = await readDescriptor()
  const client = new Client({ name: 'nirai-holo-local-bridge', version: '0.1.0' })
  const transport = new StreamableHTTPClientTransport(descriptor.url, {
    requestInit: {
      headers: {
        Authorization: `Bearer ${descriptor.bearer}`
      }
    }
  })

  await client.connect(transport)
  try {
    const result = await callCommand(client, process.argv.slice(2))
    console.log(JSON.stringify({ ok: true, result }))
  } finally {
    await transport.terminateSession().catch(() => undefined)
    await client.close().catch(() => undefined)
  }
} catch (error) {
  const rawMessage = error instanceof Error ? error.message : String(error)
  const safeMessage = descriptor?.bearer
    ? rawMessage.split(descriptor.bearer).join('[redacted]')
    : rawMessage
  console.error(JSON.stringify({ ok: false, error: safeMessage }))
  process.exitCode = 1
}
