import * as THREE from 'three'
import { RenderLoop } from './RenderLoop'
import { ResidentManager } from '../world/ResidentManager'
import type {
  MotionPoseOption,
  PoseAdjustment,
  PoseAdjustScope,
  ResidentInstance
} from '../world/ResidentInstance'
import type { AnimationClipName, AnimationName } from '../world/vrm/AnimationController'
import type { EmotionName } from '../world/vrm/ExpressionController'
import {
  EnvironmentController,
  type EnvironmentEffectName,
  type EnvironmentOptions,
  type EnvironmentQuality,
  type SeabedMaterialDebugLayer,
  type SeabedSurfaceDebugLayer
} from '../world/environment/EnvironmentController'
import {
  createDirectionalMoveTarget,
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
import {
  DEFAULT_MOTION_TUNING,
  sanitizeMotionTuning,
  type MotionTuning
} from './MotionTuning'
import {
  resolveBoomCameraPosition,
  resolveFocusAim,
  resolveFocusDistance,
  resolvePerspectiveFitDistance
} from './CameraFraming'

// Camera and pose-drag constants are the approved World/Focus feel.
// Do not retune, rename-for-cleanup, or share them into a generic rig helper.
const CAMERA_RIG_DAMPING = 8
const CAMERA_ZOOM_STEP = 0.1
const CAMERA_FLOOR_CLEARANCE = 0.36
const CAMERA_FOCUS_WIDE_PADDING = 0.28
const CAMERA_FOCUS_CLOSE_MIN_DISTANCE = 0.30
const CAMERA_FOCUS_CLOSE_DISTANCE_RATIO = 0.20
const CAMERA_FOCUS_HEAD_DROP_RATIO = 0.01
const CAMERA_WORLD_NEAR_DISTANCE_RATIO = 0.52
const CAMERA_FOCUS_BOOM_DIRECTION = new THREE.Vector3(0, 0.30, 1).normalize()
const GROUP_VIEW_PADDING = 0.22
const POSE_ADJUST_DEGREES_PER_PIXEL = 0.35
const POSE_ADJUST_FINE_DEGREES_PER_PIXEL = 0.08

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
export type {
  MotionPoseOption,
  PoseAdjustment,
  PoseAdjustScope
} from '../world/ResidentInstance'
export type { AnimationClipName } from '../world/vrm/AnimationController'

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
  private readonly cameraAim = new THREE.Vector3()
  private readonly cameraTargetAim = new THREE.Vector3()
  private readonly cameraTargetPosition = new THREE.Vector3()
  private readonly worldCameraAim = new THREE.Vector3()
  private readonly worldCameraFarPosition = new THREE.Vector3()
  private readonly worldCameraBoomDirection = new THREE.Vector3(0, 0.1, 1).normalize()
  private readonly focusedHeadPosition = new THREE.Vector3()
  private focusedResidentName: string | null = null
  private lastResidentRosterSize = -1
  private readonly selectionRaycaster = new THREE.Raycaster()
  private readonly selectionPointer = new THREE.Vector2()
  private readonly focusedBounds = new THREE.Box3()
  private readonly focusedBoundsCenter = new THREE.Vector3()
  private readonly focusedBoundsSize = new THREE.Vector3()
  private readonly groupBounds = new THREE.Box3()
  private readonly groupResidentBounds = new THREE.Box3()
  private readonly groupBoundsCenter = new THREE.Vector3()
  private readonly groupBoundsSize = new THREE.Vector3()
  private worldCameraFarDistance = 0
  private worldCameraNearDistance = 0
  private worldZoom = 0
  private focusZoom = 0
  private cameraRigReady = false
  private readonly previousResidentPosition = new THREE.Vector3()
  private hasPreviousResidentPosition = false
  private readonly poseAdjustRaycaster = new THREE.Raycaster()
  private readonly poseAdjustPointer = new THREE.Vector2()
  private poseAdjustClip: AnimationClipName | null = null
  private poseAdjustScope: PoseAdjustScope = 'root'
  private poseAdjustPointerId: number | null = null
  private poseAdjustLastX = 0
  private poseAdjustLastY = 0
  private poseAdjustListener: ((value: PoseAdjustment) => void) | null = null
  private readonly renderLoop = new RenderLoop(() => this.update(this.clock.getDelta()))
  private visualTuning: VisualTuning = DEFAULT_VISUAL_TUNING
  private motionTuning: MotionTuning = DEFAULT_MOTION_TUNING

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
    this.postProcessing.setWaterSurfaceStrength(
      this.environment.isEffectEnabled('waterSurface')
        ? this.visualTuning.waterSurfacePresence
        : 0
    )
    // Floor caustics are rendered once by EnvironmentController's animated,
    // texture-backed additive mesh. Reapplying the procedural full-screen
    // caustic pass muddies the pale sand and creates a dark elliptical band.
    this.postProcessing.setEffectEnabled('caustics', false)

    // Keep the empty-world framing close to the resident view so the first
    // rendered frame already presents the underwater space intentionally.
    this.camera.fov = 55
    this.camera.position.set(0, 1.55, 5.15)
    this.cameraAim.set(0, 1.20, -0.72)
    this.camera.lookAt(this.cameraAim)
    this.camera.updateProjectionMatrix()

    window.addEventListener('resize', this.handleResize)
    canvas.addEventListener('wheel', this.handleWheel, { passive: false })
    canvas.addEventListener('pointerdown', this.handlePosePointerDown)
    canvas.addEventListener('pointermove', this.handlePosePointerMove)
    canvas.addEventListener('pointerup', this.handlePosePointerUp)
    canvas.addEventListener('pointercancel', this.handlePosePointerUp)
    canvas.addEventListener('click', this.handleWorldClick)
    this.handleResize()
    this.camera.updateMatrixWorld(true)
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
    this.updateResidentPresentationBounds()
  }

  async loadAvatar(relativePath: string): Promise<void> {
    if (this.residents.get(M0_WORLD_CONFIG.residentName)) {
      await this.residents.changeAvatar(M0_WORLD_CONFIG.residentName, relativePath)
    } else {
      await this.residents.spawn({ name: M0_WORLD_CONFIG.residentName, avatar: relativePath })
    }

    const resident = this.residents.get(M0_WORLD_CONFIG.residentName)
    if (resident?.vrm) {
      resident.setMotionTuning(this.motionTuning)
      this.residentHeight = this.configureCameraRigs(resident.root)
      this.reconcileCameraFocusAfterRosterChange(true)
      this.updateResidentPresentationBounds()
      this.previousResidentPosition.copy(resident.root.position)
      this.hasPreviousResidentPosition = true
      resident.face(this.camera)
      resident.update(0)
    }
  }

  unloadAvatar(): void {
    this.residents.remove(M0_WORLD_CONFIG.residentName)
    this.hasPreviousResidentPosition = false
    this.cameraRigReady = false
    this.reconcileCameraFocusAfterRosterChange(true)
  }

  playAnimation(name: AnimationName): boolean {
    return this.residents.get(M0_WORLD_CONFIG.residentName)?.playAnimation(name) ?? false
  }

  setEmotion(name: EmotionName): boolean {
    return this.residents.get(M0_WORLD_CONFIG.residentName)?.setEmotion(name) ?? false
  }

  getAvailableEmotions(): readonly EmotionName[] {
    return this.residents.get(M0_WORLD_CONFIG.residentName)?.getAvailableEmotions() ?? ['neutral']
  }

  triggerBlink(): boolean {
    return this.residents.get(M0_WORLD_CONFIG.residentName)?.triggerBlink() ?? false
  }

  getFocusedResidentName(): string | null {
    return this.focusedResidentName
  }

  focusResident(residentName: string | null): boolean {
    if (residentName !== null && !this.residents.get(residentName)) {
      return false
    }
    if (residentName !== null && residentName !== this.focusedResidentName) {
      this.focusZoom = 0
    }
    this.focusedResidentName = residentName
    return true
  }

  getPoseAdjustMotionOptions(): readonly MotionPoseOption[] {
    return this.residents.get(M0_WORLD_CONFIG.residentName)?.getPoseAdjustMotionOptions() ?? []
  }

  beginPoseAdjustment(
    clip: AnimationClipName,
    scope: PoseAdjustScope,
    onChange: (value: PoseAdjustment) => void
  ): PoseAdjustment | null {
    const resident = this.residents.get(M0_WORLD_CONFIG.residentName)
    if (!resident) {
      return null
    }
    if (this.poseAdjustClip !== clip && !resident.playPoseAdjustmentMotion(clip)) {
      return null
    }

    this.poseAdjustClip = clip
    this.poseAdjustScope = scope
    this.poseAdjustListener = onChange
    this.canvas?.classList.add('pose-adjust-active')
    const value = resident.getPoseAdjustment(clip, scope)
    onChange(value)
    return value
  }

  stopPoseAdjustment(): void {
    if (this.poseAdjustPointerId !== null && this.canvas?.hasPointerCapture(this.poseAdjustPointerId)) {
      this.canvas.releasePointerCapture(this.poseAdjustPointerId)
    }
    this.poseAdjustPointerId = null
    this.poseAdjustClip = null
    this.poseAdjustListener = null
    this.canvas?.classList.remove('pose-adjust-active', 'pose-adjust-dragging')
  }

  getPoseAdjustment(
    clip: AnimationClipName,
    scope: PoseAdjustScope
  ): PoseAdjustment | null {
    return this.residents.get(M0_WORLD_CONFIG.residentName)?.getPoseAdjustment(clip, scope) ?? null
  }

  setEnvironmentEffect(name: EnvironmentEffectName, enabled: boolean): void {
    this.environment.setEffectEnabled(name, enabled)
    this.postProcessing?.setEffectEnabled(name, name === 'caustics' ? false : enabled)
    if (name === 'waterSurface') {
      this.postProcessing?.setWaterSurfaceStrength(
        enabled ? this.visualTuning.waterSurfacePresence : 0
      )
    }
  }

  setSeabedMaterialLayerEnabled(name: SeabedMaterialDebugLayer, enabled: boolean): void {
    this.environment.setSeabedMaterialLayerEnabled(name, enabled)
  }

  getSeabedMaterialLayerEnabled(name: SeabedMaterialDebugLayer): boolean {
    return this.environment.getSeabedMaterialLayerEnabled(name)
  }

  setSeabedSurfaceLayerEnabled(name: SeabedSurfaceDebugLayer, enabled: boolean): void {
    this.environment.setSeabedSurfaceLayerEnabled(name, enabled)
  }

  getSeabedSurfaceLayerEnabled(name: SeabedSurfaceDebugLayer): boolean {
    return this.environment.getSeabedSurfaceLayerEnabled(name)
  }

  setVisualTuning(value: VisualTuning): VisualTuning {
    const next = sanitizeVisualTuning(value)
    this.visualTuning = next
    this.environment.setVisualTuning(next)
    this.postProcessing?.setLightShaftSpeed(next.lightShaftSpeed)
    this.postProcessing?.setWaterSurfaceStrength(
      this.environment.isEffectEnabled('waterSurface') ? next.waterSurfacePresence : 0
    )
    return { ...next }
  }

  getVisualTuning(): VisualTuning {
    return { ...this.visualTuning }
  }

  setMotionTuning(value: MotionTuning): MotionTuning {
    const next = sanitizeMotionTuning(value)
    this.motionTuning = next
    for (const [, resident] of this.residents.getEntries()) {
      resident.setMotionTuning(next)
    }
    return { ...next }
  }

  getMotionTuning(): MotionTuning {
    return { ...this.motionTuning }
  }

  moveResidentTo(location: M0LocationName, onArrive?: () => void): boolean {
    const resident = this.residents.get(M0_WORLD_CONFIG.residentName)
    if (!resident) {
      return false
    }

    const bounds = this.getScreenSafeMovementBounds()
    const target = createDirectionalMoveTarget(resident.root.position, location, bounds)
    return resident.moveTo(target, onArrive, bounds)
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
    this.reconcileCameraFocusAfterRosterChange()
    this.residents.update(delta)
    this.updateCameraRig(delta)
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
    this.canvas?.removeEventListener('wheel', this.handleWheel)
    this.canvas?.removeEventListener('pointerdown', this.handlePosePointerDown)
    this.canvas?.removeEventListener('pointermove', this.handlePosePointerMove)
    this.canvas?.removeEventListener('pointerup', this.handlePosePointerUp)
    this.canvas?.removeEventListener('pointercancel', this.handlePosePointerUp)
    this.canvas?.removeEventListener('click', this.handleWorldClick)
    this.stopPoseAdjustment()
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

  private readonly handleWorldClick = (event: MouseEvent): void => {
    if (!this.canvas || this.poseAdjustClip) {
      return
    }

    const rect = this.canvas.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) {
      return
    }

    this.selectionPointer.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    )
    this.selectionRaycaster.setFromCamera(this.selectionPointer, this.camera)

    let selectedName: string | null = null
    let selectedDistance = Number.POSITIVE_INFINITY
    for (const [name, resident] of this.residents.getEntries()) {
      if (!resident.vrm) {
        continue
      }
      const hit = this.selectionRaycaster.intersectObject(resident.root, true)[0]
      if (hit && hit.distance < selectedDistance) {
        selectedDistance = hit.distance
        selectedName = name
      }
    }

    this.focusResident(selectedName)
  }

  private readonly handlePosePointerDown = (event: PointerEvent): void => {
    if (!this.canvas || !this.poseAdjustClip || event.button !== 0) {
      return
    }

    const resident = this.residents.get(M0_WORLD_CONFIG.residentName)
    if (!resident?.vrm) {
      return
    }

    const rect = this.canvas.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) {
      return
    }

    this.poseAdjustPointer.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    )
    this.poseAdjustRaycaster.setFromCamera(this.poseAdjustPointer, this.camera)
    if (this.poseAdjustRaycaster.intersectObject(resident.root, true).length === 0) {
      return
    }

    event.preventDefault()
    this.poseAdjustPointerId = event.pointerId
    this.poseAdjustLastX = event.clientX
    this.poseAdjustLastY = event.clientY
    this.canvas.setPointerCapture(event.pointerId)
    this.canvas.classList.add('pose-adjust-dragging')
  }

  private readonly handlePosePointerMove = (event: PointerEvent): void => {
    if (
      !this.poseAdjustClip
      || this.poseAdjustPointerId !== event.pointerId
    ) {
      return
    }

    const resident = this.residents.get(M0_WORLD_CONFIG.residentName)
    if (!resident) {
      return
    }

    event.preventDefault()
    const sensitivity = event.shiftKey
      ? POSE_ADJUST_FINE_DEGREES_PER_PIXEL
      : POSE_ADJUST_DEGREES_PER_PIXEL
    const deltaX = event.clientX - this.poseAdjustLastX
    const deltaY = event.clientY - this.poseAdjustLastY
    this.poseAdjustLastX = event.clientX
    this.poseAdjustLastY = event.clientY

    const current = resident.getPoseAdjustment(this.poseAdjustClip, this.poseAdjustScope)
    const next = resident.setPoseAdjustment(this.poseAdjustClip, this.poseAdjustScope, {
      pitchDeg: current.pitchDeg + deltaY * sensitivity,
      yawDeg: current.yawDeg + deltaX * sensitivity
    })
    this.poseAdjustListener?.(next)
  }

  private readonly handlePosePointerUp = (event: PointerEvent): void => {
    if (this.poseAdjustPointerId !== event.pointerId) {
      return
    }

    if (this.canvas?.hasPointerCapture(event.pointerId)) {
      this.canvas.releasePointerCapture(event.pointerId)
    }
    this.poseAdjustPointerId = null
    this.canvas?.classList.remove('pose-adjust-dragging')
  }

  private readonly handleResize = (): void => {
    if (!this.canvas) {
      return
    }

    this.resize(this.canvas.clientWidth, this.canvas.clientHeight)
  }

  private readonly handleWheel = (event: WheelEvent): void => {
    if (!this.cameraRigReady || event.deltaY === 0) {
      return
    }

    event.preventDefault()
    const delta = Math.sign(event.deltaY) * CAMERA_ZOOM_STEP
    if (this.focusedResidentName) {
      this.focusZoom = THREE.MathUtils.clamp(this.focusZoom - delta, 0, 1)
    } else {
      this.worldZoom = THREE.MathUtils.clamp(this.worldZoom - delta, 0, 1)
    }
  }

  private updateCameraRig(delta: number): void {
    if (!this.cameraRigReady) {
      return
    }

    const focusedResident = this.focusedResidentName
      ? this.residents.get(this.focusedResidentName)
      : undefined
    if (focusedResident && this.updateFocusedFramingBounds(focusedResident)) {
      this.resolveFocusedCameraRig(focusedResident)
    } else {
      this.resolveWorldCameraRig()
    }

    const safeDelta = Math.max(0, delta)
    this.camera.position.x = THREE.MathUtils.damp(
      this.camera.position.x,
      this.cameraTargetPosition.x,
      CAMERA_RIG_DAMPING,
      safeDelta
    )
    this.camera.position.y = THREE.MathUtils.damp(
      this.camera.position.y,
      this.cameraTargetPosition.y,
      CAMERA_RIG_DAMPING,
      safeDelta
    )
    this.camera.position.z = THREE.MathUtils.damp(
      this.camera.position.z,
      this.cameraTargetPosition.z,
      CAMERA_RIG_DAMPING,
      safeDelta
    )
    this.cameraAim.x = THREE.MathUtils.damp(
      this.cameraAim.x,
      this.cameraTargetAim.x,
      CAMERA_RIG_DAMPING,
      safeDelta
    )
    this.cameraAim.y = THREE.MathUtils.damp(
      this.cameraAim.y,
      this.cameraTargetAim.y,
      CAMERA_RIG_DAMPING,
      safeDelta
    )
    this.cameraAim.z = THREE.MathUtils.damp(
      this.cameraAim.z,
      this.cameraTargetAim.z,
      CAMERA_RIG_DAMPING,
      safeDelta
    )
    this.camera.lookAt(this.cameraAim)
  }

  private resolveFocusedCameraRig(resident: ResidentInstance): void {
    const wideDistance = Math.max(
      this.residentHeight * 1.2,
      resolvePerspectiveFitDistance(
        this.focusedBoundsSize,
        this.camera.fov,
        this.camera.aspect,
        CAMERA_FOCUS_WIDE_PADDING
      )
    )
    const closeDistance = Math.min(
      wideDistance * 0.72,
      Math.max(
        CAMERA_FOCUS_CLOSE_MIN_DISTANCE,
        this.residentHeight * CAMERA_FOCUS_CLOSE_DISTANCE_RATIO
      )
    )
    const head = resident.getCameraHeadPosition(this.focusedHeadPosition)
    const closeAim = head
      ? head.clone().add(new THREE.Vector3(0, -this.residentHeight * CAMERA_FOCUS_HEAD_DROP_RATIO, 0))
      : this.focusedBoundsCenter.clone()
    this.cameraTargetAim.copy(resolveFocusAim(
      this.focusedBoundsCenter,
      closeAim,
      this.focusZoom
    ))
    const distance = resolveFocusDistance(wideDistance, closeDistance, this.focusZoom)
    this.cameraTargetPosition.copy(resolveBoomCameraPosition(
      this.cameraTargetAim,
      CAMERA_FOCUS_BOOM_DIRECTION,
      distance,
      this.environment.getSeabedWorldY() + CAMERA_FLOOR_CLEARANCE
    ))
  }

  private resolveWorldCameraRig(): void {
    const safeDistance = Math.max(
      THREE.MathUtils.lerp(
        this.worldCameraFarDistance,
        this.worldCameraNearDistance,
        this.worldZoom
      ),
      this.getGroupSafeCameraDistance()
    )
    this.cameraTargetAim.copy(this.worldCameraAim)
    this.cameraTargetPosition.copy(this.worldCameraAim).addScaledVector(
      this.worldCameraBoomDirection,
      safeDistance
    )
  }

  private configureCameraRigs(residentRoot: THREE.Object3D): number {
    const bounds = new THREE.Box3().setFromObject(residentRoot)
    const size = bounds.getSize(new THREE.Vector3())
    const avatarHeight = Math.max(size.y, 1)
    this.camera.fov = 56
    this.worldCameraAim.set(0, avatarHeight * 0.74, -0.72)
    this.worldCameraFarPosition.set(0, avatarHeight * 1.02, avatarHeight * 3.35)
    this.worldCameraBoomDirection
      .copy(this.worldCameraFarPosition)
      .sub(this.worldCameraAim)
      .normalize()
    this.worldCameraFarDistance = this.worldCameraFarPosition.distanceTo(this.worldCameraAim)
    this.worldCameraNearDistance = Math.max(
      avatarHeight * 1.7,
      this.worldCameraFarDistance * CAMERA_WORLD_NEAR_DISTANCE_RATIO
    )
    this.worldZoom = 0
    this.focusZoom = 0
    this.camera.position.copy(this.worldCameraFarPosition)
    this.cameraAim.copy(this.worldCameraAim)
    this.cameraTargetPosition.copy(this.camera.position)
    this.cameraTargetAim.copy(this.cameraAim)
    this.camera.lookAt(this.cameraAim)
    this.camera.updateProjectionMatrix()
    this.camera.updateMatrixWorld(true)
    this.cameraRigReady = true
    return avatarHeight
  }

  private reconcileCameraFocusAfterRosterChange(force = false): void {
    const size = this.residents.size
    const previousSize = this.lastResidentRosterSize
    if (!force && size === previousSize) {
      return
    }
    this.lastResidentRosterSize = size

    if (size === 0) {
      this.focusedResidentName = null
    } else if (
      previousSize !== size
      || (this.focusedResidentName !== null && !this.residents.get(this.focusedResidentName))
    ) {
      this.focusedResidentName = null
    }
    this.updateResidentPresentationBounds()
  }

  private updateFocusedFramingBounds(resident: ResidentInstance): boolean {
    const boneBounds = resident.getCameraFramingBounds(this.focusedBounds)
    if (!boneBounds) {
      this.focusedBounds.setFromObject(resident.root)
    }
    if (this.focusedBounds.isEmpty()) {
      return false
    }

    this.focusedBounds.getCenter(this.focusedBoundsCenter)
    this.focusedBounds.getSize(this.focusedBoundsSize)
    return true
  }

  private getGroupSafeCameraDistance(): number {
    const entries = this.residents.getEntries()
    if (entries.length === 0) {
      return this.worldCameraNearDistance
    }

    this.groupBounds.makeEmpty()
    for (const [, resident] of entries) {
      const bounds = resident.getCameraFramingBounds(this.groupResidentBounds)
      if (bounds) {
        this.groupBounds.union(bounds)
      } else {
        this.groupResidentBounds.setFromObject(resident.root)
        this.groupBounds.union(this.groupResidentBounds)
      }
    }
    if (this.groupBounds.isEmpty()) {
      return this.worldCameraNearDistance
    }

    this.groupBounds.getCenter(this.groupBoundsCenter)
    this.groupBounds.getSize(this.groupBoundsSize)
    const verticalTan = Math.tan(THREE.MathUtils.degToRad(this.camera.fov * 0.5))
    const horizontalHalfSpan = Math.abs(this.groupBoundsCenter.x - this.worldCameraAim.x)
      + this.groupBoundsSize.x * 0.5
      + GROUP_VIEW_PADDING
    const verticalHalfSpan = Math.abs(this.groupBoundsCenter.y - this.worldCameraAim.y)
      + this.groupBoundsSize.y * 0.5
      + GROUP_VIEW_PADDING
    const horizontalDistance = horizontalHalfSpan / Math.max(0.05, verticalTan * this.camera.aspect)
    const verticalDistance = verticalHalfSpan / Math.max(0.05, verticalTan)
    const depthAllowance = this.groupBoundsSize.z * 0.5
    return Math.max(
      this.worldCameraNearDistance,
      horizontalDistance + depthAllowance,
      verticalDistance + depthAllowance
    )
  }

  private updateResidentPresentationBounds(): void {
    const entries = this.residents.getEntries()
    if (entries.length === 0) {
      return
    }

    // Movement range is a presentation constraint derived from the current
    // camera, not a fixed world-width rule. Every resident stays inside the
    // visible frame regardless of roster size or Focus state.
    const screenSafeBounds = this.getScreenSafeMovementBounds()
    for (const [, resident] of entries) {
      resident.constrainHorizontal(screenSafeBounds)
    }
  }

  private getScreenSafeMovementBounds() {
    // Use the camera's current zoomed framing. Portrait, landscape, and World
    // zoom therefore all produce their own safe movement width, so repeated
    // directional Moves cannot walk a resident out of the visible frame.
    return createScreenSafeSwimBounds(this.camera, this.residentHeight)
  }
}
