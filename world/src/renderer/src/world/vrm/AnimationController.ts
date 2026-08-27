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

export class AnimationController {
  private readonly clips = new Map<AnimationClipName, THREE.AnimationClip>()
  private readonly actions = new Map<AnimationClipName, THREE.AnimationAction>()
  private currentName: AnimationClipName | null = null

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

    const previousAction = this.currentName ? this.actions.get(this.currentName) : undefined
    const action = this.actions.get(name) ?? this.mixer.clipAction(clip)
    this.actions.set(name, action)

    if (previousAction && previousAction !== action) {
      previousAction.fadeOut(0.2)
    }

    action.reset()
    action.enabled = true
    action.clampWhenFinished = false
    action.setLoop(THREE.LoopRepeat, Number.POSITIVE_INFINITY)
    action.fadeIn(0.2)
    action.play()
    this.currentName = name
  }

  stop(name?: AnimationClipName): void {
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

    const existingNextAction = this.actions.get(next)
    const nextAction = existingNextAction ?? this.mixer.clipAction(nextClip)
    const currentAction = this.currentName ? this.actions.get(this.currentName) : undefined
    this.actions.set(next, nextAction)

    // Always restart the incoming clip from its authored first frame. Reusing
    // an old action at an arbitrary playback time makes transitions depend on
    // what happened several states ago and can produce a visible pose snap.
    nextAction.reset()
    nextAction.enabled = true
    nextAction.clampWhenFinished = false
    nextAction.setLoop(THREE.LoopRepeat, Number.POSITIVE_INFINITY).play()

    if (currentAction && currentAction !== nextAction) {
      currentAction.crossFadeTo(nextAction, Math.max(0, durationSec), false)
    }

    this.currentName = next
  }

  update(delta: number): void {
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
