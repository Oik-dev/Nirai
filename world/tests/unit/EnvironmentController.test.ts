import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import {
  EnvironmentController,
  type EnvironmentDependencies,
  type EnvironmentEffectName
} from '../../src/renderer/src/world/environment/EnvironmentController'

const EFFECTS: readonly EnvironmentEffectName[] = [
  'seabed',
  'fog',
  'lighting',
  'overheadGlow',
  'waterSurface',
  'caustics',
  'suspendedParticles',
  'luminousParticles',
  'bubbles',
  'lightShafts'
]

function createTextureDependencies(): {
  dependencies: EnvironmentDependencies
  loadedPaths: string[]
} {
  const loadedPaths: string[] = []
  const load = vi.fn((path: string) => {
    loadedPaths.push(path)
    return new THREE.Texture()
  })

  return {
    dependencies: {
      assetBaseUrl: 'https://nirai.test/assets/',
      textureLoader: { load }
    },
    loadedPaths
  }
}

describe('EnvironmentController', () => {
  it('creates every M0 underwater effect and lets each one be toggled', () => {
    const scene = new THREE.Scene()
    const { dependencies } = createTextureDependencies()
    const environment = new EnvironmentController(scene, { quality: 'low' }, dependencies)

    expect(scene.fog).toBeInstanceOf(THREE.FogExp2)
    const waterSurface = scene.getObjectByName('Environment:waterSurface') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.ShaderMaterial
    >
    expect(waterSurface).toBeDefined()
    expect(waterSurface).toBeInstanceOf(THREE.Mesh)
    expect(waterSurface.userData.renderMode).toBe('water-three-js-snell-window')
    expect(waterSurface.visible).toBe(true)
    expect(waterSurface.geometry.parameters.widthSegments).toBeGreaterThanOrEqual(300)
    expect(waterSurface.material.vertexShader).toContain('sampleOcean')
    expect(waterSurface.material.fragmentShader).toContain('detailNormal')
    expect(waterSurface.material.fragmentShader).toContain('refract(')
    expect(waterSurface.material.fragmentShader).toContain('1.333')
    expect(scene.getObjectByName('Environment:distanceHaze')).toBeUndefined()
    const overheadGlow = scene.getObjectByName('Environment:overheadGlow') as THREE.Mesh<
      THREE.SphereGeometry,
      THREE.ShaderMaterial
    >
    expect(overheadGlow.geometry).toBeInstanceOf(THREE.SphereGeometry)
    expect(overheadGlow.geometry.parameters.radius).toBe(72)
    expect(overheadGlow.material.side).toBe(THREE.BackSide)
    expect(overheadGlow.material.fragmentShader).toContain('uBackdropMap')
    expect(overheadGlow.material.fragmentShader).toContain('texture2D')
    expect(overheadGlow.material.fragmentShader).not.toContain('uViewportResolution')
    expect(overheadGlow.material.fragmentShader).not.toContain('uBackdropUvScale')
    expect(overheadGlow.material.fragmentShader).not.toContain('planePoint')
    expect(overheadGlow.material.fragmentShader).not.toContain('cameraPosition')
    expect(overheadGlow.material.fragmentShader).not.toContain('mapped.y = clamp')
    expect(overheadGlow.material.fragmentShader).toContain('lowerSandRemoval')
    expect(overheadGlow.material.fragmentShader).toContain('waterSourceUv')
    expect(overheadGlow.material.fragmentShader).toContain('uFogColor')
    expect(overheadGlow.material.uniforms.uFogColor.value).toEqual((scene.fog as THREE.FogExp2).color)
    expect(overheadGlow.material.uniforms.uBackdropPlaneZ).toBeUndefined()
    expect(overheadGlow.material.uniforms.uBackdropWorldSize).toBeUndefined()
    expect(overheadGlow.material.vertexShader).toContain('vBackdropUv')
    expect(overheadGlow.userData.renderMode).toBe('inner-skydome')
    expect(overheadGlow.material.fragmentShader).not.toContain('softBeam')
    expect(overheadGlow.material.fragmentShader).not.toContain('rippleNetwork')
    expect(overheadGlow.material.fragmentShader).not.toContain('surfaceCellEdge')
    expect(overheadGlow.material.fragmentShader).not.toContain('surfacePoint.x * 15.0')
    for (const effect of EFFECTS.filter((name) => name !== 'fog')) {
      expect(scene.getObjectByName(`Environment:${effect}`)).toBeDefined()
      environment.setEffectEnabled(effect, false)
      expect(scene.getObjectByName(`Environment:${effect}`)?.visible).toBe(false)
      expect(environment.isEffectEnabled(effect)).toBe(false)
    }

    environment.setEffectEnabled('fog', false)
    expect(scene.fog).toBeNull()
    environment.setEffectEnabled('fog', true)
    expect(scene.fog).toBeInstanceOf(THREE.FogExp2)

    environment.dispose()
    expect(scene.getObjectByName('Environment')).toBeUndefined()
    expect(scene.fog).toBeNull()
  })

  it('advances animated effects and uses quality to control particle counts', () => {
    const lowScene = new THREE.Scene()
    const highScene = new THREE.Scene()
    const { dependencies: lowDependencies } = createTextureDependencies()
    const { dependencies: highDependencies } = createTextureDependencies()
    const low = new EnvironmentController(lowScene, { quality: 'low' }, lowDependencies)
    const high = new EnvironmentController(highScene, { quality: 'high' }, highDependencies)
    const lowBubbles = lowScene.getObjectByName('Environment:bubbles') as THREE.Group
    const highBubbles = highScene.getObjectByName('Environment:bubbles') as THREE.Group
    const lowParticles = lowScene.getObjectByName('Environment:suspendedParticles') as THREE.Points<
      THREE.BufferGeometry,
      THREE.ShaderMaterial
    >
    const lowShafts = lowScene.getObjectByName('Environment:lightShafts') as THREE.Group
    const highShafts = highScene.getObjectByName('Environment:lightShafts') as THREE.Group
    const waterSurface = lowScene.getObjectByName('Environment:waterSurface') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.ShaderMaterial
    >
    const caustics = lowScene.getObjectByName('Environment:caustics') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.ShaderMaterial
    >
    low.update(1)

    expect(low.elapsedTime).toBe(1)
    expect(caustics.material.uniforms.time.value).toBe(4)
    expect(lowParticles.material.uniforms.time.value).toBe(1)
    expect(waterSurface.material.uniforms.uTime.value).toBe(7)
    const lowAmbient = lowBubbles.getObjectByName('Environment:bubbles:ambient') as THREE.Points
    const highAmbient = highBubbles.getObjectByName('Environment:bubbles:ambient') as THREE.Points
    expect(highAmbient.geometry.getAttribute('position').count)
      .toBeGreaterThan(lowAmbient.geometry.getAttribute('position').count)
    expect(lowAmbient.geometry.getAttribute('speed')).toBeDefined()
    expect(lowAmbient.geometry.getAttribute('cluster')).toBeDefined()
    expect(lowAmbient.geometry.getAttribute('streamOffset')).toBeDefined()
    expect(lowAmbient.geometry.getAttribute('shape')).toBeDefined()
    expect(lowAmbient.geometry.getAttribute('densityRank')).toBeDefined()
    expect(lowAmbient.userData.renderMode).toBe('single-draw-two-or-three-rising-stream-field')
    expect(lowAmbient.userData.anchorCount).toBe(2)
    expect(lowAmbient.userData.activeStreamCount).toBe(2)
    expect(highAmbient.userData.activeStreamCount).toBe(3)
    expect(lowAmbient.userData.candidateAnchorCount).toBe(10)
    expect(lowAmbient.userData.baseParticleCount).toBe(48)
    expect(lowAmbient.userData.maxParticleCount).toBe(240)
    expect(lowAmbient.userData.maximumVerticalDensity).toBe(5)
    expect(lowAmbient.userData.motion).toBe('narrow-source-widening-rise')
    expect(Math.max(...(lowAmbient.geometry.getAttribute('size').array as Float32Array)))
      .toBeLessThanOrEqual(9.6)
    expect(lowShafts.children).toHaveLength(0)
    expect(highShafts.children).toHaveLength(lowShafts.children.length)
    expect(lowShafts.userData.renderMode).toBe('depth-aware-analytic-volume')

    low.dispose()
    high.dispose()
  })

  it('keeps independent phase-continuous clocks for Master visual tuning', () => {
    const scene = new THREE.Scene()
    const { dependencies } = createTextureDependencies()
    const environment = new EnvironmentController(scene, { quality: 'medium' }, dependencies)
    const waterSurface = scene.getObjectByName('Environment:waterSurface') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.ShaderMaterial
    >
    const caustics = scene.getObjectByName('Environment:caustics') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.ShaderMaterial
    >
    const ambientBubbles = scene.getObjectByName(
      'Environment:bubbles:ambient'
    ) as THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>

    environment.setVisualTuning({
      waterSpeed: 0.5,
      waterCalmness: 0.9,
      lightShaftSpeed: 1,
      causticsSpeed: 1.5,
      bubbleRiseSpeed: 0.75,
      bubbleVerticalDensity: 1.4,
      bubbleHorizontalDensity: 1.8,
      horizonHaze: 1.5,
      waterPaleness: 1.5,
      sandWhiteness: 1.4,
      sandRelief: 2.4,
      waterSurfacePresence: 1.3,
      residentBrightness: 1.6
    })
    environment.update(2)

    expect(environment.elapsedTime).toBe(2)
    expect(waterSurface.material.uniforms.uTime.value).toBe(1)
    expect(caustics.material.uniforms.time.value).toBe(3)
    expect(ambientBubbles.material.uniforms.time.value).toBe(1.5)
    expect(ambientBubbles.material.uniforms.verticalDensity.value).toBe(1.4)
    expect(ambientBubbles.material.uniforms.horizontalDensity.value).toBe(1.8)
    expect(waterSurface.material.uniforms.uAmplitude.value).toBeLessThan(0.075)
    expect(waterSurface.material.uniforms.uDetailStrength.value).toBeLessThan(0.26)
    expect(waterSurface.material.uniforms.uVisibility.value).toBeCloseTo(1.3)
    expect((scene.fog as THREE.FogExp2).density).toBeCloseTo(0.027)
    const overheadGlow = scene.getObjectByName('Environment:overheadGlow') as THREE.Mesh<
      THREE.SphereGeometry,
      THREE.ShaderMaterial
    >
    expect(overheadGlow.material.uniforms.uHorizonHaze.value).toBeCloseTo(1.5)
    const seabed = scene.getObjectByName('Environment:seabed') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.MeshStandardMaterial
    >
    expect(seabed.material.userData.sandWhiteMixUniform.value).toBeCloseTo(0.812)
    expect(seabed.material.normalScale.x).toBeCloseTo(0.075 * 2.4)
    expect(seabed.material.bumpScale).toBeCloseTo(0.012 * 2.4)
    expect(environment.optics.scatteringStrength.value).toBeGreaterThan(0.4)
    const lighting = scene.getObjectByName('Environment:lighting') as THREE.Group
    const hemisphere = lighting.getObjectByName(
      'Environment:lighting:residentHemisphere'
    ) as THREE.HemisphereLight
    expect(hemisphere.intensity).toBeCloseTo(0.72 * 1.6)

    environment.setVisualTuning({
      waterSpeed: 2,
      waterCalmness: 0.7,
      lightShaftSpeed: 1,
      causticsSpeed: 0.5,
      bubbleRiseSpeed: 2,
      bubbleVerticalDensity: 0.8,
      bubbleHorizontalDensity: 0.6,
      horizonHaze: 1,
      waterPaleness: 1,
      sandWhiteness: 1,
      sandRelief: 1,
      waterSurfacePresence: 1,
      residentBrightness: 1
    })
    environment.update(1)

    expect(waterSurface.material.uniforms.uTime.value).toBe(3)
    expect(caustics.material.uniforms.time.value).toBe(3.5)
    expect(ambientBubbles.material.uniforms.time.value).toBe(3.5)
    expect(ambientBubbles.material.uniforms.verticalDensity.value).toBe(0.8)
    expect(ambientBubbles.material.uniforms.horizontalDensity.value).toBe(0.6)
    expect(waterSurface.material.uniforms.uAmplitude.value).toBeCloseTo(0.075)
    expect(waterSurface.material.uniforms.uDetailStrength.value).toBeCloseTo(0.26)
    expect(waterSurface.material.uniforms.uVisibility.value).toBeCloseTo(1)
    expect((scene.fog as THREE.FogExp2).density).toBeCloseTo(0.018)
    expect(seabed.material.userData.sandWhiteMixUniform.value).toBeCloseTo(0.58)
    expect(seabed.material.normalScale.x).toBeCloseTo(0.075)
    expect(seabed.material.bumpScale).toBeCloseTo(0.012)
    expect(environment.optics.scatteringStrength.value).toBeCloseTo(0.4)
    expect(hemisphere.intensity).toBeCloseTo(0.72)

    environment.dispose()
  })

  it('reserves light shafts for one sun-linked depth-aware volume without visible polygons', () => {
    const scene = new THREE.Scene()
    const { dependencies } = createTextureDependencies()
    const environment = new EnvironmentController(scene, { quality: 'medium' }, dependencies)
    const shafts = scene.getObjectByName('Environment:lightShafts') as THREE.Group
    const lighting = scene.getObjectByName('Environment:lighting') as THREE.Group

    expect(shafts.children).toHaveLength(0)
    expect(shafts.userData.renderMode).toBe('depth-aware-analytic-volume')
    expect(shafts.userData.densitySource).toBe('shared-surface-caustics')
    expect(shafts.userData.sunSurfaceAnchor).toBe(environment.optics.sunSurfaceAnchor.value)
    const overheadGlow = scene.getObjectByName('Environment:overheadGlow') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.ShaderMaterial
    >
    expect(overheadGlow.material.fragmentShader).not.toContain('softBeam')
    expect(overheadGlow.material.fragmentShader).toContain('uBackdropMap')
    expect(overheadGlow.material.uniforms.uBackdropMap.value).toBeInstanceOf(THREE.Texture)
    expect(overheadGlow.material.uniforms.lightShaftStrength.value).toBe(1)
    expect(lighting.children.some((child) => child instanceof THREE.HemisphereLight)).toBe(true)
    expect(lighting.children.some((child) => child instanceof THREE.DirectionalLight)).toBe(true)
    expect(lighting.children.some((child) => child instanceof THREE.SpotLight)).toBe(true)

    const waterSurface = scene.getObjectByName('Environment:waterSurface') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.ShaderMaterial
    >
    const caustics = scene.getObjectByName('Environment:caustics') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.ShaderMaterial
    >
    expect(waterSurface.userData.renderMode).toBe('water-three-js-snell-window')
    expect(waterSurface.material.fragmentShader).toContain('refract(')
    expect(caustics.material.fragmentShader).toContain('uCausticsMap')
    expect(caustics.material.fragmentShader).toContain('texture2D')
    expect(caustics.material.fragmentShader).toContain('uStageCenter')
    expect(caustics.material.fragmentShader).toContain('causticRadiance')
    expect(caustics.material.fragmentShader).toContain('primaryCaustic')
    expect(caustics.material.fragmentShader).toContain('detailCaustic')
    expect(caustics.material.fragmentShader).not.toContain('causticWarp')
    expect(caustics.material.uniforms.uIntensity.value).toBeGreaterThan(0.5)
    expect(caustics.material.uniforms.uSunSurfaceAnchor.value)
      .toBe(environment.optics.sunSurfaceAnchor.value)
    expect(caustics.geometry.parameters.widthSegments).toBeGreaterThan(1)
    expect(caustics.geometry.parameters.heightSegments).toBeGreaterThan(1)
    expect(caustics.userData.surfaceAttachment).toBe('seabed-following')
    expect(caustics.userData.surfaceOffset).toBeCloseTo(0.002, 6)
    const causticsHeights = caustics.geometry.getAttribute('position').array as Float32Array
    const causticsYValues = Array.from(
      { length: caustics.geometry.getAttribute('position').count },
      (_, index) => causticsHeights[index * 3 + 1]
    )
    expect(Math.max(...causticsYValues) - Math.min(...causticsYValues)).toBeGreaterThan(0.1)

    const stageLight = lighting.children.find(
      (child) => child instanceof THREE.SpotLight
    ) as THREE.SpotLight
    expect(stageLight.position.distanceTo(environment.optics.sunSurfaceAnchor.value)).toBeLessThan(0.01)
    expect(stageLight.intensity).toBeLessThan(8)
    expect(stageLight.penumbra).toBeGreaterThanOrEqual(0.8)

    environment.dispose()
  })

  it('renders round drifting particle sprites instead of square point primitives', () => {
    const scene = new THREE.Scene()
    const { dependencies } = createTextureDependencies()
    const environment = new EnvironmentController(scene, { quality: 'medium' }, dependencies)
    const suspended = scene.getObjectByName('Environment:suspendedParticles') as THREE.Points<
      THREE.BufferGeometry,
      THREE.ShaderMaterial
    >
    const luminous = scene.getObjectByName('Environment:luminousParticles') as THREE.Points<
      THREE.BufferGeometry,
      THREE.ShaderMaterial
    >

    expect(suspended.material).toBeInstanceOf(THREE.ShaderMaterial)
    expect(luminous.material).toBeInstanceOf(THREE.ShaderMaterial)
    expect(suspended.material.fragmentShader).toContain('gl_PointCoord')
    expect(suspended.material.fragmentShader).toContain('smoothstep')
    expect(luminous.material.blending).toBe(THREE.NormalBlending)
    expect(suspended.geometry.getAttribute('phase')).toBeDefined()
    expect(suspended.geometry.getAttribute('drift')).toBeDefined()
    expect(suspended.geometry.getAttribute('layer')).toBeDefined()
    expect(suspended.material.vertexShader).toContain('secondaryMotion')
    expect(luminous.geometry.getAttribute('lightAffinity')).toBeDefined()
    expect(luminous.material.uniforms.stageCenter.value)
      .toBe(environment.optics.stageCenter.value)
    const luminousSizes = luminous.geometry.getAttribute('size').array as Float32Array
    const suspendedSizes = suspended.geometry.getAttribute('size').array as Float32Array
    expect(Math.max(...luminousSizes)).toBeLessThanOrEqual(0.95)
    expect(Math.max(...suspendedSizes)).toBeLessThanOrEqual(1.65)
    expect(luminous.material.uniforms.glowStrength.value).toBe(0)
    expect(suspended.material.uniforms.glowStrength.value).toBe(0)
    expect(Math.max(...luminous.material.uniforms.particleColor.value.toArray())).toBeLessThan(0.78)
    expect(Math.max(...suspended.material.uniforms.particleColor.value.toArray())).toBeLessThan(0.78)

    environment.dispose()
  })

  it('emits a rare idle breath and more bubbles while the Resident moves, then lets them expire', () => {
    const scene = new THREE.Scene()
    const { dependencies } = createTextureDependencies()
    const environment = new EnvironmentController(scene, { quality: 'low' }, dependencies)
    const idleFrame = {
      resident: {
        position: new THREE.Vector3(0, 0, 0),
        height: 1.7,
        speed: 0,
        animation: 'stand'
      }
    }

    for (let index = 0; index < 31; index += 1) {
      environment.update(0.1, idleFrame)
    }
    const afterIdle = environment.getBubbleDiagnostics()
    expect(afterIdle.emittedTotal).toBeGreaterThanOrEqual(1)

    for (let index = 0; index < 10; index += 1) {
      environment.update(0.1, {
        resident: {
          ...idleFrame.resident,
          position: new THREE.Vector3(index * 0.12, 0, 0),
          speed: 1.2,
          animation: 'walk'
        }
      })
    }
    const afterMove = environment.getBubbleDiagnostics()
    expect(afterMove.emittedTotal - afterIdle.emittedTotal).toBeGreaterThanOrEqual(6)
    expect(afterMove.activeCount).toBeGreaterThan(0)

    const emittedAtStop = afterMove.emittedTotal
    for (let index = 0; index < 8; index += 1) {
      environment.update(0.1, idleFrame)
    }
    expect(environment.getBubbleDiagnostics().emittedTotal).toBe(emittedAtStop)
    expect(environment.getBubbleDiagnostics().activeCount).toBeGreaterThan(0)

    environment.dispose()
  })

  it('builds GroundSand005 as the opaque PBR seabed and lets distance fog hide its far field', () => {
    const scene = new THREE.Scene()
    const { dependencies, loadedPaths } = createTextureDependencies()
    const environment = new EnvironmentController(scene, { quality: 'low' }, dependencies)
    const seabed = scene.getObjectByName('Environment:seabed') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.MeshStandardMaterial
    >

    expect(seabed.material.map).toBeInstanceOf(THREE.Texture)
    expect(seabed.material.normalMap).toBeInstanceOf(THREE.Texture)
    expect(seabed.material.aoMap).toBeInstanceOf(THREE.Texture)
    expect(seabed.material.roughnessMap).toBeNull()
    expect(seabed.material.normalScale.x).toBeGreaterThanOrEqual(0.07)
    expect(seabed.material.normalScale.x).toBeLessThanOrEqual(0.085)
    expect(seabed.material.aoMapIntensity).toBeGreaterThanOrEqual(0.18)
    expect(seabed.material.aoMapIntensity).toBeLessThanOrEqual(0.24)
    expect(seabed.material.bumpMap).toBeInstanceOf(THREE.Texture)
    expect(seabed.material.bumpScale).toBeGreaterThanOrEqual(0.01)
    expect(seabed.material.bumpScale).toBeLessThanOrEqual(0.02)
    expect(seabed.material.displacementMap).toBeNull()
    expect(seabed.material.color.r).toBeGreaterThan(seabed.material.color.b)
    expect(seabed.material.color.b).toBeGreaterThan(0.80)
    expect(seabed.material.emissiveIntensity).toBeLessThan(0.05)
    expect(seabed.material.roughness).toBe(1)
    expect(seabed.material.metalness).toBe(0)
    expect(seabed.material.userData.roughnessSource).toBe('constant-matte-trial')
    expect(seabed.material.userData.colorGrade).toBe('pale-white-desaturated-trial')
    expect(seabed.material.userData.macroVariation).toBe('low-frequency-near-mid-distance-fade')
    expect(seabed.material.userData.distanceTransition).toBe('scene-fog')
    expect((scene.fog as THREE.FogExp2).density).toBeGreaterThanOrEqual(0.018)
    expect((scene.fog as THREE.FogExp2).density).toBeLessThanOrEqual(0.032)
    expect((scene.fog as THREE.FogExp2).color.g).toBeGreaterThan((scene.fog as THREE.FogExp2).color.r)
    expect(seabed.geometry.parameters.width).toBeGreaterThanOrEqual(200)
    expect(seabed.geometry.parameters.widthSegments).toBeGreaterThanOrEqual(300)
    expect(seabed.geometry.getAttribute('position').count).toBeGreaterThan(100000)
    const seabedPositions = seabed.geometry.getAttribute('position') as THREE.BufferAttribute
    const seabedYValues = Array.from(
      { length: seabedPositions.count },
      (_, index) => seabedPositions.getY(index)
    )
    expect(Math.min(...seabedYValues)).toBeLessThan(-6)
    expect(seabed.geometry.getAttribute('uv2')).toBeDefined()
    expect(seabed.material.aoMap?.channel).toBe(2)
    expect(seabed.material.map?.flipY).toBe(false)
    expect(seabed.material.map?.repeat.toArray()).toEqual([11.5, 11.5])
    expect(seabed.material.transparent).toBe(false)
    expect(seabed.material.opacity).toBe(1)
    expect(seabed.material.depthWrite).toBe(true)
    expect(seabed.material.blending).toBe(THREE.NormalBlending)
    expect(environment.getSeabedMaterialLayerEnabled('colorMap')).toBe(true)
    expect(environment.getSeabedMaterialLayerEnabled('ao')).toBe(true)
    expect(environment.getSeabedMaterialLayerEnabled('macroVariation')).toBe(true)

    environment.setSeabedMaterialLayerEnabled('colorMap', false)
    environment.setSeabedMaterialLayerEnabled('ao', false)
    environment.setSeabedMaterialLayerEnabled('macroVariation', false)
    expect(environment.getSeabedMaterialLayerEnabled('colorMap')).toBe(false)
    expect(environment.getSeabedMaterialLayerEnabled('ao')).toBe(false)
    expect(environment.getSeabedMaterialLayerEnabled('macroVariation')).toBe(false)
    expect(seabed.material.userData.sandColorMapStrengthUniform.value).toBe(0)
    expect(seabed.material.aoMapIntensity).toBe(0)
    expect(seabed.material.userData.sandMacroStrengthUniform.value).toBe(0)

    environment.setSeabedMaterialLayerEnabled('colorMap', true)
    environment.setSeabedMaterialLayerEnabled('ao', true)
    environment.setSeabedMaterialLayerEnabled('macroVariation', true)
    expect(seabed.material.userData.sandColorMapStrengthUniform.value).toBe(1)
    expect(seabed.material.aoMapIntensity).toBeCloseTo(0.20)
    expect(seabed.material.userData.sandMacroStrengthUniform.value).toBe(1)

    expect(environment.getSeabedSurfaceLayerEnabled('shadows')).toBe(true)
    expect(environment.getSeabedSurfaceLayerEnabled('geometry')).toBe(true)
    environment.setSeabedSurfaceLayerEnabled('shadows', false)
    expect(seabed.receiveShadow).toBe(false)
    expect(environment.getSeabedSurfaceLayerEnabled('shadows')).toBe(false)

    const deformedY = seabed.geometry.getAttribute('position').getY(0)
    environment.setSeabedSurfaceLayerEnabled('geometry', false)
    expect(environment.getSeabedSurfaceLayerEnabled('geometry')).toBe(false)
    expect(seabed.geometry.getAttribute('position').getY(0)).toBeCloseTo(0)
    environment.setSeabedSurfaceLayerEnabled('geometry', true)
    expect(environment.getSeabedSurfaceLayerEnabled('geometry')).toBe(true)
    expect(seabed.geometry.getAttribute('position').getY(0)).toBeCloseTo(deformedY)
    environment.setSeabedSurfaceLayerEnabled('shadows', true)
    expect(seabed.receiveShadow).toBe(true)

    expect(loadedPaths).toEqual([
      expect.stringContaining('GroundSand005_COL_4K.jpg'),
      expect.stringContaining('GroundSand005_NRM_4K.jpg'),
      expect.stringContaining('GroundSand005_AO_4K.jpg'),
      expect.stringContaining('GroundSand005_BUMP_4K.jpg'),
      expect.stringContaining('GroundSand005_GLOSS_4K.jpg'),
      expect.stringContaining('miyako-shallow-backdrop-v1.png'),
      expect.stringContaining('caustics-organic-v1.png')
    ])

    environment.dispose()
  })
})
