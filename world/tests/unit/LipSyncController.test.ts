import { describe, expect, it, vi } from 'vitest'
import { LipSyncController } from '../../src/renderer/src/world/vrm/LipSyncController'

describe('LipSyncController', () => {
  it('maps analyser amplitude to lip weight and clears it when detached', () => {
    const setLipWeight = vi.fn()
    const controller = new LipSyncController({ setLipWeight })
    const analyser = {
      getFloatTimeDomainData: (samples: Float32Array) => {
        samples.fill(0)
        samples[0] = 0.2
      }
    }

    controller.setAnalyser(analyser)
    controller.update()

    const activeWeight = setLipWeight.mock.calls.at(-1)?.[0] as number
    expect(activeWeight).toBeGreaterThan(0.9)

    controller.setAnalyser(null)
    expect(setLipWeight).toHaveBeenLastCalledWith(0)
  })

  it('suppresses very small analyser noise', () => {
    const setLipWeight = vi.fn()
    const controller = new LipSyncController({ setLipWeight })
    controller.setAnalyser({
      getFloatTimeDomainData: (samples) => samples.fill(0.005)
    })

    controller.update()

    expect(setLipWeight).toHaveBeenLastCalledWith(0)
  })
})
