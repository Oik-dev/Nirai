import * as THREE from 'three'
import type { VRM } from '@pixiv/three-vrm'
import { LoadedVrm, VrmLoader } from './vrm/VrmLoader'
import {
  AnimationClipName,
  AnimationController,
  AnimationName
} from './vrm/AnimationController'
import { EmotionName, ExpressionController } from './vrm/ExpressionController'
import { MovementController, type SwimBounds } from './MovementController'

type ResidentMotionState = 'idle' | 'moving' | 'settling' | 'afk' | 'sleep'

interface StateTransition {
  readonly clip: AnimationClipName
  readonly targetState: Exclude<ResidentMotionState, 'moving' | 'settling'>
  readonly durationSec: number
  readonly blendSec: number
  readonly fromLift: number
  elapsedSec: number
}

const TRANSITION_TIMING = {
  stand: { delay: 1.5, blend: 0.9 },
  afk: { delay: 2.6, blend: 1.35 },
  sleep: { delay: 3.4, blend: 1.8 },
  walkBlend: 0.8
} as const

const FLOAT_PROFILE: Readonly<Record<Exclude<ResidentMotionState, 'settling'>, {
  lift: number
  primary: number
  secondary: number
  drift: number
  tilt: number
}>> = {
  idle: { lift: 0.105, primary: 0.030, secondary: 0.014, drift: 0.010, tilt: 0.014 },
  moving: { lift: 0.080, primary: 0.022, secondary: 0.010, drift: 0.008, tilt: 0.011 },
  afk: { lift: 0.155, primary: 0.042, secondary: 0.018, drift: 0.014, tilt: 0.018 },
  sleep: { lift: 0, primary: 0.003, secondary: 0.002, drift: 0.002, tilt: 0.003 }
}

const DEFAULT_HOVER_ROOT_Y = 0.32
const SLEEP_SAFE_X = 1.65
const SLEEP_SAFE_Z_MIN = -0.72
const SLEEP_SAFE_Z_MAX = 0.18

export interface VrmLoaderPort {
  load(bytes: Uint8Array): Promise<LoadedVrm>
  update(loaded: LoadedVrm, delta: number): void
  unload(loaded: LoadedVrm): void
}

export type AvatarReader = (relativePath: string) => Promise<Uint8Array>

export interface AnimationControllerPort {
  load(name: AnimationClipName, url: string): Promise<void>
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
  triggerBlink(): void
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
        for (const [name, url] of createAnimationLoadEntries(this.animationUrls)) {
          await nextAnimation.load(name, url)
        }
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
    } catch (error) {
      if (generation !== this.loadGeneration) {
        return
      }

      throw error
    }
  }

  update(delta: number): void {
    if (this.loadedAvatar) {
      this.updateStateTransition(delta)
      this.movement.update(delta)
      this.updateFloat(delta)
      this.animation?.update(delta)
      this.expression?.update(delta)
      this.loader.update(this.loadedAvatar, delta)
    }
  }

  playAnimation(name: AnimationName): boolean {
    if (!this.animation) {
      return false
    }

    if (name === 'afk') {
      const nextAfkClipName = pickAfkClipName(
        this.animationUrls.afk.length,
        this.lastAfkClipName,
        this.random
      )

      if (!nextAfkClipName) {
        return false
      }

      this.prepareHoverHeight()
      this.beginStateTransition(nextAfkClipName, 'afk', TRANSITION_TIMING.afk)
      this.lastAfkClipName = nextAfkClipName
      return true
    }

    if (name === 'sleep') {
      this.movement.cancel()
      this.movement.moveTo(new THREE.Vector3(
        THREE.MathUtils.clamp(this.root.position.x, -SLEEP_SAFE_X, SLEEP_SAFE_X),
        0,
        THREE.MathUtils.clamp(this.root.position.z, SLEEP_SAFE_Z_MIN, SLEEP_SAFE_Z_MAX)
      ))
      this.beginStateTransition('sleep', 'sleep', TRANSITION_TIMING.sleep)
      return true
    }

    if (name === 'stand') {
      this.prepareHoverHeight()
      this.beginStateTransition('stand', 'idle', TRANSITION_TIMING.stand)
      return true
    }

    const isRecoveringFromSleep = this.motionState === 'sleep'
      || this.transition?.targetState === 'sleep'
    this.transition = null
    if (isRecoveringFromSleep) {
      this.prepareHoverHeight()
    }
    this.motionState = 'moving'
    this.animation.crossFade('walk', TRANSITION_TIMING.walkBlend)
    return true
  }

  setEmotion(name: EmotionName): boolean {
    if (!this.expression) {
      return false
    }

    this.expression.setEmotion(name)
    return true
  }

  triggerBlink(): boolean {
    if (!this.expression) {
      return false
    }

    this.expression.triggerBlink()
    return true
  }

  face(target: THREE.Object3D): boolean {
    if (!this.vrm?.lookAt) {
      return false
    }

    this.vrm.lookAt.autoUpdate = true
    this.vrm.lookAt.target = target
    return true
  }

  moveTo(target: THREE.Vector3, onArrive?: () => void): boolean {
    if (!this.animation) {
      return false
    }

    this.transition = null
    this.motionState = 'moving'
    this.animation.crossFade('walk', TRANSITION_TIMING.walkBlend)
    this.movement.moveTo(target, () => {
      this.beginStateTransition('stand', 'idle', TRANSITION_TIMING.stand)
      onArrive?.()
    })
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

    this.transition = null
    this.motionState = 'moving'
    this.animation.crossFade('walk', TRANSITION_TIMING.walkBlend)
    this.movement.swimNear(anchor, radius, bounds, () => {
      this.beginStateTransition('stand', 'idle', TRANSITION_TIMING.stand)
      onArrive?.()
    })
    return true
  }

  constrainHorizontal(bounds: SwimBounds): void {
    this.movement.constrainHorizontal(bounds)
  }

  dispose(): void {
    this.loadGeneration += 1
    this.movement.cancel()

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
    this.transition = null
    this.motionState = this.movement.isMoving ? 'moving' : 'idle'
    if (!this.movement.isMoving && this.root.position.y < DEFAULT_HOVER_ROOT_Y) {
      this.root.position.y = DEFAULT_HOVER_ROOT_Y
    }
    this.loadedAvatar = nextAvatar
    this.vrm = nextAvatar.vrm
    this.animation = nextAnimation
    this.expression = nextExpression
    this.root.add(nextAvatar.vrm.scene)
    nextAvatar.vrm.scene.visible = true
    nextAnimation.play(this.movement.isMoving ? 'walk' : 'stand')
    nextAnimation.update(0)
    nextExpression.update(0)
    this.updateFloat(0)
    this.loader.update(nextAvatar, 0)
  }

  private updateFloat(delta: number): void {
    if (!this.loadedAvatar) {
      return
    }

    this.floatElapsed += Math.max(0, delta)
    const avatarRoot = this.loadedAvatar.vrm.scene
    const profileState = this.transition?.targetState === 'sleep'
      ? 'idle'
      : this.transition?.targetState ?? (this.motionState === 'settling' ? 'idle' : this.motionState)
    const profile = FLOAT_PROFILE[profileState]
    const lift = this.getCurrentLift()
    const primary = Math.sin(this.floatElapsed * 1.03 + this.floatPhase) * profile.primary
    const secondary = Math.sin(this.floatElapsed * 0.37 + this.floatPhase * 1.7) * profile.secondary
    const sideDrift = Math.sin(this.floatElapsed * 0.51 + this.floatPhase * 0.73) * profile.drift
    const depthDrift = Math.cos(this.floatElapsed * 0.33 + this.floatPhase * 1.21) * profile.drift * 0.76

    avatarRoot.position.set(
      this.avatarBasePosition.x + sideDrift,
      this.avatarBasePosition.y + lift + primary + secondary,
      this.avatarBasePosition.z + depthDrift
    )
    avatarRoot.rotation.set(
      this.avatarBaseRotation.x
        + Math.sin(this.floatElapsed * 0.37 + this.floatPhase) * profile.tilt * 0.52,
      this.avatarBaseRotation.y,
      this.avatarBaseRotation.z
        + Math.sin(this.floatElapsed * 0.46 + this.floatPhase * 0.9) * profile.tilt,
      this.avatarBaseRotation.order
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
      durationSec: timing.delay,
      blendSec: timing.blend,
      fromLift: this.getCurrentLift(),
      elapsedSec: 0
    }
    this.motionState = 'settling'
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
    this.animation?.crossFade(completed.clip, completed.blendSec)
  }

  private getCurrentLift(): number {
    if (!this.transition) {
      const state = this.motionState === 'settling' ? 'idle' : this.motionState
      return FLOAT_PROFILE[state].lift
    }

    const progress = THREE.MathUtils.clamp(
      this.transition.elapsedSec / this.transition.durationSec,
      0,
      1
    )
    const eased = progress * progress * (3 - 2 * progress)
    return THREE.MathUtils.lerp(
      this.transition.fromLift,
      FLOAT_PROFILE[this.transition.targetState].lift,
      eased
    )
  }

  private prepareHoverHeight(): void {
    this.movement.settleAt(DEFAULT_HOVER_ROOT_Y, {
      min: new THREE.Vector3(-2.25, DEFAULT_HOVER_ROOT_Y, -1.35),
      max: new THREE.Vector3(2.25, 1.12, 0.55)
    })
  }

  private placeOnGround(avatarRoot: THREE.Object3D): void {
    const bounds = new THREE.Box3().setFromObject(avatarRoot)
    const center = bounds.getCenter(new THREE.Vector3())

    avatarRoot.position.x -= center.x
    avatarRoot.position.y -= bounds.min.y
    avatarRoot.position.z -= center.z
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

function createAnimationLoadEntries(
  urls: ResidentAnimationUrls
): ReadonlyArray<readonly [AnimationClipName, string]> {
  const entries: Array<readonly [AnimationClipName, string]> = [
    ['stand', urls.stand],
    ['walk', urls.walk]
  ]

  urls.afk.forEach((url, index) => entries.push([`afk-${index}`, url]))
  entries.push(['sleep', urls.sleep])
  return entries
}

function pickAfkClipName(
  candidateCount: number,
  previous: AnimationClipName | null,
  random: () => number
): AnimationClipName | null {
  const candidates = Array.from(
    { length: candidateCount },
    (_, index) => `afk-${index}` as const
  )
  const eligible = candidates.length > 1
    ? candidates.filter((name) => name !== previous)
    : candidates

  if (eligible.length === 0) {
    return null
  }

  const index = Math.min(eligible.length - 1, Math.floor(random() * eligible.length))
  return eligible[Math.max(0, index)]
}
