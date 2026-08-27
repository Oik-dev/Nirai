import * as THREE from 'three'
import type { VRM } from '@pixiv/three-vrm'
import { LoadedVrm, VrmLoader } from './vrm/VrmLoader'
import {
  AnimationClipName,
  AnimationController,
  AnimationName,
  type AnimationLoadOptions
} from './vrm/AnimationController'
import { EmotionName, ExpressionController } from './vrm/ExpressionController'
import {
  MovementController,
  type LocomotionMedium,
  type SwimBounds
} from './MovementController'
import { UnderwaterMotionController, type UnderwaterPose } from './UnderwaterMotionController'

type ResidentMotionState = 'idle' | 'moving' | 'settling' | 'afk' | 'sleep'

interface FloatProfile {
  readonly lift: number
  readonly primary: number
  readonly secondary: number
  readonly drift: number
  readonly tilt: number
}

interface StateTransition {
  readonly clip: AnimationClipName
  readonly targetState: Exclude<ResidentMotionState, 'moving' | 'settling'>
  readonly durationSec: number
  readonly blendSec: number
  readonly fromLift: number
  readonly fromProfile: FloatProfile
  elapsedSec: number
}

interface SleepDescent {
  readonly start: THREE.Vector3
  readonly target: THREE.Vector3
  readonly durationSec: number
  elapsedSec: number
}

const TRANSITION_TIMING = {
  stand: { delay: 0, blend: 0.9 },
  afk: { delay: 0, blend: 1.35 },
  sleep: { delay: 0, blend: 1.8 },
  locomotionBlend: 0.8
} as const

const SLEEP_DESCENT_DURATION_SEC = 3
const AFK_GROUND_DESCENT_DURATION_SEC = 2.6

// Internal AFK clip ids are zero-based while public filenames are one-based.
const AFK_CLOSED_EYES = new Set<AnimationClipName>([
  'afk-0',
  'afk-1',
  'afk-5',
  'sleep'
]) // AFK-01 / AFK-02 / AFK-06 / Sleep
const AFK_GROUNDED = 'afk-4' as const // AFK-05
const AFK_REORIENTED = 'afk-5' as const // AFK-06
const AFK_GROUNDED_09 = 'afk-8' as const // AFK-09
const RETIRED_AFK_CLIPS = new Set<AnimationClipName>(['afk-6', 'afk-7']) // AFK-07 / AFK-08
const GROUNDING_AFK_CLIPS = new Set<AnimationClipName>([AFK_GROUNDED, AFK_GROUNDED_09])
const CAMERA_FRAMING_BONE_NAMES = [
  'head',
  'neck',
  'chest',
  'hips',
  'leftShoulder',
  'rightShoulder',
  'leftHand',
  'rightHand',
  'leftUpperLeg',
  'rightUpperLeg',
  'leftLowerLeg',
  'rightLowerLeg',
  'leftFoot',
  'rightFoot'
] as const

export type PoseAdjustScope = 'root' | 'head'
export type ResidentProximityMode = 'natural' | 'directed'

export interface PoseAdjustment {
  readonly pitchDeg: number
  readonly yawDeg: number
}

export interface MotionPoseOption {
  readonly clip: AnimationClipName
  readonly label: string
}

interface MotionPoseAdjustment {
  root: PoseAdjustment
  head: PoseAdjustment
}

const ZERO_POSE_ADJUSTMENT: Readonly<PoseAdjustment> = { pitchDeg: 0, yawDeg: 0 }

const FLOAT_PROFILE: Readonly<Record<Exclude<ResidentMotionState, 'settling'>, FloatProfile>> = {
  idle: { lift: 0.105, primary: 0.030, secondary: 0.014, drift: 0.010, tilt: 0.014 },
  moving: { lift: 0.080, primary: 0.022, secondary: 0.010, drift: 0.008, tilt: 0.011 },
  afk: { lift: 0.155, primary: 0.042, secondary: 0.018, drift: 0.014, tilt: 0.018 },
  sleep: { lift: 0, primary: 0.003, secondary: 0.002, drift: 0.002, tilt: 0.003 }
}

const DEFAULT_HOVER_ROOT_Y = 0.32
const SEABED_EPSILON = 0.045
const MANUAL_BLINK_DURATION_SEC = 0.28
const MAX_RESIDENT_FRAME_DELTA = 1 / 15
const RESIDENT_SIMULATION_STEP = 1 / 60
const DEFAULT_PRESENTATION_BOUNDS: SwimBounds = {
  min: new THREE.Vector3(-2.15, 0, -1.42),
  max: new THREE.Vector3(2.15, 1.12, 0.36)
}
const SEABED_FLOAT_PROFILE = {
  lift: 0.006,
  primary: 0.003,
  secondary: 0.002,
  drift: 0.002,
  tilt: 0.004
} as const
// Grounded AFKs should visually sit at exactly the same height as Sleep.
const AFK_GROUNDED_FLOAT_PROFILE = FLOAT_PROFILE.sleep

export interface VrmLoaderPort {
  load(bytes: Uint8Array): Promise<LoadedVrm>
  update(loaded: LoadedVrm, delta: number): void
  unload(loaded: LoadedVrm): void
}

export type AvatarReader = (relativePath: string) => Promise<Uint8Array>

export interface AnimationControllerPort {
  load(name: AnimationClipName, url: string, options?: AnimationLoadOptions): Promise<void>
  play(name: AnimationClipName): void
  crossFade(next: AnimationClipName, durationSec: number): void
  getCurrentName(): AnimationClipName | null
  update(delta: number): void
  dispose(): void
}

export type AnimationControllerFactory = (loaded: LoadedVrm) => AnimationControllerPort

export interface ResidentAnimationUrls {
  readonly stand: string
  readonly walk: string
  readonly afk: readonly string[]
  readonly sleep: string
}

export interface ExpressionControllerPort {
  setEmotion(name: EmotionName): void
  getAvailableEmotions?(): readonly EmotionName[]
  triggerBlink(durationSec?: number): void
  setBlinkHeld?(held: boolean): void
  update(delta: number): void
  dispose(): void
}

export type ExpressionControllerFactory = (loaded: LoadedVrm) => ExpressionControllerPort

export class ResidentInstance {
  readonly root = new THREE.Group()
  readonly movement: MovementController
  vrm: VRM | null = null
  animation: AnimationControllerPort | null = null
  expression: ExpressionControllerPort | null = null

  private loadGeneration = 0
  private loadedAvatar: LoadedVrm | null = null
  private lastAfkClipName: AnimationClipName | null = null
  private readonly floatPhase: number
  private floatElapsed = 0
  private readonly avatarBasePosition = new THREE.Vector3()
  private readonly avatarBaseRotation = new THREE.Euler()
  private motionState: ResidentMotionState = 'idle'
  private transition: StateTransition | null = null
  private underwaterMotion: UnderwaterMotionController | null = null
  private presentationBounds: SwimBounds = cloneBounds(DEFAULT_PRESENTATION_BOUNDS)
  private pendingSleep = false
  private animationTimeScale = 0.76
  private glidePitch = 0
  private legSwingEnabled = false
  private sleepDescent: SleepDescent | null = null
  private afkGroundDescent: SleepDescent | null = null
  private afkGrounded = false
  private hoverRecoveryTargetY: number | null = null
  private presentationYaw = 0
  private presentationPitch = 0
  private proximityMode: ResidentProximityMode = 'natural'
  private readonly separationOffset = new THREE.Vector3()
  private readonly separationTarget = new THREE.Vector3()
  private readonly cameraAnchorScratch = new THREE.Vector3()
  private readonly poseAdjustments = createDefaultPoseAdjustments()
  private readonly loadedAnimationClips = new Set<AnimationClipName>()
  private readonly animationLoads = new Map<AnimationClipName, Promise<void>>()
  private motionRequestSerial = 0

  constructor(
    readonly name: string,
    private readonly loader: VrmLoaderPort = new VrmLoader(),
    private readonly readAvatar: AvatarReader = (path) => window.nirai.avatar.read(path),
    private readonly createAnimation: AnimationControllerFactory = (loaded) =>
      new AnimationController(loaded.mixer, loaded.vrm),
    private readonly animationUrls: ResidentAnimationUrls = createDefaultAnimationUrls(),
    private readonly createExpression: ExpressionControllerFactory = (loaded) =>
      new ExpressionController(loaded.vrm.expressionManager),
    private readonly random: () => number = Math.random
  ) {
    this.root.name = `Resident:${name}`
    this.floatPhase = createResidentPhase(name)
    this.movement = new MovementController(this.root, 1.05, this.floatPhase, random)
  }

  async loadAvatar(relativePath: string): Promise<void> {
    const generation = ++this.loadGeneration

    try {
      const bytes = await this.readAvatar(relativePath)

      if (generation !== this.loadGeneration) {
        return
      }

      const nextAvatar = await this.loader.load(bytes)
      const nextAnimation = this.createAnimation(nextAvatar)

      try {
        await nextAnimation.load('stand', this.animationUrls.stand)
      } catch (error) {
        nextAnimation.dispose()
        this.loader.unload(nextAvatar)
        throw error
      }

      if (generation !== this.loadGeneration) {
        nextAnimation.dispose()
        this.loader.unload(nextAvatar)
        return
      }

      const nextExpression = this.createExpression(nextAvatar)
      this.replaceAvatar(nextAvatar, nextAnimation, nextExpression)
      this.startBackgroundAnimationLoading(generation, nextAnimation)
    } catch (error) {
      if (generation !== this.loadGeneration) {
        return
      }

      throw error
    }
  }

  update(delta: number): void {
    if (!this.loadedAvatar) {
      return
    }

    const safeDelta = THREE.MathUtils.clamp(delta, 0, MAX_RESIDENT_FRAME_DELTA)
    const stepCount = Math.max(1, Math.ceil(safeDelta / RESIDENT_SIMULATION_STEP))
    const step = stepCount > 0 ? safeDelta / stepCount : 0
    for (let index = 0; index < stepCount; index += 1) {
      this.updateFrame(step)
    }
  }

  private updateFrame(delta: number): void {
    if (!this.loadedAvatar) {
      return
    }

    this.updateStateTransition(delta)
    this.movement.update(delta)
    this.updateSleepDescent(delta)
    this.updateAfkGroundDescent(delta)
    this.updateHoverRecovery(delta)
    this.updateSeparationOffset(delta)
    this.updateFloat(delta)
    this.underwaterMotion?.beginFrame(delta)
    this.animationTimeScale = THREE.MathUtils.damp(
      this.animationTimeScale,
      this.getAnimationTimeScale(),
      3.6,
      delta
    )
    const animationDelta = delta * this.animationTimeScale
    this.animation?.update(animationDelta)
    const currentClip = this.animation?.getCurrentName()
    const headPose = currentClip ? this.getPoseAdjustment(currentClip, 'head') : ZERO_POSE_ADJUSTMENT
    this.underwaterMotion?.apply(
      this.getUnderwaterPose(),
      this.legSwingEnabled,
      hasPoseAdjustment(headPose)
        ? {
            pitchRadians: THREE.MathUtils.degToRad(headPose.pitchDeg),
            yawRadians: THREE.MathUtils.degToRad(headPose.yawDeg)
          }
        : null
    )
    this.expression?.update(delta)
    this.syncLookAtForPresentation()
    this.loader.update(this.loadedAvatar, delta)
  }

  playAnimation(name: AnimationName): boolean {
    if (!this.animation) {
      return false
    }

    if (name === 'afk') {
      const nextAfkClipName = pickAfkClipName(
        this.getLoadedAfkClipNames(),
        this.lastAfkClipName,
        this.random
      )

      if (!nextAfkClipName) {
        return false
      }

      return this.startAfkClip(nextAfkClipName)
    }

    if (name === 'sleep') {
      if (!this.isAnimationLoaded('sleep')) {
        return false
      }

      this.startSleepPresentation()
      return true
    }

    if (name === 'stand') {
      this.legSwingEnabled = false
      this.proximityMode = 'natural'
      this.cancelDeferredMotion()
      const needsHoverRecovery = this.needsHoverRecovery()
      this.movement.cancel()
      this.pendingSleep = false
      this.sleepDescent = null
      this.afkGroundDescent = null
      this.afkGrounded = false
      if (needsHoverRecovery) {
        this.prepareHoverHeight()
      }
      this.beginStateTransition('stand', 'idle', TRANSITION_TIMING.stand)
      return true
    }

    return false
  }

  setEmotion(name: EmotionName): boolean {
    if (!this.expression) {
      return false
    }

    this.expression.setEmotion(name)
    return true
  }

  getAvailableEmotions(): readonly EmotionName[] {
    return this.expression?.getAvailableEmotions?.() ?? ['neutral']
  }

  triggerBlink(): boolean {
    if (!this.expression) {
      return false
    }

    this.expression.triggerBlink(MANUAL_BLINK_DURATION_SEC)
    return true
  }

  getPoseAdjustMotionOptions(): readonly MotionPoseOption[] {
    return createMotionPoseOptions(this.animationUrls.afk.length)
  }

  getProximityMode(): ResidentProximityMode {
    return this.proximityMode
  }

  setProximityMode(mode: ResidentProximityMode): void {
    this.proximityMode = mode
  }

  setSeparationTarget(offset: THREE.Vector3): void {
    this.separationTarget.set(offset.x, 0, offset.z)
  }

  getPresentationPosition(target = new THREE.Vector3()): THREE.Vector3 {
    return target.copy(this.root.position).add(this.separationOffset)
  }

  isSleepPresentationActive(): boolean {
    return this.pendingSleep
      || this.sleepDescent !== null
      || this.motionState === 'sleep'
      || this.transition?.targetState === 'sleep'
  }

  isGroundedPresentationActive(): boolean {
    return this.isSleepPresentationActive()
      || this.afkGroundDescent !== null
      || this.afkGrounded
  }

  getCameraFramingBounds(target = new THREE.Box3()): THREE.Box3 | null {
    const humanoid = this.vrm?.humanoid
    if (!humanoid) {
      return null
    }

    target.makeEmpty()
    let count = 0
    for (const boneName of CAMERA_FRAMING_BONE_NAMES) {
      const bone = humanoid.getNormalizedBoneNode(boneName)
      if (!bone) {
        continue
      }
      bone.getWorldPosition(this.cameraAnchorScratch)
      target.expandByPoint(this.cameraAnchorScratch)
      count += 1
    }

    if (count < 2 || target.isEmpty()) {
      return null
    }

    const size = target.getSize(new THREE.Vector3())
    target.expandByVector(new THREE.Vector3(
      Math.max(0.10, size.x * 0.10),
      Math.max(0.08, size.y * 0.06),
      Math.max(0.10, size.z * 0.10)
    ))
    return target
  }

  getCameraHeadPosition(target = new THREE.Vector3()): THREE.Vector3 | null {
    const head = this.vrm?.humanoid?.getNormalizedBoneNode('head')
    if (!head) {
      return null
    }
    return head.getWorldPosition(target)
  }

  playPoseAdjustmentMotion(clip: AnimationClipName): boolean {
    if (!this.animation || !this.getPoseAdjustMotionOptions().some((option) => option.clip === clip)) {
      return false
    }

    // The pose editor must preview the same presentation the product uses.
    // It only changes pose-adjustment values; it never owns a shortcut state path.
    if (clip === 'stand') {
      return this.playAnimation('stand')
    }
    if (clip === 'sleep') {
      if (!this.isAnimationLoaded('sleep')) {
        return false
      }
      this.startSleepPresentation()
      return true
    }
    if (clip.startsWith('afk-')) {
      return this.startAfkClip(clip)
    }

    return false
  }

  getPoseAdjustment(clip: AnimationClipName, scope: PoseAdjustScope): PoseAdjustment {
    return { ...(this.poseAdjustments.get(clip)?.[scope] ?? ZERO_POSE_ADJUSTMENT) }
  }

  setPoseAdjustment(
    clip: AnimationClipName,
    scope: PoseAdjustScope,
    value: PoseAdjustment
  ): PoseAdjustment {
    const next = {
      pitchDeg: THREE.MathUtils.clamp(
        Number.isFinite(value.pitchDeg) ? value.pitchDeg : 0,
        -89,
        89
      ),
      yawDeg: normalizeDegrees(Number.isFinite(value.yawDeg) ? value.yawDeg : 0)
    }
    const current = this.poseAdjustments.get(clip) ?? createEmptyMotionPoseAdjustment()
    this.poseAdjustments.set(clip, {
      ...current,
      [scope]: next
    })
    return { ...next }
  }

  face(target: THREE.Object3D): boolean {
    if (!this.vrm?.lookAt) {
      return false
    }

    this.vrm.lookAt.autoUpdate = true
    this.vrm.lookAt.target = target
    return true
  }

  moveTo(
    target: THREE.Vector3,
    onArrive?: () => void,
    bounds?: SwimBounds
  ): boolean {
    const medium: LocomotionMedium = 'seabed'
    const legSwingEnabled = true
    if (!this.animation) {
      return false
    }

    const safeTarget = target.clone()
    const safeBounds = cloneBounds(bounds ?? this.presentationBounds)
    const needsHoverRecovery = this.needsHoverRecovery()
    if (needsHoverRecovery && safeTarget.y <= SEABED_EPSILON) {
      safeTarget.y = DEFAULT_HOVER_ROOT_Y
    }

    const startMove = (): void => {
      this.proximityMode = 'directed'
      this.pendingSleep = false
      this.sleepDescent = null
      this.afkGroundDescent = null
      this.afkGrounded = false
      this.transition = null
      this.hoverRecoveryTargetY = null
      this.motionState = 'moving'
      this.legSwingEnabled = legSwingEnabled
      const finish = (): void => {
        this.legSwingEnabled = false
        this.proximityMode = 'natural'
        this.beginStateTransition('stand', 'idle', TRANSITION_TIMING.stand)
        onArrive?.()
      }

      this.startLocomotionPresentation(medium)
      this.movement.moveTo(safeTarget, finish, safeBounds, medium)
    }

    if (!this.isAnimationLoaded('walk')) {
      this.deferMotionUntilLoaded(['walk'], startMove)
      return true
    }

    this.cancelDeferredMotion()
    startMove()
    return true
  }

  swimNear(
    anchor: THREE.Vector3,
    radius: THREE.Vector3,
    bounds: SwimBounds,
    onArrive?: () => void
  ): boolean {
    if (!this.animation) {
      return false
    }

    this.cancelDeferredMotion()
    this.proximityMode = 'natural'
    this.pendingSleep = false
    this.sleepDescent = null
    this.afkGroundDescent = null
    this.afkGrounded = false
    this.transition = null
    this.motionState = 'moving'
    this.legSwingEnabled = false
    this.startLocomotionPresentation('swim')
    this.movement.swimNear(anchor, radius, bounds, () => {
      this.beginStateTransition('stand', 'idle', TRANSITION_TIMING.stand)
      onArrive?.()
    })
    return true
  }

  constrainHorizontal(bounds: SwimBounds): void {
    this.setPresentationBounds(bounds)
    this.movement.constrainHorizontal(bounds)
  }

  setPresentationBounds(bounds: SwimBounds): void {
    this.presentationBounds = cloneBounds(bounds)
  }

  dispose(): void {
    this.loadGeneration += 1
    this.motionRequestSerial += 1
    this.loadedAnimationClips.clear()
    this.animationLoads.clear()
    this.pendingSleep = false
    this.sleepDescent = null
    this.afkGroundDescent = null
    this.afkGrounded = false
    this.hoverRecoveryTargetY = null
    this.presentationYaw = 0
    this.presentationPitch = 0
    this.proximityMode = 'natural'
    this.separationOffset.set(0, 0, 0)
    this.separationTarget.set(0, 0, 0)
    this.legSwingEnabled = false
    this.movement.cancel()
    this.underwaterMotion?.dispose()
    this.underwaterMotion = null

    if (this.loadedAvatar) {
      if (this.loadedAvatar.vrm.lookAt) {
        this.loadedAvatar.vrm.lookAt.target = null
      }
      this.root.remove(this.loadedAvatar.vrm.scene)
      this.animation?.dispose()
      this.expression?.dispose()
      this.loader.unload(this.loadedAvatar)
      this.loadedAvatar = null
      this.vrm = null
      this.animation = null
      this.expression = null
    }
  }

  private replaceAvatar(
    nextAvatar: LoadedVrm,
    nextAnimation: AnimationControllerPort,
    nextExpression: ExpressionControllerPort
  ): void {
    if (this.loadedAvatar) {
      if (this.loadedAvatar.vrm.lookAt) {
        this.loadedAvatar.vrm.lookAt.target = null
      }
      this.root.remove(this.loadedAvatar.vrm.scene)
      this.animation?.dispose()
      this.expression?.dispose()
      this.loader.unload(this.loadedAvatar)
    }

    this.placeOnGround(nextAvatar.vrm.scene)
    this.avatarBasePosition.copy(nextAvatar.vrm.scene.position)
    this.avatarBaseRotation.copy(nextAvatar.vrm.scene.rotation)
    this.floatElapsed = 0
    this.animationTimeScale = 0.76
    this.glidePitch = 0
    this.legSwingEnabled = false
    this.sleepDescent = null
    this.afkGroundDescent = null
    this.afkGrounded = false
    this.hoverRecoveryTargetY = null
    this.presentationYaw = 0
    this.presentationPitch = 0
    this.proximityMode = 'natural'
    this.separationOffset.set(0, 0, 0)
    this.separationTarget.set(0, 0, 0)
    this.transition = null
    this.motionState = this.movement.isMoving ? 'moving' : 'idle'
    if (!this.movement.isMoving && this.root.position.y < DEFAULT_HOVER_ROOT_Y) {
      this.root.position.y = DEFAULT_HOVER_ROOT_Y
    }
    this.loadedAvatar = nextAvatar
    this.vrm = nextAvatar.vrm
    this.motionRequestSerial += 1
    this.loadedAnimationClips.clear()
    this.loadedAnimationClips.add('stand')
    this.animationLoads.clear()
    this.underwaterMotion?.dispose()
    this.underwaterMotion = new UnderwaterMotionController(nextAvatar.vrm, this.floatPhase)
    this.animation = nextAnimation
    this.expression = nextExpression
    this.root.add(nextAvatar.vrm.scene)
    nextAvatar.vrm.scene.visible = true
    nextAnimation.play('stand')
    nextAnimation.update(0)
    nextExpression.update(0)
    this.updateFloat(0)
    this.loader.update(nextAvatar, 0)
    this.root.updateMatrixWorld(true)
  }

  private updateFloat(delta: number): void {
    if (!this.loadedAvatar) {
      return
    }

    this.floatElapsed += Math.max(0, delta)
    const avatarRoot = this.loadedAvatar.vrm.scene
    const profile = this.getCurrentFloatProfile()
    const lift = this.getCurrentLift()
    const primary = Math.sin(this.floatElapsed * 1.03 + this.floatPhase) * profile.primary
    const secondary = Math.sin(this.floatElapsed * 0.37 + this.floatPhase * 1.7) * profile.secondary
    const sideDrift = Math.sin(this.floatElapsed * 0.51 + this.floatPhase * 0.73) * profile.drift
    const depthDrift = Math.cos(this.floatElapsed * 0.33 + this.floatPhase * 1.21) * profile.drift * 0.76
    const targetGlidePitch = this.motionState === 'moving'
      && this.movement.locomotionMedium === 'swim'
      ? THREE.MathUtils.degToRad(-4)
      : 0
    this.glidePitch = THREE.MathUtils.damp(this.glidePitch, targetGlidePitch, 3.2, delta)

    const currentClip = this.animation?.getCurrentName()
    const rootPose = currentClip ? this.getPoseAdjustment(currentClip, 'root') : ZERO_POSE_ADJUSTMENT
    const targetPresentationYaw = THREE.MathUtils.degToRad(rootPose.yawDeg)
    const targetPresentationPitch = THREE.MathUtils.degToRad(rootPose.pitchDeg)
    this.presentationYaw = THREE.MathUtils.damp(
      this.presentationYaw,
      targetPresentationYaw,
      4.0,
      delta
    )
    this.presentationPitch = THREE.MathUtils.damp(
      this.presentationPitch,
      targetPresentationPitch,
      4.0,
      delta
    )

    avatarRoot.position.set(
      this.avatarBasePosition.x + sideDrift + this.separationOffset.x,
      this.avatarBasePosition.y + lift + primary + secondary,
      this.avatarBasePosition.z + depthDrift + this.separationOffset.z
    )
    avatarRoot.rotation.set(
      this.avatarBaseRotation.x
        + Math.sin(this.floatElapsed * 0.37 + this.floatPhase) * profile.tilt * 0.52
        + this.glidePitch
        + this.presentationPitch,
      this.avatarBaseRotation.y + this.presentationYaw,
      this.avatarBaseRotation.z
        + Math.sin(this.floatElapsed * 0.46 + this.floatPhase * 0.9) * profile.tilt,
      this.avatarBaseRotation.order
    )
  }

  private updateSeparationOffset(delta: number): void {
    this.separationOffset.x = THREE.MathUtils.damp(
      this.separationOffset.x,
      this.separationTarget.x,
      5.2,
      Math.max(0, delta)
    )
    this.separationOffset.z = THREE.MathUtils.damp(
      this.separationOffset.z,
      this.separationTarget.z,
      5.2,
      Math.max(0, delta)
    )
  }

  private beginStateTransition(
    clip: AnimationClipName,
    targetState: StateTransition['targetState'],
    timing: { readonly delay: number; readonly blend: number }
  ): void {
    this.transition = {
      clip,
      targetState,
      durationSec: timing.blend,
      blendSec: timing.blend,
      fromLift: this.getCurrentLift(),
      fromProfile: this.getCurrentFloatProfile(),
      elapsedSec: 0
    }
    this.motionState = 'settling'
    this.setBlinkHeldForClip(clip)
    this.animation?.crossFade(clip, timing.blend)
  }

  private updateStateTransition(delta: number): void {
    if (!this.transition) {
      return
    }

    this.transition.elapsedSec += Math.max(0, delta)
    if (this.transition.elapsedSec < this.transition.durationSec) {
      return
    }

    const completed = this.transition
    this.transition = null
    this.motionState = completed.targetState
  }

  private getCurrentFloatProfile(): FloatProfile {
    if (this.transition) {
      const progress = this.transition.durationSec <= 0
        ? 1
        : THREE.MathUtils.clamp(this.transition.elapsedSec / this.transition.durationSec, 0, 1)
      const eased = progress * progress * (3 - 2 * progress)
      return blendFloatProfile(
        this.transition.fromProfile,
        this.getTransitionTargetFloatProfile(this.transition),
        eased
      )
    }

    const state = this.motionState === 'settling' ? 'idle' : this.motionState
    if (state === 'afk' && GROUNDING_AFK_CLIPS.has(this.animation?.getCurrentName() ?? 'stand')) {
      return AFK_GROUNDED_FLOAT_PROFILE
    }
    if (state === 'moving' && this.movement.locomotionMedium === 'seabed') {
      return this.root.position.y <= SEABED_EPSILON
        ? SEABED_FLOAT_PROFILE
        : FLOAT_PROFILE.idle
    }
    return FLOAT_PROFILE[state]
  }

  private getCurrentLift(): number {
    if (!this.transition) {
      return this.getCurrentFloatProfile().lift
    }

    const progress = this.transition.durationSec <= 0
      ? 1
      : THREE.MathUtils.clamp(
          this.transition.elapsedSec / this.transition.durationSec,
          0,
          1
        )
    const eased = progress * progress * (3 - 2 * progress)
    return THREE.MathUtils.lerp(
      this.transition.fromLift,
      this.getTransitionTargetFloatProfile(this.transition).lift,
      eased
    )
  }

  private getTransitionTargetFloatProfile(transition: StateTransition): FloatProfile {
    return GROUNDING_AFK_CLIPS.has(transition.clip)
      ? AFK_GROUNDED_FLOAT_PROFILE
      : FLOAT_PROFILE[transition.targetState]
  }

  private getLoadedAfkClipNames(): AnimationClipName[] {
    return Array.from(
      { length: this.animationUrls.afk.length },
      (_, index) => `afk-${index}` as const
    ).filter((name) => !RETIRED_AFK_CLIPS.has(name) && this.isAnimationLoaded(name))
  }

  private startAfkClip(nextAfkClipName: AnimationClipName): boolean {
    if (!this.animation || RETIRED_AFK_CLIPS.has(nextAfkClipName)) {
      return false
    }

    if (!this.isAnimationLoaded(nextAfkClipName)) {
      return false
    }

    const needsHoverRecovery = this.needsHoverRecovery()
    this.legSwingEnabled = false
    this.proximityMode = 'natural'
    this.cancelDeferredMotion()
    this.movement.cancel()
    this.pendingSleep = false
    this.sleepDescent = null
    this.afkGroundDescent = null
    this.afkGrounded = false

    if (GROUNDING_AFK_CLIPS.has(nextAfkClipName)) {
      this.hoverRecoveryTargetY = null
      const target = this.root.position.clone()
      target.y = 0
      this.afkGrounded = true
      this.afkGroundDescent = {
        start: this.root.position.clone(),
        target,
        durationSec: AFK_GROUND_DESCENT_DURATION_SEC,
        elapsedSec: 0
      }
    } else if (needsHoverRecovery) {
      this.prepareHoverHeight()
    }

    this.beginStateTransition(nextAfkClipName, 'afk', TRANSITION_TIMING.afk)
    this.lastAfkClipName = nextAfkClipName
    return true
  }

  private startBackgroundAnimationLoading(
    generation: number,
    animation: AnimationControllerPort
  ): void {
    void Promise.allSettled([
      this.ensureAnimationLoaded('walk', generation, animation),
      this.ensureAnimationLoaded('sleep', generation, animation)
    ])

    void (async () => {
      const afkEntries = this.animationUrls.afk
        .map((url, index) => [`afk-${index}` as const, url] as const)
        .filter(([name]) => !RETIRED_AFK_CLIPS.has(name))
      for (let index = 0; index < afkEntries.length; index += 3) {
        if (generation !== this.loadGeneration || animation !== this.animation) {
          return
        }
        const batch = afkEntries.slice(index, index + 3)
        await Promise.allSettled(
          batch.map(([name]) => this.ensureAnimationLoaded(name, generation, animation))
        )
      }
    })()
  }

  private ensureAnimationLoaded(
    name: AnimationClipName,
    generation: number,
    animation: AnimationControllerPort
  ): Promise<void> {
    if (generation !== this.loadGeneration || animation !== this.animation) {
      return Promise.resolve()
    }
    if (this.loadedAnimationClips.has(name)) {
      return Promise.resolve()
    }

    const existing = this.animationLoads.get(name)
    if (existing) {
      return existing
    }

    const url = getAnimationUrl(this.animationUrls, name)
    const load = GROUNDING_AFK_CLIPS.has(name)
      ? animation.load(name, url, { preserveAuthoredHipsHeight: true })
      : animation.load(name, url)
    const trackedLoad = load.then(() => {
      if (generation === this.loadGeneration && animation === this.animation) {
        this.loadedAnimationClips.add(name)
      }
    }).finally(() => {
      if (this.animationLoads.get(name) === trackedLoad) {
        this.animationLoads.delete(name)
      }
    })
    this.animationLoads.set(name, trackedLoad)
    return trackedLoad
  }

  private isAnimationLoaded(name: AnimationClipName): boolean {
    return this.loadedAnimationClips.has(name)
  }

  private deferMotionUntilLoaded(
    names: readonly AnimationClipName[],
    action: () => void
  ): void {
    const animation = this.animation
    if (!animation) {
      return
    }

    const generation = this.loadGeneration
    const requestSerial = ++this.motionRequestSerial
    void Promise.all(
      names.map((name) => this.ensureAnimationLoaded(name, generation, animation))
    ).then(() => {
      if (
        generation !== this.loadGeneration
        || animation !== this.animation
        || requestSerial !== this.motionRequestSerial
      ) {
        return
      }
      action()
    }).catch(() => undefined)
  }

  private cancelDeferredMotion(): void {
    this.motionRequestSerial += 1
  }

  private startSleepPresentation(): void {
    this.proximityMode = 'natural'
    this.cancelDeferredMotion()
    this.movement.cancel()
    this.hoverRecoveryTargetY = null
    this.legSwingEnabled = false
    this.afkGroundDescent = null
    this.afkGrounded = false
    this.transition = null
    this.pendingSleep = true
    this.sleepDescent = {
      start: this.root.position.clone(),
      target: this.createSafeSleepTarget(),
      durationSec: SLEEP_DESCENT_DURATION_SEC,
      elapsedSec: 0
    }

    // Sleep is one continuous presentation: begin folding into the authored
    // sleeping pose while drifting toward the sand. Switching only after the
    // translation finishes creates a visible stand-slide and arrival snap.
    this.beginStateTransition('sleep', 'sleep', TRANSITION_TIMING.sleep)
  }

  private updateSleepDescent(delta: number): void {
    if (!this.sleepDescent) {
      return
    }

    this.sleepDescent.elapsedSec += Math.max(0, delta)
    const progress = THREE.MathUtils.clamp(
      this.sleepDescent.elapsedSec / this.sleepDescent.durationSec,
      0,
      1
    )
    const eased = smootherStep(progress)
    this.root.position.lerpVectors(this.sleepDescent.start, this.sleepDescent.target, eased)

    if (progress >= 1) {
      this.root.position.copy(this.sleepDescent.target)
      this.sleepDescent = null
      this.pendingSleep = false
    }
  }

  private updateAfkGroundDescent(delta: number): void {
    if (!this.afkGroundDescent) {
      return
    }

    this.afkGroundDescent.elapsedSec += Math.max(0, delta)
    const progress = THREE.MathUtils.clamp(
      this.afkGroundDescent.elapsedSec / this.afkGroundDescent.durationSec,
      0,
      1
    )
    this.root.position.lerpVectors(
      this.afkGroundDescent.start,
      this.afkGroundDescent.target,
      smootherStep(progress)
    )

    if (progress >= 1) {
      this.root.position.copy(this.afkGroundDescent.target)
      this.afkGroundDescent = null
      this.afkGrounded = true
    }
  }

  private needsHoverRecovery(): boolean {
    return this.pendingSleep
      || this.motionState === 'sleep'
      || this.transition?.targetState === 'sleep'
      || this.afkGrounded
      || this.afkGroundDescent !== null
      || this.hoverRecoveryTargetY !== null
      || this.root.position.y < DEFAULT_HOVER_ROOT_Y - 0.001
  }

  private setBlinkHeldForClip(clip: AnimationClipName): void {
    this.expression?.setBlinkHeld?.(AFK_CLOSED_EYES.has(clip))
  }

  private syncLookAtForPresentation(): void {
    const lookAt = this.vrm?.lookAt
    if (!lookAt) {
      return
    }

    // Head pose editing and VRM LookAt both drive the head. Never let them
    // fight each other; this also prevents large rotations on authored poses.
    const currentClip = this.animation?.getCurrentName()
    const headPose = currentClip ? this.getPoseAdjustment(currentClip, 'head') : ZERO_POSE_ADJUSTMENT
    lookAt.autoUpdate = !hasPoseAdjustment(headPose)
  }

  private createSafeSleepTarget(): THREE.Vector3 {
    const bounds = this.presentationBounds
    const width = Math.max(0, bounds.max.x - bounds.min.x)
    const depth = Math.max(0, bounds.max.z - bounds.min.z)
    const marginX = Math.min(0.42, width * 0.22)
    const marginZ = Math.min(0.18, depth * 0.18)
    const minX = bounds.min.x + marginX
    const maxX = bounds.max.x - marginX
    const minZ = bounds.min.z + marginZ
    const maxZ = bounds.max.z - marginZ

    return new THREE.Vector3(
      minX <= maxX ? THREE.MathUtils.clamp(this.root.position.x, minX, maxX) : (bounds.min.x + bounds.max.x) * 0.5,
      0,
      minZ <= maxZ ? THREE.MathUtils.clamp(this.root.position.z, minZ, maxZ) : (bounds.min.z + bounds.max.z) * 0.5
    )
  }

  private prepareHoverHeight(): void {
    this.hoverRecoveryTargetY = Math.max(DEFAULT_HOVER_ROOT_Y, this.root.position.y)
  }

  private updateHoverRecovery(delta: number): void {
    if (this.hoverRecoveryTargetY === null) {
      return
    }

    this.root.position.y = THREE.MathUtils.damp(
      this.root.position.y,
      this.hoverRecoveryTargetY,
      7,
      Math.max(0, delta)
    )
    if (Math.abs(this.root.position.y - this.hoverRecoveryTargetY) < 0.001) {
      this.root.position.y = this.hoverRecoveryTargetY
      this.hoverRecoveryTargetY = null
    }
  }

  private startLocomotionPresentation(medium: LocomotionMedium): void {
    const next = medium === 'seabed' ? 'walk' : 'stand'
    if (!this.isAnimationLoaded(next)) {
      return
    }
    if (this.animation?.getCurrentName() !== next) {
      this.setBlinkHeldForClip(next)
      this.animation?.crossFade(next, TRANSITION_TIMING.locomotionBlend)
    }
  }

  private getAnimationTimeScale(): number {
    if (this.motionState === 'moving') {
      return this.movement.locomotionMedium === 'seabed' ? 0.72 : 0.56
    }
    if (this.motionState === 'afk') {
      return 0.68
    }
    return this.motionState === 'sleep' ? 0.82 : 0.76
  }

  private getUnderwaterPose(): UnderwaterPose {
    if (this.transition?.targetState === 'sleep') {
      return 'idle'
    }
    if (this.motionState === 'moving') {
      return this.movement.locomotionMedium
    }
    if (this.motionState === 'afk' && this.afkGrounded) {
      return 'grounded-afk'
    }
    if (this.motionState === 'settling') {
      return 'idle'
    }
    return this.motionState
  }

  private placeOnGround(avatarRoot: THREE.Object3D): void {
    const bounds = new THREE.Box3().setFromObject(avatarRoot)
    const center = bounds.getCenter(new THREE.Vector3())

    avatarRoot.position.x -= center.x
    avatarRoot.position.y -= bounds.min.y
    avatarRoot.position.z -= center.z
  }
}

function createEmptyMotionPoseAdjustment(): MotionPoseAdjustment {
  return {
    root: { ...ZERO_POSE_ADJUSTMENT },
    head: { ...ZERO_POSE_ADJUSTMENT }
  }
}

function createDefaultPoseAdjustments(): Map<AnimationClipName, MotionPoseAdjustment> {
  const adjustments = new Map<AnimationClipName, MotionPoseAdjustment>()
  const set = (
    clip: AnimationClipName,
    root: PoseAdjustment,
    head: PoseAdjustment = ZERO_POSE_ADJUSTMENT
  ): void => {
    adjustments.set(clip, {
      root: { ...root },
      head: { ...head }
    })
  }

  set('afk-0', { pitchDeg: 10.85, yawDeg: 64.4 })
  set('afk-1', { pitchDeg: 10.85, yawDeg: 10.85 })
  set('afk-2', { pitchDeg: 0.35, yawDeg: 14.35 })
  set('afk-3', { pitchDeg: 10.5, yawDeg: 17.5 }, { pitchDeg: -2.45, yawDeg: 1.75 })
  set(AFK_GROUNDED, { pitchDeg: 9.2, yawDeg: 1.9 }, { pitchDeg: -50.15, yawDeg: -12.25 })
  set(AFK_REORIENTED, { pitchDeg: 26.1, yawDeg: 60.6 }, { pitchDeg: -15.05, yawDeg: 24.85 })
  set(AFK_GROUNDED_09, { pitchDeg: 9.45, yawDeg: -4.9 }, { pitchDeg: -9.45, yawDeg: 1.05 })
  set('sleep', { pitchDeg: 7.35, yawDeg: 65.8 })

  return adjustments
}

function createMotionPoseOptions(afkCount: number): readonly MotionPoseOption[] {
  return [
    { clip: 'stand', label: 'Stand' },
    ...Array.from({ length: afkCount }, (_, index) => ({
      clip: `afk-${index}` as const,
      label: `AFK-${String(index + 1).padStart(2, '0')}`
    })).filter((option) => !RETIRED_AFK_CLIPS.has(option.clip)),
    { clip: 'sleep', label: 'Sleep' }
  ]
}

function hasPoseAdjustment(value: PoseAdjustment): boolean {
  return Math.abs(value.pitchDeg) > 0.0001 || Math.abs(value.yawDeg) > 0.0001
}

function cloneBounds(bounds: SwimBounds): SwimBounds {
  return { min: bounds.min.clone(), max: bounds.max.clone() }
}

function smootherStep(value: number): number {
  return value * value * value * (value * (value * 6 - 15) + 10)
}

function normalizeDegrees(value: number): number {
  const wrapped = ((value + 180) % 360 + 360) % 360 - 180
  return Math.abs(wrapped + 180) < 1e-6 ? 180 : wrapped
}

function blendFloatProfile(from: FloatProfile, to: FloatProfile, t: number): FloatProfile {
  return {
    lift: THREE.MathUtils.lerp(from.lift, to.lift, t),
    primary: THREE.MathUtils.lerp(from.primary, to.primary, t),
    secondary: THREE.MathUtils.lerp(from.secondary, to.secondary, t),
    drift: THREE.MathUtils.lerp(from.drift, to.drift, t),
    tilt: THREE.MathUtils.lerp(from.tilt, to.tilt, t)
  }
}

function createResidentPhase(name: string): number {
  let hash = 2166136261
  for (let index = 0; index < name.length; index += 1) {
    hash ^= name.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return ((hash >>> 0) / 0xffffffff) * Math.PI * 2
}

function createDefaultAnimationUrls(): ResidentAnimationUrls {
  const resolveAnimation = (filename: string): string =>
    new URL(`${import.meta.env.BASE_URL}animations/${filename}`, window.location.href).href

  return {
    stand: resolveAnimation('stand.vrma'),
    walk: resolveAnimation('walk.vrma'),
    afk: Array.from({ length: 9 }, (_, index) =>
      resolveAnimation(`afk-${String(index + 1).padStart(2, '0')}.vrma`)
    ),
    sleep: resolveAnimation('sleep.vrma')
  }
}

function getAnimationUrl(urls: ResidentAnimationUrls, name: AnimationClipName): string {
  if (name === 'stand') return urls.stand
  if (name === 'walk') return urls.walk
  if (name === 'sleep') return urls.sleep

  const index = Number(name.slice('afk-'.length))
  const url = urls.afk[index]
  if (!url) {
    throw new Error(`Animation URL is not configured: ${name}`)
  }
  return url
}

function pickAfkClipName(
  candidates: readonly AnimationClipName[],
  previous: AnimationClipName | null,
  random: () => number
): AnimationClipName | null {
  const eligible = candidates.length > 1
    ? candidates.filter((name) => name !== previous)
    : candidates

  if (eligible.length === 0) {
    return null
  }

  const index = Math.min(eligible.length - 1, Math.floor(random() * eligible.length))
  return eligible[Math.max(0, index)]
}
