import { describe, expect, it, vi } from 'vitest'
import { SpeechQueue } from '../../src/renderer/src/audio/SpeechQueue'

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve: () => void = () => undefined
  const promise = new Promise<void>((done) => { resolve = done })
  return { promise, resolve }
}

describe('SpeechQueue', () => {
  it('plays different residents through one global queue sequentially', async () => {
    const first = deferred()
    const second = deferred()
    const play = vi.fn()
      .mockImplementationOnce(async () => first.promise)
      .mockImplementationOnce(async () => second.promise)
      .mockResolvedValueOnce(undefined)
    const speaking = vi.fn()
    const audio = { play, stop: vi.fn() }
    const queue = new SpeechQueue(audio, speaking)

    queue.enqueue({ requestId: 'R1', residentName: 'Lapan', text: 'a', audio: new Uint8Array([1]) })
    queue.enqueue({ requestId: 'R1', residentName: 'Kina', text: 'b', audio: new Uint8Array([2]) })
    queue.enqueue({ requestId: 'R1', residentName: 'Shiro', text: 'c', audio: new Uint8Array([3]) })
    await Promise.resolve()
    expect(play).toHaveBeenCalledTimes(1)
    expect(speaking).toHaveBeenLastCalledWith('Lapan', 'R1')

    first.resolve()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(play).toHaveBeenCalledTimes(2)
    expect(speaking).toHaveBeenLastCalledWith('Kina', 'R1')

    second.resolve()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(play).toHaveBeenCalledTimes(3)
    expect(speaking).toHaveBeenCalledWith('Shiro', 'R1')
  })

  it('cancels only the requested current speech and removes queued matches', async () => {
    const first = deferred()
    const play = vi.fn()
      .mockImplementationOnce(async () => first.promise)
      .mockResolvedValue(undefined)
    const stop = vi.fn(() => first.resolve())
    const speaking = vi.fn()
    const queue = new SpeechQueue({ play, stop }, speaking)

    queue.enqueue({ requestId: 'R1', residentName: 'Lapan', text: 'a', audio: new Uint8Array([1]) })
    queue.enqueue({ requestId: 'R1', residentName: 'Lapan', text: 'b', audio: new Uint8Array([2]) })
    queue.enqueue({ requestId: 'R2', residentName: 'Lapan', text: 'c', audio: new Uint8Array([3]) })
    await Promise.resolve()
    expect(speaking).toHaveBeenCalledWith('Lapan', 'R1')

    queue.cancel('R1')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(stop).toHaveBeenCalledTimes(1)
    expect(speaking).toHaveBeenCalledWith(null, null)
    expect(play).toHaveBeenCalledTimes(2)
    expect((play.mock.calls[1][0] as Uint8Array)[0]).toBe(3)
  })

  it('cancels one deleted resident without discarding other residents queued speech', async () => {
    const first = deferred()
    const play = vi.fn()
      .mockImplementationOnce(async () => first.promise)
      .mockResolvedValue(undefined)
    const stop = vi.fn(() => first.resolve())
    const speaking = vi.fn()
    const queue = new SpeechQueue({ play, stop }, speaking)

    queue.enqueue({ requestId: 'R1', residentName: 'Lapan', text: 'a', audio: new Uint8Array([1]) })
    queue.enqueue({ requestId: 'R1', residentName: 'Kina', text: 'b', audio: new Uint8Array([2]) })
    queue.enqueue({ requestId: 'R2', residentName: 'Lapan', text: 'c', audio: new Uint8Array([3]) })
    queue.enqueue({ requestId: 'R2', residentName: 'Shiro', text: 'd', audio: new Uint8Array([4]) })
    await Promise.resolve()

    queue.cancelResident('Lapan')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(stop).toHaveBeenCalledTimes(1)
    expect(play).toHaveBeenCalledTimes(3)
    expect((play.mock.calls[1][0] as Uint8Array)[0]).toBe(2)
    expect((play.mock.calls[2][0] as Uint8Array)[0]).toBe(4)
  })
})
