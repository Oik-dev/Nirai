import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import {
  CAUSTIC_FIELD_GLSL,
  SURFACE_WAVE_GLSL,
  beerLambertTransmittance,
  calculateSunSurfaceAnchor,
  createUnderwaterOpticsState
} from '../../src/renderer/src/world/environment/UnderwaterOptics'

describe('UnderwaterOptics', () => {
  it('defines one sun, surface and stage shared by every underwater effect', () => {
    const optics = createUnderwaterOpticsState()

    expect(optics.sunDirection.value.length()).toBeCloseTo(1, 5)
    expect(optics.sunDirection.value.y).toBeGreaterThan(0.65)
    expect(optics.surfaceY.value).toBeGreaterThan(3.5)
    expect(optics.stageCenter.value.length()).toBeLessThan(1.5)
    expect(optics.sunSurfaceAnchor.value).toEqual(
      calculateSunSurfaceAnchor(
        optics.stageCenter.value,
        optics.surfaceY.value,
        optics.sunDirection.value
      )
    )
    expect(optics.sunSurfaceAnchor.value.y).toBeCloseTo(optics.surfaceY.value, 5)
    expect(optics.sunRadiance.value.r).toBeGreaterThan(1)
    expect(optics.absorption.value.r).toBeGreaterThan(optics.absorption.value.g)
    expect(optics.absorption.value.g).toBeGreaterThan(optics.absorption.value.b)
    expect(optics.scatteringColor.value.b).toBeGreaterThan(optics.scatteringColor.value.r)
    expect(optics.scatteringStrength.value).toBeGreaterThan(0)
    expect(optics.scatteringStrength.value).toBeLessThan(0.7)
    expect(SURFACE_WAVE_GLSL).toContain('sampleSurfaceWave')
    expect(CAUSTIC_FIELD_GLSL).toContain('sampleSurfaceWave')
    expect(CAUSTIC_FIELD_GLSL).toContain('sampleCausticField')
  })

  it('applies Beer-Lambert absorption by travelled water distance', () => {
    const optics = createUnderwaterOpticsState()
    const near = beerLambertTransmittance(1, optics.absorption.value)
    const far = beerLambertTransmittance(12, optics.absorption.value)

    expect(far.r).toBeLessThan(near.r)
    expect(far.g).toBeLessThan(near.g)
    expect(far.b).toBeLessThan(near.b)
    expect(far.r).toBeLessThan(far.b)
  })

  it('keeps the sun anchor calculation finite for grazing light and invalid surfaces', () => {
    const stage = new THREE.Vector3(1, 2, -3)
    const grazing = calculateSunSurfaceAnchor(stage, 5, new THREE.Vector3(1, 0.01, -1))
    const invalid = calculateSunSurfaceAnchor(stage, 1, new THREE.Vector3(0, 1, 0))

    expect(grazing.toArray().every(Number.isFinite)).toBe(true)
    expect(grazing.y).toBeCloseTo(5, 5)
    expect(invalid).toEqual(stage)
  })
})
