import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_MAX_RENDER_FPS, RenderLoop } from '../../src/renderer/src/runtime/RenderLoop'

describe('RenderLoop', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('caps the default render cadence at 72 FPS while still following requestAnimationFrame', () => {
    expect(DEFAULT_MAX_RENDER_FPS).toBe(72)

    const callbacks: FrameRequestCallback[] = []
    let nextId = 1
    const request = vi.fn((callback: FrameRequestCallback) => {
      callbacks.push(callback)
      return nextId++
    })
    const cancel = vi.fn()
    vi.stubGlobal('requestAnimationFrame', request)
    vi.stubGlobal('cancelAnimationFrame', cancel)
    vi.spyOn(performance, 'now').mockReturnValue(0)

    const onFrame = vi.fn()
    const loop = new RenderLoop(onFrame)
    loop.start()

    callbacks.shift()?.(7)
    expect(onFrame).not.toHaveBeenCalled()

    callbacks.shift()?.(14)
    expect(onFrame).toHaveBeenCalledTimes(1)
    expect(onFrame).toHaveBeenLastCalledWith(0.014)

    callbacks.shift()?.(21)
    expect(onFrame).toHaveBeenCalledTimes(1)

    callbacks.shift()?.(28)
    expect(onFrame).toHaveBeenCalledTimes(2)
    expect(onFrame).toHaveBeenLastCalledWith(0.014)

    loop.stop()
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('keeps long-frame deltas bounded while advancing the next target frame', () => {
    const callbacks: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
      callbacks.push(callback)
      return callbacks.length
    }))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    vi.spyOn(performance, 'now').mockReturnValue(0)

    const onFrame = vi.fn()
    const loop = new RenderLoop(onFrame)
    loop.start()

    callbacks.shift()?.(250)
    expect(onFrame).toHaveBeenCalledOnce()
    expect(onFrame).toHaveBeenCalledWith(0.1)

    callbacks.shift()?.(257)
    expect(onFrame).toHaveBeenCalledOnce()

    callbacks.shift()?.(264)
    expect(onFrame).toHaveBeenCalledTimes(2)
  })
})
