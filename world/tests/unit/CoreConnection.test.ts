import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CoreConnection, getReconnectDelayMs } from '../../src/renderer/src/runtime/CoreConnection'
import { createProtocolMessage } from '../../src/renderer/src/protocol/types'
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
  send(data: string): void
  close(): void
}

function createFakeSocket(): FakeSocket {
  return {
    onopen: null,
    onmessage: null,
    onerror: null,
    onclose: null,
    sent: [],
    closeCount: 0,
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
    useConnectionStore.setState(INITIAL_CONNECTION_STATE, true)
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
      onProtocolMessage
    })

    connection.start()
    expect(useConnectionStore.getState().status).toBe('connecting')

    socket.onopen?.(new Event('open'))
    expect(socket.sent).toHaveLength(1)
    const hello = JSON.parse(socket.sent[0])
    expect(hello.type).toBe('hello')
    expect(hello.payload).toEqual({ role: 'world' })
    expect(useConnectionStore.getState().status).toBe('connecting')

    const ack = createProtocolMessage('hello_ack', {
      residents: [],
      locations: [],
      time_of_day: 'day',
      settings: { audio_volume: 100 },
      active_session: 'S-20260828-001'
    })
    socket.onmessage?.({ data: JSON.stringify(ack) } as MessageEvent)

    expect(useConnectionStore.getState().status).toBe('connected')
    expect(onProtocolMessage).toHaveBeenCalledWith(ack)

    connection.stop()
    expect(socket.closeCount).toBe(1)
    expect(useConnectionStore.getState().status).toBe('disconnected')
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
})
