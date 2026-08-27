import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import type { VRM } from '@pixiv/three-vrm'
import {
  createVRMAnimationClip,
  VRMAnimation,
  VRMAnimationLoaderPlugin
} from '@pixiv/three-vrm-animation'

export type AnimationName =
  | 'stand'
  | 'afk'
  | 'sleep'

// `walk` is an internal clip used only by the preserved former Move B
// presentation. It is deliberately not a public AnimationName/action.
export type AnimationClipName = Exclude<AnimationName, 'afk'> | 'walk' | `afk-${number}`

export interface AnimationLoadOptions {
  readonly preserveAuthoredHipsHeight?: boolean
}

export type AnimationClipLoader = (url: string, vrm: VRM) => Promise<THREE.AnimationClip>

interface WeightTransitionEntry {
  readonly action: THREE.AnimationAction
  readonly from: number
  readonly to: number
}

interface WeightTransition {
  readonly durationSec: number
  readonly entries: readonly WeightTransitionEntry[]
  elapsedSec: number
}

export class AnimationController {
  private readonly clips = new Map<AnimationClipName, THREE.AnimationClip>()
  private readonly actions = new Map<AnimationClipName, THREE.AnimationAction>()
  private currentName: AnimationClipName | null = null
  private weightTransition: WeightTransition | null = null

  constructor(
    private readonly mixer: THREE.AnimationMixer,
    private readonly vrm: VRM,
    private readonly loadClip: AnimationClipLoader = loadVrmAnimationClip
  ) {}

  getCurrentName(): AnimationClipName | null {
    return this.currentName
  }

  async load(
    name: AnimationClipName,
    url: string,
    options: AnimationLoadOptions = {}
  ): Promise<void> {
    const loadedClip = await this.loadClip(url, this.vrm)
    const clip = stabilizeHumanoidTranslation(name, loadedClip, this.vrm, options)
    this.clips.set(name, clip)
  }

  play(name: AnimationClipName): void {
    if (this.currentName === name) {
      return
    }

    const clip = this.clips.get(name)

    if (!clip) {
      throw new Error(`Animation is not loaded: ${name}`)
    }

    this.weightTransition = null
    for (const existing of this.actions.values()) {
      existing.stopFading()
      existing.stop()
    }

    const action = this.actions.get(name) ?? this.mixer.clipAction(clip)
    this.actions.set(name, action)
    action.reset()
    action.enabled = true
    action.clampWhenFinished = false
    action.setLoop(THREE.LoopRepeat, Number.POSITIVE_INFINITY)
    action.setEffectiveWeight(1)
    action.play()
    this.currentName = name
  }

  stop(name?: AnimationClipName): void {
    this.weightTransition = null
    if (name) {
      this.actions.get(name)?.stop()
      if (this.currentName === name) {
        this.currentName = null
      }
      return
    }

    this.mixer.stopAllAction()
    this.currentName = null
  }

  crossFade(next: AnimationClipName, durationSec: number): void {
    if (this.currentName === next) {
      return
    }

    const nextClip = this.clips.get(next)

    if (!nextClip) {
      throw new Error(`Animation is not loaded: ${next}`)
    }

    const nextAction = this.actions.get(next) ?? this.mixer.clipAction(nextClip)
    this.actions.set(next, nextAction)
    const fadeDuration = Math.max(0, durationSec)

    // Read every currently contributing action before changing any weight.
    // Rapid Stand/Walk reversals can return to an action that is still fading
    // out. Restarting that action with AnimationAction.fadeIn() discards its
    // current contribution and can make the total animation weight collapse
    // toward zero for a frame, visibly exposing the VRM bind/T pose.
    const scheduled = Array.from(this.actions.values()).filter((action) => action.isScheduled())
    const currentWeights = new Map<THREE.AnimationAction, number>()
    let totalWeight = 0
    for (const action of scheduled) {
      action.stopFading()
      const weight = Math.max(0, action.getEffectiveWeight())
      currentWeights.set(action, weight)
      totalWeight += weight
    }

    const nextWasContributing = (currentWeights.get(nextAction) ?? 0) > 0.00001
    if (!nextAction.isScheduled()) {
      nextAction.reset()
      nextAction.enabled = true
      nextAction.clampWhenFinished = false
      nextAction.setLoop(THREE.LoopRepeat, Number.POSITIVE_INFINITY).play()
    } else if (!nextWasContributing) {
      // A fully faded reused action should restart from its authored first
      // frame; a still-contributing action keeps its current playback time so
      // a rapid reversal remains continuous.
      nextAction.reset()
      nextAction.enabled = true
      nextAction.clampWhenFinished = false
      nextAction.setLoop(THREE.LoopRepeat, Number.POSITIVE_INFINITY).play()
    }

    if (!currentWeights.has(nextAction)) {
      currentWeights.set(nextAction, 0)
    }

    // Repair any already-degraded state before beginning the next blend. This
    // is also defensive against external mixer changes: the transition always
    // starts from a normalized weight vector whose sum is exactly one.
    if (totalWeight <= 0.00001) {
      for (const action of currentWeights.keys()) {
        currentWeights.set(action, action === nextAction ? 1 : 0)
      }
      totalWeight = 1
    } else {
      for (const [action, weight] of currentWeights) {
        currentWeights.set(action, weight / totalWeight)
      }
    }

    const entries: WeightTransitionEntry[] = []
    for (const [action, from] of currentWeights) {
      action.stopFading()
      action.enabled = true
      action.setEffectiveWeight(from)
      entries.push({ action, from, to: action === nextAction ? 1 : 0 })
    }

    if (fadeDuration <= 0) {
      for (const entry of entries) {
        entry.action.setEffectiveWeight(entry.to)
        if (entry.to <= 0) entry.action.stop()
      }
      this.weightTransition = null
    } else {
      this.weightTransition = {
        durationSec: fadeDuration,
        entries,
        elapsedSec: 0
      }
    }

    this.currentName = next
  }

  update(delta: number): void {
    if (this.weightTransition) {
      const transition = this.weightTransition
      transition.elapsedSec += Math.max(0, delta)
      const progress = THREE.MathUtils.clamp(
        transition.elapsedSec / transition.durationSec,
        0,
        1
      )
      for (const entry of transition.entries) {
        entry.action.setEffectiveWeight(THREE.MathUtils.lerp(entry.from, entry.to, progress))
      }
      if (progress >= 1) {
        for (const entry of transition.entries) {
          if (entry.to <= 0) entry.action.stop()
        }
        this.weightTransition = null
      }
    }

    this.mixer.update(delta)
  }

  dispose(): void {
    this.stop()
    for (const clip of this.clips.values()) {
      this.mixer.uncacheClip(clip)
    }
    this.clips.clear()
    this.actions.clear()
  }
}

function stabilizeHumanoidTranslation(
  name: AnimationClipName,
  source: THREE.AnimationClip,
  vrm: VRM,
  options: AnimationLoadOptions
): THREE.AnimationClip {
  if (name === 'sleep') {
    return source
  }

  const preserveAuthoredHipsHeight = options.preserveAuthoredHipsHeight === true

  const restHipsPosition = vrm.humanoid?.normalizedRestPose.hips?.position
  if (!restHipsPosition) {
    return source
  }

  const clip = source.clone()
  clip.tracks = clip.tracks.map((track) => {
    if (
      !(track instanceof THREE.VectorKeyframeTrack)
      || !track.name.endsWith('NormalizedHips.position')
    ) {
      return track
    }

    const stabilized = track.clone()
    const values = stabilized.values
    const offsetX = restHipsPosition[0] - values[0]
    const offsetY = preserveAuthoredHipsHeight ? 0 : restHipsPosition[1] - values[1]
    const offsetZ = restHipsPosition[2] - values[2]
    for (let index = 0; index < values.length; index += 3) {
      values[index] += offsetX
      values[index + 1] += offsetY
      values[index + 2] += offsetZ
    }
    return stabilized
  })
  return clip
}

async function loadVrmAnimationClip(url: string, vrm: VRM): Promise<THREE.AnimationClip> {
  const loader = new GLTFLoader()
  loader.register((parser) => new VRMAnimationLoaderPlugin(parser))
  const gltf = await loader.loadAsync(url)
  const animations = gltf.userData.vrmAnimations as VRMAnimation[] | undefined
  const animation = animations?.[0]

  if (!animation) {
    throw new Error(`VRMA contains no animation: ${url}`)
  }

  return createVRMAnimationClip(animation, vrm)
}
