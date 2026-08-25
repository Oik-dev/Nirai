import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SceneRuntime } from '../../src/renderer/src/runtime/SceneRuntime'
import { installM0AcceptanceBridge } from '../../src/renderer/src/runtime/M0AcceptanceBridge'
import { DEFAULT_VISUAL_TUNING } from '../../src/renderer/src/runtime/VisualTuning'

describe('M0AcceptanceBridge', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('keeps the tuning panel hidden until a development command changes it', () => {
    vi.stubGlobal('window', {})
    const onVisualTuningChange = vi.fn()
    const onVisualTuningPanelVisibilityChange = vi.fn()
    const runtime = {
      getVisualTuning: vi.fn(() => ({ ...DEFAULT_VISUAL_TUNING })),
      setVisualTuning: vi.fn((value) => value)
    } as unknown as SceneRuntime

    const remove = installM0AcceptanceBridge(runtime, {
      onVisualTuningChange,
      onVisualTuningPanelVisibilityChange
    })
    const bridge = (window as unknown as Window & {
      __niraiM0: {
        showVisualTuningPanel: () => boolean
        hideVisualTuningPanel: () => boolean
        setVisualTuningPanelVisible: (visible: boolean) => boolean
      }
    }).__niraiM0

    expect(onVisualTuningPanelVisibilityChange).not.toHaveBeenCalled()
    expect(bridge.showVisualTuningPanel()).toBe(true)
    expect(onVisualTuningPanelVisibilityChange).toHaveBeenLastCalledWith(true)
    expect(onVisualTuningChange).toHaveBeenLastCalledWith(DEFAULT_VISUAL_TUNING)
    expect(bridge.hideVisualTuningPanel()).toBe(false)
    expect(onVisualTuningPanelVisibilityChange).toHaveBeenLastCalledWith(false)
    expect(bridge.setVisualTuningPanelVisible(true)).toBe(true)

    remove()
    expect((window as Window & { __niraiM0?: unknown }).__niraiM0).toBeUndefined()
  })
})
