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

const url = process.env.NIRAI_HOLO_MCP_URL
const bearer = process.env.NIRAI_HOLO_MCP_GATE0_BEARER
if (!url || !bearer) throw new Error('NIRAI_HOLO_MCP_URL and NIRAI_HOLO_MCP_GATE0_BEARER are required')

function payload(result) {
  const text = result?.content?.find((item) => item?.type === 'text')?.text
  if (typeof text !== 'string') throw new Error('Tool result has no text payload')
  return JSON.parse(text)
}

const client = new Client({ name: 'nirai-holo-gate0-client', version: '0.1.0' })
const transport = new StreamableHTTPClientTransport(new URL(url), {
  requestInit: {
    headers: {
      Authorization: `Bearer ${bearer}`
    }
  }
})

await client.connect(transport)
try {
  const listed = await client.listTools()
  const toolNames = listed.tools.map((tool) => tool.name).sort()
  const expected = ['holo_attach', 'holo_get_snapshot', 'holo_say', 'holo_wait_events']
  if (JSON.stringify(toolNames) !== JSON.stringify(expected)) {
    throw new Error(`Unexpected Holo tool set: ${toolNames.join(',')}`)
  }
  if (toolNames.some((name) => /approve|decision/i.test(name))) {
    throw new Error('Approval/Decision tool must not exist in Holo MCP')
  }

  const attached = payload(await client.callTool({ name: 'holo_attach', arguments: {} }))
  const before = payload(await client.callTool({ name: 'holo_get_snapshot', arguments: {} }))

  const sayText = `gate0-mcp-say-${Date.now()}`
  const said = payload(await client.callTool({
    name: 'holo_say',
    arguments: { text: sayText }
  }))

  const events = payload(await client.callTool({
    name: 'holo_wait_events',
    arguments: {
      after_event_id: before.latest_event_id,
      timeout_sec: 1,
      limit: 50
    }
  }))
  if (!events.events.some((event) => event?.payload?.entry?.text === sayText)) {
    throw new Error('Holo World Say was not observed through holo_wait_events')
  }

  const timeout = payload(await client.callTool({
    name: 'holo_wait_events',
    arguments: {
      after_event_id: events.latest_event_id,
      timeout_sec: 0.05,
      limit: 50
    }
  }))
  if (timeout.timed_out !== true) throw new Error('Expected timeout path')

  const controller = new AbortController()
  const cancelStarted = Date.now()
  const cancelPromise = client.callTool({
    name: 'holo_wait_events',
    arguments: {
      after_event_id: timeout.latest_event_id,
      timeout_sec: 5,
      limit: 50
    }
  }, undefined, { signal: controller.signal })
  setTimeout(() => controller.abort(new Error('gate0 cancel')), 100)
  let cancelled = false
  try {
    await cancelPromise
  } catch {
    cancelled = true
  }
  const cancelElapsedMs = Date.now() - cancelStarted
  if (!cancelled) throw new Error('Expected MCP wait cancellation')

  const afterCancel = payload(await client.callTool({ name: 'holo_get_snapshot', arguments: {} }))

  console.log(JSON.stringify({
    ok: true,
    tool_names: toolNames,
    dive_session_id: attached.dive_session_id,
    snapshot_latest_event_id: before.latest_event_id,
    say_kind: said.kind,
    observed_event_count: events.events.length,
    timeout: timeout.timed_out,
    cancel_elapsed_ms: cancelElapsedMs,
    post_cancel_snapshot_ok: typeof afterCancel.latest_event_id === 'number'
  }))
} finally {
  await transport.terminateSession().catch(() => undefined)
  await client.close().catch(() => undefined)
}
