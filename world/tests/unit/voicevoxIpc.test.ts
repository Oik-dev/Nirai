import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  const handlers = new Map<string, (...args: unknown[]) => unknown>()
  return {
    handlers,
    handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => handlers.set(channel, handler))
  }
})

vi.mock('electron', () => ({ ipcMain: { handle: mocks.handle } }))

import { registerVoicevoxIpc } from '../../src/main/ipc/voicevoxIpc'

describe('VOICEVOX IPC', () => {
  beforeEach(() => {
    mocks.handlers.clear()
    mocks.handle.mockClear()
    vi.unstubAllGlobals()
    registerVoicevoxIpc()
  })

  it('registers health, speakers, and synthesize handlers', () => {
    expect([...mocks.handlers.keys()].sort()).toEqual([
      'voicevox:health',
      'voicevox:speakers',
      'voicevox:synthesize'
    ])
  })

  it('reports health from the local engine endpoint', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response('0.24.0', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const health = mocks.handlers.get('voicevox:health')

    await expect(health?.()).resolves.toBe(true)
    expect(String(fetchMock.mock.calls[0][0])).toContain('/version')
  })

  it('applies speed pitch and intonation before synthesis', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        speedScale: 1,
        pitchScale: 0,
        intonationScale: 1
      }), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(new Uint8Array([1, 2, 3]), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const synthesize = mocks.handlers.get('voicevox:synthesize')

    const audio = await synthesize?.({}, {
      text: 'test',
      style_id: 3,
      speed: 1.2,
      pitch: 0.05,
      intonation: 0.9
    }) as Uint8Array

    expect([...audio]).toEqual([1, 2, 3])
    expect(String(fetchMock.mock.calls[0][0])).toContain('/audio_query')
    expect(String(fetchMock.mock.calls[1][0])).toContain('/synthesis')
    const init = fetchMock.mock.calls[1][1] as RequestInit
    expect(JSON.parse(String(init.body))).toMatchObject({
      speedScale: 1.2,
      pitchScale: 0.05,
      intonationScale: 0.9
    })
  })
})
