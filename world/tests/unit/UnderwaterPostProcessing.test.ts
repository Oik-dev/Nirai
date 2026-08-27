import { describe, expect, it, vi } from 'vitest'
import { createHash } from 'node:crypto'
import * as THREE from 'three'

const captures = vi.hoisted(() => ({
  shaderPasses: [] as Array<{ uniforms: Record<string, { value: unknown }> }>,
  composerTargets: [] as THREE.WebGLRenderTarget[],
  composerPasses: [] as unknown[],
  composerInstances: [] as Array<{
    renderTarget1: { samples: number }
    renderTarget2: { samples: number }
  }>
}))

vi.mock('three/addons/postprocessing/EffectComposer.js', () => ({
  EffectComposer: class {
    renderTarget1 = { samples: 0 }
    renderTarget2 = { samples: 0 }
    readBuffer: { depthTexture: THREE.DepthTexture | null }
    writeBuffer: { depthTexture: THREE.DepthTexture | null }
    setPixelRatio = vi.fn()
    addPass = vi.fn((pass: unknown) => captures.composerPasses.push(pass))
    render = vi.fn()
    setSize = vi.fn()
    dispose = vi.fn()

    constructor(_renderer: THREE.WebGLRenderer, target: THREE.WebGLRenderTarget) {
      captures.composerTargets.push(target)
      captures.composerInstances.push(this)
      this.readBuffer = { depthTexture: target.depthTexture }
      this.writeBuffer = { depthTexture: target.depthTexture }
    }
  }
}))

vi.mock('three/addons/postprocessing/RenderPass.js', () => ({
  RenderPass: class { dispose = vi.fn() }
}))

vi.mock('three/addons/postprocessing/ShaderPass.js', () => ({
  ShaderPass: class {
    uniforms: Record<string, { value: unknown }>
    dispose = vi.fn()

    constructor(shader: { uniforms: Record<string, { value: unknown }> }) {
      this.uniforms = Object.fromEntries(
        Object.entries(shader.uniforms).map(([name, uniform]) => [name, { value: uniform.value }])
      )
      captures.shaderPasses.push(this)
    }
  }
}))

vi.mock('three/addons/postprocessing/OutputPass.js', () => ({
  OutputPass: class { dispose = vi.fn() }
}))

import { createUnderwaterOpticsState } from '../../src/renderer/src/world/environment/UnderwaterOptics'
import {
  UNDERWATER_BILATERAL_SHADER,
  UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER,
  UNDERWATER_ILLUMINATION_SHADER,
  UNDERWATER_SHADER,
  UnderwaterDepthAwareCompositePass,
  UnderwaterIlluminationPass,
  UnderwaterPostProcessing
} from '../../src/renderer/src/world/environment/UnderwaterPostProcessing'

describe('UnderwaterPostProcessing', () => {
  it('uses the requested quality and lets duplicated visual effects be disabled in the final composite', () => {
    const renderer = { getPixelRatio: () => 1 } as unknown as THREE.WebGLRenderer
    const post = new UnderwaterPostProcessing(
      renderer,
      new THREE.Scene(),
      new THREE.PerspectiveCamera(),
      'low',
      createUnderwaterOpticsState()
    )
    const pass = captures.shaderPasses.at(-1)
    const illuminationPass = captures.composerPasses.filter(
      (candidate) => candidate instanceof UnderwaterIlluminationPass
    ).at(-1) as UnderwaterIlluminationPass | undefined

    expect(captures.composerTargets.at(-1)?.depthTexture).toBeInstanceOf(THREE.DepthTexture)
    expect(pass?.uniforms.tDepth.value).toBeInstanceOf(THREE.DepthTexture)
    expect(pass?.uniforms.waterSurfaceStrength.value).toBe(1)
    expect(pass?.uniforms.causticsStrength.value).toBe(1)
    expect(illuminationPass?.effectStrength).toBe(1)
    expect(illuminationPass?.raySteps).toBe(12)

    post.setWaterSurfaceStrength(1.75)
    expect(pass?.uniforms.waterSurfaceStrength.value).toBe(1.75)
    post.setWaterSurfaceStrength(99)
    expect(pass?.uniforms.waterSurfaceStrength.value).toBe(2.5)
    post.setWaterSurfaceStrength(1)

    post.setLightShaftSpeed(0.25)
    const illuminationUpdate = vi.spyOn(illuminationPass!, 'update')
    post.render(2)
    expect(post.getLightShaftSpeed()).toBe(0.25)
    expect(illuminationUpdate).toHaveBeenCalledWith(
      0.5,
      expect.any(THREE.Matrix4),
      expect.any(THREE.Camera)
    )

    post.setLightShaftSpeed(99)
    expect(post.getLightShaftSpeed()).toBe(10)

    post.setEffectEnabled('waterSurface', false)
    post.setEffectEnabled('lightShafts', false)
    post.setEffectEnabled('caustics', false)

    expect(pass?.uniforms.waterSurfaceStrength.value).toBe(0)
    expect(illuminationPass?.effectStrength).toBe(0)
    expect(pass?.uniforms.causticsStrength.value).toBe(0)
  })

  it('keeps bright caustics and sun shafts without a full-screen bloom pass', () => {
    const passCountBefore = captures.composerPasses.length
    const renderer = { getPixelRatio: () => 1 } as unknown as THREE.WebGLRenderer
    const post = new UnderwaterPostProcessing(
      renderer,
      new THREE.Scene(),
      new THREE.PerspectiveCamera(),
      'high',
      createUnderwaterOpticsState()
    )

    expect(UNDERWATER_SHADER.fragmentShader).toContain('directSunRadiance')
    expect(UNDERWATER_SHADER.fragmentShader).toContain('causticRadiance')
    expect(captures.composerPasses.length - passCountBefore).toBe(5)
    expect(captures.composerInstances.at(-1)?.renderTarget1.samples).toBe(2)
    expect(captures.composerInstances.at(-1)?.renderTarget2.samples).toBe(2)

    post.dispose()
  })

  it('preserves the accepted ray-marched sun shape without the rejected local density filters', () => {
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('reconstructWorldPosition')
    expect(UNDERWATER_SHADER.fragmentShader).toContain('beerLambert')
    expect(UNDERWATER_SHADER.fragmentShader).not.toContain('MAX_RAY_STEPS')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('MAX_RAY_STEPS')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('sampleCausticField')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('volumetricScatterDensity')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('projectSampleToSurface')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('interleavedGradientNoise')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('float caustic = sampleCausticField')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).not.toContain('filteredCaustic')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).not.toContain('hashJitter')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('0.39 + dither')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('1.0 - exp(-illumination)')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('uSunSurfaceAnchor')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).not.toContain('billboardRight')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).not.toContain('rayBundles')
    expect(UNDERWATER_SHADER.fragmentShader).toContain('uAbsorption')
    expect(UNDERWATER_SHADER.fragmentShader).toContain('uScatteringColor')
    expect(UNDERWATER_SHADER.fragmentShader).toContain('uScatteringStrength')
    expect(UNDERWATER_SHADER.fragmentShader).toContain('uSunRadiance')
    expect(UNDERWATER_SHADER.fragmentShader).not.toContain('uExtinction')
    expect(UNDERWATER_SHADER.fragmentShader)
      .toContain('float waterDistance = min(reconstructedDistance, 28.0)')
    expect(UNDERWATER_SHADER.fragmentShader)
      .not.toContain('depth > 0.9999 ? 28.0 : min(reconstructedDistance, 38.0)')
  })

  it('locks the Master-approved light-shaft visual unless that scope is explicitly reopened', () => {
    const optics = createUnderwaterOpticsState()
    const normalizeLineEndings = (source: string): string => source.replace(/\r\n/g, '\n')
    const fingerprint = createHash('sha256')
      .update(normalizeLineEndings(UNDERWATER_ILLUMINATION_SHADER.fragmentShader))
      .update(normalizeLineEndings(UNDERWATER_BILATERAL_SHADER.fragmentShader))
      .update(normalizeLineEndings(UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER.fragmentShader))
      .update(JSON.stringify({
        sunDirection: optics.sunDirection.value.toArray(),
        sunSurfaceAnchor: optics.sunSurfaceAnchor.value.toArray(),
        sunRadiance: optics.sunRadiance.value.toArray(),
        absorption: optics.absorption.value.toArray()
      }))
      .digest('hex')

    expect(fingerprint).toBe('6a9409e5416fcc89568171e7b9ed5082d27affac2f500f318a85d992ddfea488')
  })

  it('captures the current scene depth before any color-buffer swap and keeps half-resolution RGB illumination', () => {
    const pass = new UnderwaterIlluminationPass(
      new THREE.PerspectiveCamera(54, 1, 0.1, 100),
      'high',
      createUnderwaterOpticsState()
    )
    const sceneDepth = new THREE.DepthTexture(8, 8)
    const renderer = {
      setRenderTarget: vi.fn(),
      clear: vi.fn(),
      render: vi.fn()
    } as unknown as THREE.WebGLRenderer

    pass.setSize(1001, 601)
    pass.render(
      renderer,
      {} as THREE.WebGLRenderTarget,
      { depthTexture: sceneDepth } as THREE.WebGLRenderTarget
    )

    expect(pass.needsSwap).toBe(false)
    expect(pass.sceneDepthTexture).toBe(sceneDepth)
    expect(pass.lowResolution.toArray()).toEqual([501, 301])
    expect(pass.raySteps).toBe(60)
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader).toContain('MAX_RAY_STEPS = 64')
    expect(UNDERWATER_ILLUMINATION_SHADER.fragmentShader)
      .toContain('gl_FragColor = vec4(volumetricLight, linearDepth)')
    pass.dispose()
  })

  it('filters illumination only and uses relative scene depth when upsampling', () => {
    expect(UNDERWATER_BILATERAL_SHADER.fragmentShader).toContain('centerSample.a')
    expect(UNDERWATER_BILATERAL_SHADER.fragmentShader).toContain('niraiLuminance')
    expect(UNDERWATER_BILATERAL_SHADER.fragmentShader).not.toContain('float luminance(')
    expect(UNDERWATER_BILATERAL_SHADER.fragmentShader).toContain('rangeWeight')
    expect(UNDERWATER_BILATERAL_SHADER.fragmentShader).toContain('spatialWeight')
    expect(UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER.fragmentShader)
      .toContain('relativeDepthDifference')
    expect(UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER.fragmentShader).toContain('depthWeight')
    expect(UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER.fragmentShader).toContain('spatialWeight')
    expect(UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER.fragmentShader).toContain('uGodraysResolution')
    expect(UNDERWATER_SHADER.fragmentShader).toContain('vec4(color, linearDepth)')
    expect(UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER.fragmentShader)
      .toContain('float fullResolutionDepth = source.a')
    expect(UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER.fragmentShader)
      .toContain('weightedIllumination / max(totalWeight, 0.00001)')
    expect(UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER.fragmentShader)
      .not.toContain('vec3 volumetricLight = texture2D(tGodrays, vUv).rgb')
  })

  it('orders depth capture before underwater color and depth-aware composite after it', () => {
    const renderer = { getPixelRatio: () => 1 } as unknown as THREE.WebGLRenderer
    const post = new UnderwaterPostProcessing(
      renderer,
      new THREE.Scene(),
      new THREE.PerspectiveCamera(),
      'medium',
      createUnderwaterOpticsState()
    )
    const illuminationIndex = captures.composerPasses.findIndex(
      (candidate) => candidate instanceof UnderwaterIlluminationPass
    )
    const compositeIndex = captures.composerPasses.findIndex(
      (candidate) => candidate instanceof UnderwaterDepthAwareCompositePass
    )

    expect(illuminationIndex).toBeGreaterThanOrEqual(0)
    expect(compositeIndex).toBeGreaterThan(illuminationIndex)
    expect(compositeIndex - illuminationIndex).toBe(2)
    post.dispose()
  })

  it('shares the unified sun and water optical uniforms with the final composite', () => {
    const renderer = { getPixelRatio: () => 1 } as unknown as THREE.WebGLRenderer
    const optics = createUnderwaterOpticsState()
    const post = new UnderwaterPostProcessing(
      renderer,
      new THREE.Scene(),
      new THREE.PerspectiveCamera(),
      'medium',
      optics
    )
    const pass = captures.shaderPasses.at(-1)
    const illuminationPass = captures.composerPasses.filter(
      (candidate) => candidate instanceof UnderwaterIlluminationPass
    ).at(-1) as UnderwaterIlluminationPass | undefined

    expect(illuminationPass?.sunSurfaceAnchor).toBe(optics.sunSurfaceAnchor.value)
    expect(illuminationPass?.sunRadiance).toBe(optics.sunRadiance.value)
    expect(pass?.uniforms.uSunRadiance.value).toBe(optics.sunRadiance.value)
    expect(pass?.uniforms.uAbsorption.value).toBe(optics.absorption.value)
    expect(pass?.uniforms.uScatteringColor.value).toBe(optics.scatteringColor.value)
    expect(pass?.uniforms.uScatteringStrength.value).toBe(optics.scatteringStrength.value)

    post.dispose()
  })
})
