import { isHelloAckMessage, parseProtocolMessage } from '../protocol/parser'
import { createProtocolMessage, type ProtocolMessage } from '../protocol/types'
import { useConnectionStore } from '../stores/connectionStore'

const DEFAULT_CORE_URL = 'ws://127.0.0.1:8765'
const MAX_RECONNECT_DELAY_MS = 30_000

interface WebSocketLike {
  onopen: ((event: Event) => void) | null
  onmessage: ((event: MessageEvent) => void) | null
  onerror: ((event: Event) => void) | null
  onclose: ((event: CloseEvent) => void) | null
  send(data: string): void
  close(): void
}

type WebSocketFactory = (url: string) => WebSocketLike

export function getReconnectDelayMs(reconnectCount: number): number {
  if (reconnectCount <= 0) return 1000
  return Math.min(1000 * 2 ** reconnectCount, MAX_RECONNECT_DELAY_MS)
}

export class CoreConnection {
  private readonly url: string
  private readonly createSocket: WebSocketFactory
  private readonly onProtocolMessage?: (message: ProtocolMessage) => void
  private readonly authSecret: string
  private socket: WebSocketLike | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectCount = 0
  private stopped = true

  constructor(options?: {
    url?: string
    createSocket?: WebSocketFactory
    onProtocolMessage?: (message: ProtocolMessage) => void
    authSecret?: string
  }) {
    this.url = options?.url ?? DEFAULT_CORE_URL
    this.createSocket = options?.createSocket ?? ((url) => new WebSocket(url))
    this.onProtocolMessage = options?.onProtocolMessage
    this.authSecret = options?.authSecret ?? ''
  }

  start(): void {
    if (!this.stopped) return
    this.stopped = false
    this.connect(false)
  }

  send(type: string, payload: Record<string, unknown>, id?: string): boolean {
    if (this.stopped || this.socket === null) return false
    if (useConnectionStore.getState().status !== 'connected') return false
    this.socket.send(JSON.stringify(createProtocolMessage(type, payload, id)))
    return true
  }

  stop(): void {
    this.stopped = true
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.onopen = null
      socket.onmessage = null
      socket.onerror = null
      socket.onclose = null
      socket.close()
    }
    useConnectionStore.setState({
      ...useConnectionStore.getState(),
      status: 'disconnected',
      activeRequestId: null
    })
  }

  private connect(isReconnect: boolean): void {
    if (this.stopped) return

    useConnectionStore.setState({
      ...useConnectionStore.getState(),
      status: isReconnect ? 'reconnecting' : 'connecting',
      lastError: null
    })

    let socket: WebSocketLike
    try {
      socket = this.createSocket(this.url)
    } catch (error) {
      this.scheduleReconnect(error instanceof Error ? error.message : 'Core接続を開始できませんでした')
      return
    }

    this.socket = socket

    socket.onopen = () => {
      if (this.stopped || this.socket !== socket) return
      socket.send(JSON.stringify(createProtocolMessage('hello', {
        role: 'world',
        secret: this.authSecret
      })))
    }

    socket.onmessage = (event) => {
      if (this.stopped || this.socket !== socket) return
      const message = parseProtocolMessage(event.data)
      if (!message) return

      if (isHelloAckMessage(message)) {
        this.reconnectCount = 0
        useConnectionStore.setState({
          ...useConnectionStore.getState(),
          status: 'connected',
          lastError: null,
          reconnectCount: 0,
          reconnectDelayMs: 1000
        })
      }

      this.onProtocolMessage?.(message)
    }

    socket.onerror = () => {
      if (this.stopped || this.socket !== socket) return
      useConnectionStore.setState({
        ...useConnectionStore.getState(),
        lastError: 'Core WebSocket error'
      })
    }

    socket.onclose = () => {
      if (this.socket === socket) {
        this.socket = null
      }
      if (this.stopped) return
      this.scheduleReconnect('Coreとの接続が切れました')
    }
  }

  private scheduleReconnect(message: string): void {
    if (this.stopped || this.reconnectTimer !== null) return
    const delay = getReconnectDelayMs(this.reconnectCount)
    this.reconnectCount += 1

    useConnectionStore.setState({
      ...useConnectionStore.getState(),
      status: 'reconnecting',
      lastError: message,
      reconnectCount: this.reconnectCount,
      reconnectDelayMs: delay,
      activeRequestId: null
    })

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect(true)
    }, delay)
  }
}
