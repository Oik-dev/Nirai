import * as THREE from 'three'
import { RenderLoop } from './RenderLoop'
import { ResidentManager } from '../world/ResidentManager'
import type { AnimationName } from '../world/vrm/AnimationController'
import type { EmotionName } from '../world/vrm/ExpressionController'
import {
  EnvironmentController,
  type EnvironmentEffectName,
  type EnvironmentOptions,
  type EnvironmentQuality
} from '../world/environment/EnvironmentController'
import {
  createScreenSafeSwimBounds,
  M0_WORLD_CONFIG,
  type M0LocationName
} from './worldConfig'
import { UnderwaterPostProcessing } from '../world/environment/UnderwaterPostProcessing'
import {
  DEFAULT_VISUAL_TUNING,
  sanitizeVisualTuning,
  type VisualTuning
} from './VisualTuning'

const ENVIRONMENT_EFFECTS = [
  'seabed',
  'lighting',
  'overheadGlow',
  'waterSurface',
  'caustics',
  'suspendedParticles',
  'luminousParticles',
  'bubbles',
  'lightShafts'
] as const

export interface M0RuntimeDiagnostics {
  readonly environmentElapsedSec: number
  readonly effects: Readonly<Record<(typeof ENVIRONMENT_EFFECTS)[number] | 'fog', boolean>>
  readonly bubbleActivity: {
    readonly activeCount: number
    readonly emittedTotal: number
  }
  readonly resident: {
    readonly loaded: boolean
    readonly vrmVersion: string | null
    readonly currentAnimation: string | null
    readonly position: readonly [number, number, number]
    readonly lookAtCamera: boolean
    readonly expressions: Readonly<Record<'happy' | 'angry' | 'sad' | 'blink', number | null>>
  }
  readonly renderer: {
    readonly geometries: number
    readonly textures: number
    readonly calls: number
    readonly triangles: number
  } | null
  readonly visualTuning: VisualTuning
}

export type { M0LocationName } from './worldConfig'

export class SceneRuntime {
  readonly scene = new THREE.Scene()
  readonly camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100)
  readonly clock = new THREE.Clock()
  readonly residents = new ResidentManager(this.scene)
  readonly environment: EnvironmentController

  renderer: THREE.WebGLRenderer | null = null

  private canvas: HTMLCanvasElement | null = null
  private postProcessing: UnderwaterPostProcessing | null = null
  private readonly environmentQuality: EnvironmentQuality
  private residentHeight = 1.6
  private readonly previousResidentPosition = new THREE.Vector3()
  private hasPreviousResidentPosition = false
  private readonly renderLoop = new RenderLoop(() => this.update(this.clock.getDelta()))
  private visualTuning: VisualTuning = DEFAULT_VISUAL_TUNING

  constructor(environmentOptions: EnvironmentOptions = M0_WORLD_CONFIG.environment) {
    this.environmentQuality = environmentOptions.quality ?? 'medium'
    this.environment = new EnvironmentController(this.scene, environmentOptions)
  }

  start(canvas: HTMLCanvasElement): void {
    if (this.renderer) {
      return
    }

    this.canvas = canvas
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.renderer.setClearColor(0x062136)
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 0.95
    this.postProcessing = new UnderwaterPostProcessing(
      this.renderer,
      this.scene,
      this.camera,
      this.environmentQuality,
      this.environment.optics
    )
    this.postProcessing.setLightShaftSpeed(this.visualTuning.lightShaftSpeed)
    for (const name of ['waterSurface', 'lightShafts'] as const) {
      this.postProcessing.setEffectEnabled(name, this.environment.isEffectEnabled(name))
    }
    // Floor caustics are rendered once by EnvironmentController's animated,
    // texture-backed additive mesh. Reapplying the procedural full-screen
    // caustic pass muddies the pale sand and creates a dark elliptical band.
    this.postProcessing.setEffectEnabled('caustics', false)

    // Keep the empty-world framing close to the resident view so the first
    // rendered frame already presents the underwater space intentionally.
    this.camera.fov = 55
    this.camera.position.set(0, 1.08, 5.15)
    this.camera.lookAt(0, 1.90, -0.72)
    this.camera.updateProjectionMatrix()

    window.addEventListener('resize', this.handleResize)
    this.handleResize()
    this.clock.start()
    this.renderLoop.start()
  }

  resize(width: number, height: number): void {
    if (!this.renderer) {
      return
    }

    const safeWidth = Math.max(1, Math.floor(width))
    const safeHeight = Math.max(1, Math.floor(height))
    this.camera.aspect = safeWidth / safeHeight
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(safeWidth, safeHeight, false)
    this.environment.resize(safeWidth, safeHeight)
    this.postProcessing?.setSize(safeWidth, safeHeight)
    this.residents.get(M0_WORLD_CONFIG.residentName)?.constrainHorizontal(
      createScreenSafeSwimBounds(this.camera, this.residentHeight)
    )
  }

  async loadAvatar(relativePath: string): Promise<void> {
    if (this.residents.get(M0_WORLD_CONFIG.residentName)) {
      await this.residents.changeAvatar(M0_WORLD_CONFIG.residentName, relativePath)
    } else {
      await this.residents.spawn({ name: M0_WORLD_CONFIG.residentName, avatar: relativePath })
    }

    const resident = this.residents.get(M0_WORLD_CONFIG.residentName)
    if (resident?.vrm) {
      this.residentHeight = this.focusCameraOn(resident.root)
      this.previousResidentPosition.copy(resident.root.position)
      this.hasPreviousResidentPosition = true
      resident.face(this.camera)
      resident.update(0)
    }
  }

  unloadAvatar(): void {
    this.residents.remove(M0_WORLD_CONFIG.residentName)
    this.hasPreviousResidentPosition = false
  }

  playAnimation(name: AnimationName): boolean {
    return this.residents.get(M0_WORLD_CONFIG.residentName)?.playAnimation(name) ?? false
  }

  setEmotion(name: EmotionName): boolean {
    return this.residents.get(M0_WORLD_CONFIG.residentName)?.setEmotion(name) ?? false
  }

  triggerBlink(): boolean {
    return this.residents.get(M0_WORLD_CONFIG.residentName)?.triggerBlink() ?? false
  }

  setEnvironmentEffect(name: EnvironmentEffectName, enabled: boolean): void {
    this.environment.setEffectEnabled(name, enabled)
    this.postProcessing?.setEffectEnabled(name, name === 'caustics' ? false : enabled)
  }

  setVisualTuning(value: VisualTuning): VisualTuning {
    const next = sanitizeVisualTuning(value)
    this.visualTuning = next
    this.environment.setVisualTuning(next)
    this.postProcessing?.setLightShaftSpeed(next.lightShaftSpeed)
    return { ...next }
  }

  getVisualTuning(): VisualTuning {
    return { ...this.visualTuning }
  }

  moveResidentTo(location: M0LocationName, onArrive?: () => void): boolean {
    const resident = this.residents.get(M0_WORLD_CONFIG.residentName)
    if (!resident) {
      return false
    }

    return resident.swimNear(
      new THREE.Vector3(...M0_WORLD_CONFIG.locations[location]),
      new THREE.Vector3(...M0_WORLD_CONFIG.swim.radius),
      createScreenSafeSwimBounds(this.camera, this.residentHeight),
      onArrive
    )
  }

  getM0Diagnostics(): M0RuntimeDiagnostics {
    const resident = this.residents.get(M0_WORLD_CONFIG.residentName)
    const expressionManager = resident?.vrm?.expressionManager
    const effects = Object.fromEntries(
      ENVIRONMENT_EFFECTS.map((name) => [
        name,
        this.environment.group.getObjectByName(`Environment:${name}`)?.visible ?? false
      ])
    ) as Record<(typeof ENVIRONMENT_EFFECTS)[number], boolean>

    return {
      environmentElapsedSec: this.environment.elapsedTime,
      effects: {
        ...effects,
        fog: this.scene.fog !== null
      },
      bubbleActivity: this.environment.getBubbleDiagnostics(),
      resident: {
        loaded: resident?.vrm !== null && resident?.vrm !== undefined,
        vrmVersion: resident?.vrm?.meta.metaVersion ?? null,
        currentAnimation: resident?.animation?.getCurrentName() ?? null,
        position: resident
          ? [resident.root.position.x, resident.root.position.y, resident.root.position.z]
          : [0, 0, 0],
        lookAtCamera: resident?.vrm?.lookAt?.target === this.camera,
        expressions: {
          happy: expressionManager?.getValue('happy') ?? null,
          angry: expressionManager?.getValue('angry') ?? null,
          sad: expressionManager?.getValue('sad') ?? null,
          blink: expressionManager?.getValue('blink') ?? null
        }
      },
      renderer: this.renderer
        ? {
            geometries: this.renderer.info.memory.geometries,
            textures: this.renderer.info.memory.textures,
            calls: this.renderer.info.render.calls,
            triangles: this.renderer.info.render.triangles
          }
        : null,
      visualTuning: this.getVisualTuning()
    }
  }

  update(delta: number): void {
    this.residents.update(delta)
    const resident = this.residents.get(M0_WORLD_CONFIG.residentName)

    if (resident?.vrm) {
      const position = resident.root.position.clone()
      const speed = this.hasPreviousResidentPosition && delta > 0
        ? position.distanceTo(this.previousResidentPosition) / delta
        : 0
      this.environment.update(delta, {
        resident: {
          position,
          height: this.residentHeight,
          speed,
          animation: resident.animation?.getCurrentName() ?? null
        }
      })
      this.previousResidentPosition.copy(position)
      this.hasPreviousResidentPosition = true
    } else {
      this.environment.update(delta)
      this.hasPreviousResidentPosition = false
    }
    if (this.postProcessing) {
      this.postProcessing.render(delta)
    } else {
      this.renderer?.render(this.scene, this.camera)
    }
  }

  dispose(): void {
    window.removeEventListener('resize', this.handleResize)
    this.renderLoop.stop()
    this.clock.stop()
    this.residents.dispose()
    this.environment.dispose()

    this.postProcessing?.dispose()
    this.postProcessing = null
    this.renderer?.dispose()
    this.renderer = null
    this.canvas = null
  }

  private readonly handleResize = (): void => {
    if (!this.canvas) {
      return
    }

    this.resize(this.canvas.clientWidth, this.canvas.clientHeight)
  }

  private focusCameraOn(residentRoot: THREE.Object3D): number {
    const bounds = new THREE.Box3().setFromObject(residentRoot)
    const size = bounds.getSize(new THREE.Vector3())
    const avatarHeight = Math.max(size.y, 1)
    this.camera.fov = 56
    this.camera.position.set(0, avatarHeight * 0.68, avatarHeight * 3.1)
    this.camera.lookAt(0, avatarHeight * 1.08, -0.72)
    this.camera.updateProjectionMatrix()
    return avatarHeight
  }
}
