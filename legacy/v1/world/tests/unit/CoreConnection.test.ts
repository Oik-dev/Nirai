import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CoreConnection, getReconnectDelayMs } from '../../src/renderer/src/runtime/CoreConnection'
import {
  NIRAI_PROTOCOL_VERSION,
  createProtocolMessage,
  createWorldHelloPayload
} from '../../src/renderer/src/protocol/types'
import {
  INITIAL_CONNECTION_STATE,
  useConnectionStore
} from '../../src/renderer/src/stores/connectionStore'

interface FakeSocket {
  onopen: ((event: Event) => void) | null
  onmessage: ((event: MessageEvent) => void) | null
  onerror: ((event: Event) => void) | null
  onclose: ((event: CloseEvent) => void) | null
  readonly sent: string[]
  closeCount: number
  readyState: number
  send(data: string): void
  close(): void
}

const CORE_PROTOCOL = {
  version: NIRAI_PROTOCOL_VERSION,
  runtime_id: 'nirai-core',
  capabilities: ['semantic-actions-v1']
}

function createFakeSocket(): FakeSocket {
  return {
    onopen: null,
    onmessage: null,
    onerror: null,
    onclose: null,
    sent: [],
    closeCount: 0,
    readyState: 1,
    send(data: string) {
      this.sent.push(data)
    },
    close() {
      this.closeCount += 1
    }
  }
}

describe('CoreConnection', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useConnectionStore.setState(INITIAL_CONNECTION_STATE, true)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('uses the fixed 1,2,4,8,16,30 second sequence and caps at 30 seconds', () => {
    expect([0, 1, 2, 3, 4, 5, 6, 20].map(getReconnectDelayMs)).toEqual([
      1000,
      2000,
      4000,
      8000,
      16000,
      30000,
      30000,
      30000
    ])
  })

  it('sends hello on socket open and only becomes connected after hello_ack', () => {
    const socket = createFakeSocket()
    const onProtocolMessage = vi.fn()
    const connection = new CoreConnection({
      createSocket: () => socket,
      onProtocolMessage,
      authSecret: 'world-secret'
    })

    connection.start()
    expect(useConnectionStore.getState().status).toBe('connecting')

    socket.onopen?.(new Event('open'))
    expect(socket.sent).toHaveLength(1)
    const hello = JSON.parse(socket.sent[0])
    expect(hello.type).toBe('hello')
    expect(hello.payload).toEqual(createWorldHelloPayload('world-secret'))
    expect(useConnectionStore.getState().status).toBe('connecting')

    const ack = createProtocolMessage('hello_ack', {
      protocol: CORE_PROTOCOL,
      residents: [],
      locations: [],
      time_of_day: 'day',
      settings: { audio_volume: 100 },
      active_session: 'S-20260828-001',
      holo_addon: { local_bridge_state: 'not_started', current_dive_session_id: null }
    })
    socket.onmessage?.({ data: JSON.stringify(ack) } as MessageEvent)

    expect(useConnectionStore.getState().status).toBe('connected')
    expect(onProtocolMessage).toHaveBeenCalledWith(ack)

    connection.stop()
    expect(socket.closeCount).toBe(1)
    expect(useConnectionStore.getState().status).toBe('disconnected')
  })

  it('preserves a correlation id on Holo Dive messages after handshake', () => {
    const socket = createFakeSocket()
    const connection = new CoreConnection({ createSocket: () => socket })

    connection.start()
    socket.onopen?.(new Event('open'))
    socket.onmessage?.({
      data: JSON.stringify(createProtocolMessage('hello_ack', {
        protocol: CORE_PROTOCOL,
        residents: [],
        locations: [],
        time_of_day: 'day',
        settings: { audio_volume: 100 },
        active_session: null,
        holo_addon: { local_bridge_state: 'not_started', current_dive_session_id: null }
      }))
    } as MessageEvent)

    expect(connection.send(
      'holo_dive_started',
      { dive_session_id: 'DIVE-2' },
      'HOLO-DIVE-REQ-2'
    )).toBe(true)
    const sent = JSON.parse(socket.sent.at(-1) ?? '{}')
    expect(sent.type).toBe('holo_dive_started')
    expect(sent.id).toBe('HOLO-DIVE-REQ-2')
    expect(sent.payload).toEqual({ dive_session_id: 'DIVE-2' })
    connection.stop()
  })

  it('rejects a hello_ack from an incompatible Core protocol version', () => {
    const socket = createFakeSocket()
    const connection = new CoreConnection({ createSocket: () => socket })

    connection.start()
    socket.onopen?.(new Event('open'))
    socket.onmessage?.({
      data: JSON.stringify(createProtocolMessage('hello_ack', {
        protocol: { ...CORE_PROTOCOL, version: NIRAI_PROTOCOL_VERSION + 1 },
        residents: [],
        locations: [],
        time_of_day: 'day',
        settings: { audio_volume: 100 },
        active_session: null,
        holo_addon: { local_bridge_state: 'not_started', current_dive_session_id: null }
      }))
    } as MessageEvent)

    expect(useConnectionStore.getState().status).toBe('disconnected')
    expect(useConnectionStore.getState().lastError).toContain('互換性')
    expect(socket.closeCount).toBe(1)
    connection.stop()
  })

  it('stops reconnecting when Core explicitly rejects the World protocol version', () => {
    const socket = createFakeSocket()
    const factory = vi.fn(() => socket)
    const connection = new CoreConnection({ createSocket: factory })

    connection.start()
    socket.onopen?.(new Event('open'))
    socket.onclose?.({ code: 4004, reason: 'Unsupported Nirai protocol version' } as CloseEvent)
    vi.advanceTimersByTime(30_000)

    expect(factory).toHaveBeenCalledTimes(1)
    expect(useConnectionStore.getState().status).toBe('disconnected')
    expect(useConnectionStore.getState().lastError).toContain('Unsupported Nirai protocol')
    connection.start()
    expect(factory).toHaveBeenCalledTimes(2)
    connection.stop()
  })

  it('does not treat an arbitrary valid protocol message as handshake completion', () => {
    const socket = createFakeSocket()
    const connection = new CoreConnection({ createSocket: () => socket })

    connection.start()
    socket.onopen?.(new Event('open'))
    socket.onmessage?.({
      data: JSON.stringify(createProtocolMessage('notice', { level: 'INFO', text: 'ready' }))
    } as MessageEvent)

    expect(useConnectionStore.getState().status).toBe('connecting')
    connection.stop()
  })

  it('returns false and reconnects when the transport is closing before onclose', () => {
    const socket = createFakeSocket()
    const factory = vi.fn(() => socket)
    const connection = new CoreConnection({ createSocket: factory })
    connection.start()
    useConnectionStore.setState({ status: 'connected' })
    socket.readyState = 2
    expect(connection.send('say', { text: 'keep draft' })).toBe(false)
    expect(socket.sent).toEqual([])
    expect(useConnectionStore.getState().status).toBe('reconnecting')
    vi.advanceTimersByTime(1000)
    expect(factory).toHaveBeenCalledTimes(2)
    connection.stop()
  })

  it('handles send exceptions without exposing transport error details', () => {
    const socket = createFakeSocket()
    socket.send = () => { throw new Error('sensitive transport details') }
    const connection = new CoreConnection({ createSocket: () => socket })
    connection.start()
    useConnectionStore.setState({ status: 'connected' })
    expect(connection.send('say', { text: 'keep draft' })).toBe(false)
    expect(useConnectionStore.getState().status).toBe('reconnecting')
    expect(useConnectionStore.getState().lastError).not.toContain('sensitive')
    connection.stop()
  })

  it('recovers from a hello send exception using the normal reconnect path', () => {
    const socket = createFakeSocket()
    socket.send = () => { throw new Error('cannot send') }
    const connection = new CoreConnection({ createSocket: () => socket })
    connection.start()
    expect(() => socket.onopen?.(new Event('open'))).not.toThrow()
    expect(useConnectionStore.getState().status).toBe('reconnecting')
    connection.stop()
  })

  it('ignores an old close callback after a new connection has started', () => {
    const first = createFakeSocket()
    const second = createFakeSocket()
    const factory = vi.fn().mockReturnValueOnce(first).mockReturnValue(second)
    const connection = new CoreConnection({ createSocket: factory })
    connection.start()
    const staleClose = first.onclose
    connection.stop()
    connection.start()
    staleClose?.(new Event('close') as CloseEvent)
    vi.advanceTimersByTime(30_000)
    expect(factory).toHaveBeenCalledTimes(2)
    expect(useConnectionStore.getState().status).toBe('connecting')
    connection.stop()
  })
})
