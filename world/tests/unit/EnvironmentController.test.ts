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
    expect(waterSurface.geometry.parameters.widthSegments).toBeGreaterThanOrEqual(128)
    expect(waterSurface.material.vertexShader).toContain('sampleSurfaceWave')
    expect(waterSurface.material.fragmentShader).toContain('fresnel')
    expect(waterSurface.material.fragmentShader).toContain('sampleSurfaceSlope')
    expect(waterSurface.material.fragmentShader).toContain('sunGlint')
    expect(waterSurface.material.fragmentShader).toContain('refract(')
    expect(waterSurface.material.fragmentShader).toContain('1.333')
    expect(waterSurface.material.fragmentShader).toContain('totalInternalReflection')
    expect(waterSurface.material.fragmentShader).toContain('detailFade')
    expect(waterSurface.material.fragmentShader).toContain('horizonFade')
    expect(waterSurface.material.fragmentShader).not.toContain('sampleCausticField')
    expect(waterSurface.material.fragmentShader).not.toContain('uniform vec3 cameraPosition')
    expect(scene.getObjectByName('Environment:distanceHaze')).toBeUndefined()
    const overheadGlow = scene.getObjectByName('Environment:overheadGlow') as THREE.Mesh<
      THREE.SphereGeometry,
      THREE.ShaderMaterial
    >
    expect(overheadGlow.material.fragmentShader).toContain('uSunDirection')
    expect(overheadGlow.material.fragmentShader).not.toContain('softBeam')
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
    expect(caustics.material.uniforms.time.value).toBe(1)
    expect(lowParticles.material.uniforms.time.value).toBe(1)
    expect(waterSurface.material.uniforms.time.value).toBe(1)
    const lowAmbient = lowBubbles.getObjectByName('Environment:bubbles:ambient') as THREE.Points
    const highAmbient = highBubbles.getObjectByName('Environment:bubbles:ambient') as THREE.Points
    expect(highAmbient.geometry.getAttribute('position').count)
      .toBeGreaterThan(lowAmbient.geometry.getAttribute('position').count)
    expect(lowAmbient.geometry.getAttribute('speed')).toBeDefined()
    expect(lowAmbient.geometry.getAttribute('cluster')).toBeDefined()
    expect(Math.max(...(lowAmbient.geometry.getAttribute('size').array as Float32Array)))
      .toBeLessThanOrEqual(2)
    expect(lowShafts.children).toHaveLength(0)
    expect(highShafts.children).toHaveLength(lowShafts.children.length)
    expect(lowShafts.userData.renderMode).toBe('depth-aware-analytic-volume')

    low.dispose()
    high.dispose()
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
      THREE.SphereGeometry,
      THREE.ShaderMaterial
    >
    expect(overheadGlow.material.fragmentShader).not.toContain('softBeam')
    expect(overheadGlow.material.uniforms.uSunDirection.value)
      .toBe(environment.optics.sunDirection.value)
    expect(overheadGlow.material.uniforms.uSunRadiance.value)
      .toBe(environment.optics.sunRadiance.value)
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
    expect(waterSurface.material.vertexShader).toContain('sampleSurfaceWave')
    expect(waterSurface.material.uniforms.uSunSurfaceAnchor.value)
      .toBe(environment.optics.sunSurfaceAnchor.value)
    expect(caustics.material.fragmentShader).toContain('sampleSurfaceWave')
    expect(caustics.material.fragmentShader).toContain('uStageCenter')
    expect(caustics.material.fragmentShader).toContain('causticRadiance')
    expect(caustics.material.uniforms.uIntensity.value).toBeGreaterThan(0.8)
    expect(caustics.material.uniforms.uSunSurfaceAnchor.value)
      .toBe(environment.optics.sunSurfaceAnchor.value)

    const stageLight = lighting.children.find(
      (child) => child instanceof THREE.SpotLight
    ) as THREE.SpotLight
    expect(stageLight.position.distanceTo(environment.optics.sunSurfaceAnchor.value)).toBeLessThan(0.01)

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

  it('builds the seabed from the selected five-map PBR sand material', () => {
    const scene = new THREE.Scene()
    const { dependencies, loadedPaths } = createTextureDependencies()
    const environment = new EnvironmentController(scene, { quality: 'low' }, dependencies)
    const seabed = scene.getObjectByName('Environment:seabed') as THREE.Mesh<
      THREE.PlaneGeometry,
      THREE.MeshStandardMaterial
    >

    expect(seabed.material.map).toBeInstanceOf(THREE.Texture)
    expect(seabed.material.normalMap).toBeInstanceOf(THREE.Texture)
    expect(seabed.material.roughnessMap).toBeInstanceOf(THREE.Texture)
    expect(seabed.material.aoMap).toBeInstanceOf(THREE.Texture)
    expect(seabed.material.displacementMap).toBeInstanceOf(THREE.Texture)
    expect(seabed.material.displacementScale).toBeGreaterThan(0)
    expect(Math.abs(seabed.material.color.b - seabed.material.color.r)).toBeLessThan(0.12)
    expect(seabed.material.emissiveIntensity).toBe(0)
    expect(seabed.material.roughness).toBeLessThanOrEqual(0.8)
    expect(seabed.material.normalScale.x).toBeGreaterThanOrEqual(0.18)
    expect((scene.fog as THREE.FogExp2).density).toBeLessThanOrEqual(0.032)
    expect(seabed.geometry.getAttribute('position').count).toBeGreaterThan(25000)
    expect([
      seabed.material.map,
      seabed.material.normalMap,
      seabed.material.roughnessMap,
      seabed.material.aoMap,
      seabed.material.displacementMap
    ].every((texture) => texture?.flipY === false)).toBe(true)
    expect(loadedPaths).toEqual(
      expect.arrayContaining([
        expect.stringContaining('aerial_beach_01_diff_4k.jpg'),
        expect.stringContaining('aerial_beach_01_nor_gl_4k.png'),
        expect.stringContaining('aerial_beach_01_rough_4k.jpg'),
        expect.stringContaining('aerial_beach_01_ao_4k.jpg'),
        expect.stringContaining('aerial_beach_01_disp_4k.png')
      ])
    )

    environment.dispose()
  })
})
