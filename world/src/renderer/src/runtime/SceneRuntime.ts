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
  createThreeResidentInitialSlots,
  createTwoResidentInitialSlots,
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
  resolvePerspectiveFitDistance,
  resolveWorldGroupAim,
  resolveWorldZoomDistance
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
const BUBBLE_HEAD_CLEARANCE_HEIGHT_RATIO = 0.08
export const RESIDENT_CHAT_APPROACH_DISTANCE = 0.72
export const RESIDENT_CHAT_APPROACH_TOLERANCE = 0.08
export const MAX_GROUP_CHAT_PARTICIPANTS = 10
const GROUP_CHAT_ADJACENT_DISTANCE = 0.78
const GROUP_CHAT_THREE_SIDE_OFFSET = 0.82
const GROUP_CHAT_THREE_CENTER_DEPTH = 0.40
const GROUP_CHAT_FOCUS_FORWARD_OFFSET = 0.38

export interface ResidentChatApproachPlan {
  readonly arrived: boolean
  readonly destination: THREE.Vector3 | null
}

export function resolveResidentChatApproachPlan(
  sourceRoot: THREE.Vector3,
  targetRoot: THREE.Vector3,
  sourcePresentation: THREE.Vector3,
  targetPresentation: THREE.Vector3,
  bounds: { readonly min: THREE.Vector3; readonly max: THREE.Vector3 }
): ResidentChatApproachPlan {
  const logicalAway = sourceRoot.clone().sub(targetRoot)
  logicalAway.y = 0
  const logicalDistance = logicalAway.length()
  if (
    logicalDistance >= RESIDENT_CHAT_APPROACH_DISTANCE - RESIDENT_CHAT_APPROACH_TOLERANCE
    && logicalDistance <= RESIDENT_CHAT_APPROACH_DISTANCE + RESIDENT_CHAT_APPROACH_TOLERANCE
  ) {
    return { arrived: true, destination: null }
  }

  const presentationAway = sourcePresentation.clone().sub(targetPresentation)
  presentationAway.y = 0
  const presentationDistance = presentationAway.length()
  const away = presentationDistance > 1e-5
    ? presentationAway.multiplyScalar(1 / presentationDistance)
    : logicalDistance > 1e-5
      ? logicalAway.multiplyScalar(1 / logicalDistance)
      : new THREE.Vector3(sourceRoot.x <= targetRoot.x ? -1 : 1, 0, 0)

  const destination = targetRoot.clone().addScaledVector(
    away,
    RESIDENT_CHAT_APPROACH_DISTANCE
  )
  destination.x = THREE.MathUtils.clamp(destination.x, bounds.min.x, bounds.max.x)
  destination.y = sourceRoot.y
  destination.z = THREE.MathUtils.clamp(destination.z, bounds.min.z, bounds.max.z)
  return { arrived: false, destination }
}

export function resolveGroupConversationSlots(
  positions: readonly THREE.Vector3[],
  bounds: { readonly min: THREE.Vector3; readonly max: THREE.Vector3 }
): readonly THREE.Vector3[] {
  const count = positions.length
  if (count < 2 || count > MAX_GROUP_CHAT_PARTICIPANTS) return []

  const average = positions.reduce(
    (sum, position) => sum.add(position),
    new THREE.Vector3()
  ).multiplyScalar(1 / count)
  const safeWidth = Math.max(0, bounds.max.x - bounds.min.x)
  const safeDepth = Math.max(0, bounds.max.z - bounds.min.z)

  if (count === 3) {
    const sideOffset = Math.min(GROUP_CHAT_THREE_SIDE_OFFSET, safeWidth * 0.28)
    const centerDepth = Math.min(GROUP_CHAT_THREE_CENTER_DEPTH, safeDepth * 0.20)
    const sideDepth = centerDepth * 0.18
    const centerX = bounds.min.x + sideOffset <= bounds.max.x - sideOffset
      ? THREE.MathUtils.clamp(average.x, bounds.min.x + sideOffset, bounds.max.x - sideOffset)
      : (bounds.min.x + bounds.max.x) * 0.5
    const centerZ = bounds.min.z + centerDepth <= bounds.max.z - sideDepth
      ? THREE.MathUtils.clamp(average.z, bounds.min.z + centerDepth, bounds.max.z - sideDepth)
      : (bounds.min.z + bounds.max.z) * 0.5
    const orderedIndices = positions
      .map((position, index) => ({ index, x: position.x }))
      .sort((left, right) => left.x - right.x)
      .map(({ index }) => index)
    const visualSlots = [
      new THREE.Vector3(centerX - sideOffset, 0, centerZ + sideDepth),
      new THREE.Vector3(centerX, 0, centerZ - centerDepth),
      new THREE.Vector3(centerX + sideOffset, 0, centerZ + sideDepth)
    ]
    const result = positions.map((position) => position.clone())
    for (let visualIndex = 0; visualIndex < visualSlots.length; visualIndex += 1) {
      const sourceIndex = orderedIndices[visualIndex]
      result[sourceIndex] = visualSlots[visualIndex].clone()
      result[sourceIndex].y = positions[sourceIndex].y
    }
    return result
  }

  const desiredRadius = GROUP_CHAT_ADJACENT_DISTANCE / Math.max(
    0.1,
    2 * Math.sin(Math.PI / count)
  )
  const radiusX = Math.min(desiredRadius, safeWidth * 0.38)
  const radiusZ = Math.min(desiredRadius * 0.62, safeDepth * 0.38)
  const centerX = bounds.min.x + radiusX <= bounds.max.x - radiusX
    ? THREE.MathUtils.clamp(average.x, bounds.min.x + radiusX, bounds.max.x - radiusX)
    : (bounds.min.x + bounds.max.x) * 0.5
  const centerZ = bounds.min.z + radiusZ <= bounds.max.z - radiusZ
    ? THREE.MathUtils.clamp(average.z, bounds.min.z + radiusZ, bounds.max.z - radiusZ)
    : (bounds.min.z + bounds.max.z) * 0.5

  return positions.map((position, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / count
    return new THREE.Vector3(
      THREE.MathUtils.clamp(centerX + Math.cos(angle) * radiusX, bounds.min.x, bounds.max.x),
      position.y,
      THREE.MathUtils.clamp(centerZ + Math.sin(angle) * radiusZ, bounds.min.z, bounds.max.z)
    )
  })
}
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

export interface ResidentScreenAnchor {
  readonly x: number
  readonly y: number
  readonly visible: boolean
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
  private readonly initialSceneAssetsReady: Promise<void>
  private residentHeight = 1.6
  private readonly residentHeights = new Map<string, number>()
  private readonly cameraAim = new THREE.Vector3()
  private readonly cameraTargetAim = new THREE.Vector3()
  private readonly cameraTargetPosition = new THREE.Vector3()
  private readonly worldCameraAim = new THREE.Vector3()
  private readonly worldCameraFarPosition = new THREE.Vector3()
  private readonly worldCameraBoomDirection = new THREE.Vector3(0, 0.1, 1).normalize()
  private readonly focusedHeadPosition = new THREE.Vector3()
  private readonly overlayAnchorPosition = new THREE.Vector3()
  private primaryResidentName: string = M0_WORLD_CONFIG.residentName
  private residentRosterOrder: string[] = []
  private focusedResidentName: string | null = null
  private focusChangeListener: ((residentName: string | null) => void) | null = null
  private lastResidentRosterSize = -1
  private initialRosterLayoutActive = false
  private initialRosterLayoutKey: string | null = null
  private initialRosterCenterZ: number | null = null
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
  private readonly residentMotionTunings = new Map<string, MotionTuning>()
  private readonly speechAnalysers = new Map<string, AnalyserNode>()
  private readonly groupConversationLookTarget = new THREE.Object3D()
  private readonly conversationReturnPositions = new Map<string, THREE.Vector3>()

  constructor(environmentOptions: EnvironmentOptions = M0_WORLD_CONFIG.environment) {
    this.environmentQuality = environmentOptions.quality ?? 'medium'
    const loadingManager = new THREE.LoadingManager()
    this.initialSceneAssetsReady = new Promise((resolve) => {
      loadingManager.onLoad = resolve
    })
    this.environment = new EnvironmentController(
      this.scene,
      environmentOptions,
      { textureLoader: new THREE.TextureLoader(loadingManager) }
    )
  }

  whenInitialSceneReady(): Promise<void> {
    return this.initialSceneAssetsReady
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

  async loadAvatar(relativePath: string, residentName?: string): Promise<void> {
    const targetResidentName = residentName ?? this.getDebugTargetResidentName()
    if (!this.residents.get(this.primaryResidentName)) {
      this.primaryResidentName = targetResidentName
    }
    await this.loadResidentAvatar(targetResidentName, relativePath)
  }

  async loadResidentAvatar(residentName: string, relativePath: string): Promise<void> {
    if (this.residents.get(residentName)) {
      await this.residents.changeAvatar(residentName, relativePath)
    } else {
      await this.residents.spawn({ name: residentName, avatar: relativePath })
    }

    const resident = this.residents.get(residentName)
    if (!resident?.vrm) return

    if (
      this.residentRosterOrder.length === 0
      && !this.residents.get(this.primaryResidentName)
    ) {
      this.primaryResidentName = residentName
    }
    const residentMotionTuning = this.residentMotionTunings.get(residentName) ?? DEFAULT_MOTION_TUNING
    this.residentMotionTunings.set(residentName, residentMotionTuning)
    resident.setMotionTuning(residentMotionTuning)
    const height = this.measureResidentHeight(resident.root)
    this.residentHeights.set(residentName, height)
    this.recomputeResidentHeight()
    this.configureCameraRigs(this.residentHeight, !this.cameraRigReady)
    this.reconcileCameraFocusAfterRosterChange(true)
    this.updateResidentPresentationBounds()
    if (residentName === this.primaryResidentName) {
      this.previousResidentPosition.copy(resident.root.position)
      this.hasPreviousResidentPosition = true
    }
    resident.face(this.camera)
    const speechAnalyser = this.speechAnalysers.get(residentName)
    resident.setLipSyncAnalyser(speechAnalyser ?? null)
    resident.update(0)
  }

  unloadResident(residentName: string): void {
    const removingPrimary = residentName === this.primaryResidentName
    this.residents.remove(residentName)
    this.residentHeights.delete(residentName)
    this.residentMotionTunings.delete(residentName)
    this.speechAnalysers.delete(residentName)
    this.conversationReturnPositions.delete(residentName)
    this.recomputeResidentHeight()
    if (this.residents.size > 0 && this.cameraRigReady) {
      this.configureCameraRigs(this.residentHeight, false)
    }

    if (removingPrimary) {
      const replacement = this.residents.getEntries()[0]
      if (replacement) {
        this.primaryResidentName = replacement[0]
        if (replacement[1].vrm) {
          this.previousResidentPosition.copy(replacement[1].root.position)
          this.hasPreviousResidentPosition = true
        }
      } else {
        this.primaryResidentName = M0_WORLD_CONFIG.residentName
        this.hasPreviousResidentPosition = false
        this.cameraRigReady = false
      }
    }
    this.reconcileCameraFocusAfterRosterChange(true)
  }

  unloadAvatar(): void {
    this.unloadResident(this.primaryResidentName)
  }

  getPrimaryResidentName(): string {
    return this.primaryResidentName
  }

  setResidentRosterOrder(names: readonly string[]): void {
    const next = [...new Set(names)]
    const changed = next.length !== this.residentRosterOrder.length
      || next.some((name, index) => name !== this.residentRosterOrder[index])
    if (!changed) return

    this.residentRosterOrder = next
    if (next.length > 0 && !next.includes(this.primaryResidentName)) {
      this.primaryResidentName = next[0]
    }
    this.initialRosterLayoutKey = null
    this.initialRosterLayoutActive = this.residents.size === 2 || this.residents.size === 3
    this.initialRosterCenterZ = null
    this.reconcileCameraFocusAfterRosterChange(true)
  }

  playAnimation(name: AnimationName): boolean {
    return this.residents.get(this.getDebugTargetResidentName())?.playAnimation(name) ?? false
  }

  setEmotion(name: EmotionName): boolean {
    return this.residents.get(this.getDebugTargetResidentName())?.setEmotion(name) ?? false
  }

  getAvailableEmotions(): readonly EmotionName[] {
    return this.residents.get(this.getDebugTargetResidentName())?.getAvailableEmotions() ?? ['neutral']
  }

  triggerBlink(): boolean {
    return this.residents.get(this.getDebugTargetResidentName())?.triggerBlink() ?? false
  }

  setSpeechAnalyser(residentName: string, analyser: AnalyserNode | null): void {
    if (analyser) this.speechAnalysers.set(residentName, analyser)
    else this.speechAnalysers.delete(residentName)
    this.residents.get(residentName)?.setLipSyncAnalyser(analyser)
  }

  faceResidentToMaster(residentName: string): boolean {
    return this.residents.get(residentName)?.face(this.camera) ?? false
  }

  faceResidentToResident(residentName: string, targetName: string): boolean {
    const resident = this.residents.get(residentName)
    const target = this.residents.get(targetName)
    if (!resident?.vrm || !target?.vrm) return false
    return resident.face(target.root, true)
  }

  standResident(residentName: string): boolean {
    const resident = this.residents.get(residentName)
    if (!resident) return false

    const returnPosition = this.conversationReturnPositions.get(residentName)
    this.conversationReturnPositions.delete(residentName)
    resident.clearBodyFacing()
    resident.resetSeparationOffset()
    this.residents.setNaturalSeparationDistance(0.96)

    if (returnPosition) {
      const bounds = this.getScreenSafeMovementBounds()
      const target = returnPosition.clone().clamp(bounds.min, bounds.max)
      const started = resident.moveTo(target, () => {
        resident.setProximityMode('natural')
        resident.face(this.camera)
      }, bounds, false)
      if (started) {
        return true
      }
    }

    resident.setProximityMode('natural')
    resident.face(this.camera)
    return resident.playAnimation('stand')
  }

  gatherResidents(
    participantNames: readonly string[],
    onArrive?: () => void
  ): boolean {
    const uniqueNames = [...new Set(participantNames)]
    if (
      uniqueNames.length !== participantNames.length
      || uniqueNames.length < 2
      || uniqueNames.length > MAX_GROUP_CHAT_PARTICIPANTS
    ) return false

    const participants = uniqueNames.map((name) => this.residents.get(name))
    if (participants.some((resident) => !resident?.vrm)) return false
    const loadedParticipants = participants as ResidentInstance[]
    const bounds = this.getScreenSafeMovementBounds()
    const slots = resolveGroupConversationSlots(
      loadedParticipants.map((resident) => resident.root.position.clone()),
      bounds
    )
    if (slots.length !== loadedParticipants.length) return false

    this.initialRosterLayoutActive = false
    for (const resident of loadedParticipants) {
      if (!this.conversationReturnPositions.has(resident.name)) {
        this.conversationReturnPositions.set(resident.name, resident.root.position.clone())
      }
      resident.resetSeparationOffset()
    }
    let remaining = loadedParticipants.length
    let completed = false
    const finishOne = (): void => {
      remaining -= 1
      if (remaining > 0 || completed) return
      completed = true

      const lookPosition = new THREE.Vector3(
        slots.reduce((sum, slot) => sum + slot.x, 0) / slots.length,
        0,
        Math.max(...slots.map((slot) => slot.z)) + GROUP_CHAT_FOCUS_FORWARD_OFFSET
      )
      let headY = 0
      let headCount = 0
      for (const resident of loadedParticipants) {
        const head = resident.getCameraHeadPosition(new THREE.Vector3())
        if (!head) continue
        headY += head.y
        headCount += 1
      }
      lookPosition.y = headCount > 0
        ? headY / headCount
        : loadedParticipants[0].root.position.y + this.residentHeight * 0.72
      this.groupConversationLookTarget.position.copy(lookPosition)
      this.groupConversationLookTarget.updateMatrixWorld(true)
      for (const resident of loadedParticipants) {
        resident.setProximityMode('formation')
        resident.resetSeparationOffset()
        resident.face(this.groupConversationLookTarget, true)
      }
      onArrive?.()
    }

    for (let index = 0; index < loadedParticipants.length; index += 1) {
      const resident = loadedParticipants[index]
      const slot = slots[index]
      resident.setProximityMode('formation')
      const started = resident.moveTo(slot, () => {
        resident.setProximityMode('formation')
        resident.resetSeparationOffset()
        finishOne()
      }, bounds, false)
      // moveTo internally enters directed mode; Group Formation owns its slots,
      // so disable pairwise collision offsets again for the travel itself.
      resident.setProximityMode('formation')
      if (!started) {
        resident.resetSeparationOffset()
        finishOne()
      }
    }
    return true
  }

  approachResident(
    residentName: string,
    targetName: string,
    onArrive?: () => void
  ): boolean {
    if (residentName === targetName) return false
    const resident = this.residents.get(residentName)
    const target = this.residents.get(targetName)
    if (!resident?.vrm || !target?.vrm) return false

    const bounds = this.getScreenSafeMovementBounds()
    const plan = resolveResidentChatApproachPlan(
      resident.root.position,
      target.root.position,
      resident.getPresentationPosition(),
      target.getPresentationPosition(),
      bounds
    )
    if (!this.conversationReturnPositions.has(residentName)) {
      this.conversationReturnPositions.set(residentName, resident.root.position.clone())
    }
    if (!this.conversationReturnPositions.has(targetName)) {
      this.conversationReturnPositions.set(targetName, target.root.position.clone())
    }
    resident.resetSeparationOffset()
    target.resetSeparationOffset()

    if (plan.arrived) {
      resident.setProximityMode('directed')
      target.setProximityMode('directed')
      onArrive?.()
      return true
    }

    if (!plan.destination) return false
    target.setProximityMode('directed')
    const started = resident.moveTo(plan.destination, () => {
      resident.setProximityMode('directed')
      target.setProximityMode('directed')
      onArrive?.()
    }, bounds, false)
    if (!started) {
      target.setProximityMode('natural')
    } else {
      this.initialRosterLayoutActive = false
    }
    return started
  }

  getResidentScreenAnchor(residentName: string): ResidentScreenAnchor | null {
    if (!this.canvas) return null
    const resident = this.residents.get(residentName)
    const head = resident?.getCameraHeadPosition(this.overlayAnchorPosition)
    if (!resident?.vrm || !head) return null

    const residentHeight = this.residentHeights.get(residentName) ?? this.residentHeight
    head.y += residentHeight * BUBBLE_HEAD_CLEARANCE_HEIGHT_RATIO
    head.project(this.camera)
    const rect = this.canvas.getBoundingClientRect()
    return {
      x: rect.left + ((head.x + 1) * 0.5) * rect.width,
      y: rect.top + ((1 - head.y) * 0.5) * rect.height,
      visible: head.z >= -1 && head.z <= 1 && head.x >= -1.2 && head.x <= 1.2 && head.y >= -1.2 && head.y <= 1.2
    }
  }

  getFocusedResidentName(): string | null {
    return this.focusedResidentName
  }

  getDebugTargetResidentName(): string {
    return this.focusedResidentName ?? this.primaryResidentName
  }

  setFocusChangeListener(listener: ((residentName: string | null) => void) | null): void {
    this.focusChangeListener = listener
    listener?.(this.focusedResidentName)
  }

  focusResident(residentName: string | null): boolean {
    if (residentName !== null && !this.residents.get(residentName)) {
      return false
    }
    if (residentName !== null && residentName !== this.focusedResidentName) {
      this.focusZoom = 0
    }
    this.setFocusedResidentName(residentName)
    return true
  }

  getPoseAdjustMotionOptions(): readonly MotionPoseOption[] {
    return this.residents.get(this.getDebugTargetResidentName())?.getPoseAdjustMotionOptions() ?? []
  }

  beginPoseAdjustment(
    clip: AnimationClipName,
    scope: PoseAdjustScope,
    onChange: (value: PoseAdjustment) => void
  ): PoseAdjustment | null {
    const resident = this.residents.get(this.getDebugTargetResidentName())
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
    return this.residents.get(this.getDebugTargetResidentName())?.getPoseAdjustment(clip, scope) ?? null
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
    const residentName = this.getDebugTargetResidentName()
    this.residentMotionTunings.set(residentName, next)
    this.residents.get(residentName)?.setMotionTuning(next)
    return { ...next }
  }

  getMotionTuning(): MotionTuning {
    const residentName = this.getDebugTargetResidentName()
    return { ...(this.residentMotionTunings.get(residentName) ?? DEFAULT_MOTION_TUNING) }
  }

  moveResidentTo(location: M0LocationName, onArrive?: () => void): boolean {
    const resident = this.residents.get(this.getDebugTargetResidentName())
    if (!resident) {
      return false
    }

    const bounds = this.getScreenSafeMovementBounds()
    const target = createDirectionalMoveTarget(
      resident.root.position,
      location,
      bounds,
      Math.random,
      this.residents.size > 1
    )
    const started = resident.moveTo(
      target,
      onArrive,
      bounds,
      this.residents.size > 1
    )
    if (started) {
      this.initialRosterLayoutActive = false
      this.residents.setNaturalSeparationDistance(0.96)
    }
    return started
  }

  getM0Diagnostics(): M0RuntimeDiagnostics {
    const resident = this.residents.get(this.primaryResidentName)
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
    const resident = this.residents.get(this.primaryResidentName)

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
    this.residentHeights.clear()
    this.residentMotionTunings.clear()
    this.speechAnalysers.clear()
    this.conversationReturnPositions.clear()
    this.environment.dispose()

    this.postProcessing?.dispose()
    this.postProcessing = null
    this.renderer?.dispose()
    this.renderer = null
    this.canvas = null
    this.focusChangeListener = null
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

    const resident = this.residents.get(this.getDebugTargetResidentName())
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

    const resident = this.residents.get(this.getDebugTargetResidentName())
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
    const safeDistance = resolveWorldZoomDistance(
      this.worldCameraFarDistance,
      this.worldCameraNearDistance,
      this.getGroupSafeCameraDistance(),
      this.worldZoom
    )
    this.cameraTargetAim.copy(resolveWorldGroupAim(
      this.worldCameraAim,
      this.groupBoundsCenter,
      this.residents.size
    ))
    this.cameraTargetPosition.copy(this.cameraTargetAim).addScaledVector(
      this.worldCameraBoomDirection,
      safeDistance
    )
  }

  private measureResidentHeight(residentRoot: THREE.Object3D): number {
    const bounds = new THREE.Box3().setFromObject(residentRoot)
    return Math.max(bounds.getSize(new THREE.Vector3()).y, 1)
  }

  private recomputeResidentHeight(): void {
    this.residentHeight = Math.max(1.6, ...this.residentHeights.values())
  }

  private configureCameraRigs(avatarHeight: number, resetCamera: boolean): void {
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
    if (resetCamera) {
      this.worldZoom = 0
      this.focusZoom = 0
      this.camera.position.copy(this.worldCameraFarPosition)
      this.cameraAim.copy(this.worldCameraAim)
      this.cameraTargetPosition.copy(this.camera.position)
      this.cameraTargetAim.copy(this.cameraAim)
      this.camera.lookAt(this.cameraAim)
      this.camera.updateMatrixWorld(true)
    }
    this.camera.updateProjectionMatrix()
    this.cameraRigReady = true
  }

  private reconcileCameraFocusAfterRosterChange(force = false): void {
    const size = this.residents.size
    const previousSize = this.lastResidentRosterSize
    if (!force && size === previousSize) {
      return
    }
    this.lastResidentRosterSize = size
    const layoutKey = size === 2 || size === 3
      ? `${size}:${this.getOrderedResidentEntries().map(([name]) => name).join('\u0000')}`
      : null
    if (layoutKey !== this.initialRosterLayoutKey) {
      const hadInitialLayout = this.initialRosterLayoutKey !== null
      this.initialRosterLayoutKey = layoutKey
      this.initialRosterLayoutActive = layoutKey !== null
      if (!hadInitialLayout || layoutKey === null) {
        this.initialRosterCenterZ = null
      }
    }

    if (size === 0) {
      this.setFocusedResidentName(null)
    } else if (
      previousSize !== size
      || (this.focusedResidentName !== null && !this.residents.get(this.focusedResidentName))
    ) {
      this.setFocusedResidentName(null)
    }
    this.updateResidentPresentationBounds()
  }

  private setFocusedResidentName(residentName: string | null): void {
    if (this.focusedResidentName === residentName) {
      return
    }
    this.focusedResidentName = residentName
    this.focusChangeListener?.(residentName)
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

  private getOrderedResidentEntries(): readonly (readonly [string, ResidentInstance])[] {
    const entries = this.residents.getEntries()
    if (this.residentRosterOrder.length === 0) return entries

    const byName = new Map(entries)
    const ordered: (readonly [string, ResidentInstance])[] = []
    const included = new Set<string>()
    for (const name of this.residentRosterOrder) {
      const resident = byName.get(name)
      if (!resident) continue
      ordered.push([name, resident] as const)
      included.add(name)
    }
    for (const entry of entries) {
      if (!included.has(entry[0])) ordered.push(entry)
    }
    return ordered
  }

  private updateResidentPresentationBounds(): void {
    const entries = this.getOrderedResidentEntries()
    if (entries.length === 0) {
      return
    }

    // Movement range is a presentation constraint derived from the current
    // camera, not a fixed world-width rule. Every resident stays inside the
    // visible frame regardless of roster size or Focus state.
    const screenSafeBounds = this.getScreenSafeMovementBounds()
    const safeWidth = Math.max(0, screenSafeBounds.max.x - screenSafeBounds.min.x)
    const initialSeparationDistance = entries.length === 3
      ? Math.min(0.96, safeWidth * 0.10)
      : Math.min(0.96, safeWidth * 0.22)
    this.residents.setNaturalSeparationDistance(
      (entries.length === 2 || entries.length === 3) && this.initialRosterLayoutActive
        ? initialSeparationDistance
        : 0.96
    )
    for (const [, resident] of entries) {
      resident.constrainHorizontal(screenSafeBounds)
    }

    if (entries.length === 2 && this.initialRosterLayoutActive) {
      this.applyTwoResidentInitialLayout(entries, screenSafeBounds)
    } else if (entries.length === 3 && this.initialRosterLayoutActive) {
      this.applyThreeResidentInitialLayout(entries, screenSafeBounds)
    }
  }

  private applyTwoResidentInitialLayout(
    entries: readonly (readonly [string, ResidentInstance])[],
    bounds: ReturnType<typeof createScreenSafeSwimBounds>
  ): void {
    if (this.initialRosterCenterZ === null) {
      this.initialRosterCenterZ = THREE.MathUtils.clamp(
        entries.reduce((sum, [, resident]) => sum + resident.root.position.z, 0) / entries.length,
        bounds.min.z,
        bounds.max.z
      )
    }
    const [leftSlot, rightSlot] = createTwoResidentInitialSlots(
      bounds,
      this.initialRosterCenterZ
    )
    const placements = [
      [entries[0][1], leftSlot],
      [entries[1][1], rightSlot]
    ] as const

    for (const [resident, slot] of placements) {
      resident.resetSeparationOffset()
      resident.root.position.x = slot.x
      resident.root.position.z = slot.z
    }
    const primary = this.residents.get(this.primaryResidentName)
    if (primary) {
      this.previousResidentPosition.copy(primary.root.position)
      this.hasPreviousResidentPosition = true
    }
  }

  private applyThreeResidentInitialLayout(
    entries: readonly (readonly [string, ResidentInstance])[],
    bounds: ReturnType<typeof createScreenSafeSwimBounds>
  ): void {
    if (entries.length !== 3) return

    if (this.initialRosterCenterZ === null) {
      this.initialRosterCenterZ = THREE.MathUtils.clamp(
        entries.reduce((sum, [, resident]) => sum + resident.root.position.z, 0) / entries.length,
        bounds.min.z,
        bounds.max.z
      )
    }
    const [leftSlot, centerSlot, rightSlot] = createThreeResidentInitialSlots(
      bounds,
      this.initialRosterCenterZ
    )
    const placements = [
      [entries[0][1], leftSlot],
      [entries[1][1], centerSlot],
      [entries[2][1], rightSlot]
    ] as const

    for (const [resident, slot] of placements) {
      resident.resetSeparationOffset()
      resident.root.position.x = slot.x
      resident.root.position.z = slot.z
    }
    const primary = this.residents.get(this.primaryResidentName)
    if (primary) {
      this.previousResidentPosition.copy(primary.root.position)
      this.hasPreviousResidentPosition = true
    }
  }

  private getScreenSafeMovementBounds() {
    // Use the camera's current zoomed framing. Portrait, landscape, and World
    // zoom therefore all produce their own safe movement width, so repeated
    // directional Moves cannot walk a resident out of the visible frame.
    return createScreenSafeSwimBounds(this.camera, this.residentHeight)
  }
}
