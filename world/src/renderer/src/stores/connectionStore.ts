import { create } from 'zustand'

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'reconnecting'

export interface ConnectionState {
  readonly status: ConnectionStatus
  readonly lastError: string | null
  readonly reconnectCount: number
  readonly reconnectDelayMs: number
  readonly activeRequestId: string | null
}

export const INITIAL_CONNECTION_STATE: ConnectionState = {
  status: 'disconnected',
  lastError: null,
  reconnectCount: 0,
  reconnectDelayMs: 1000,
  activeRequestId: null
}

export const useConnectionStore = create<ConnectionState>(() => INITIAL_CONNECTION_STATE)
