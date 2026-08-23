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
  | 'walk'
  | 'afk'
  | 'sleep'

export type AnimationClipName = Exclude<AnimationName, 'afk'> | `afk-${number}`

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

  async load(name: AnimationClipName, url: string): Promise<void> {
    const clip = await this.loadClip(url, this.vrm)
    this.clips.set(name, clip)
  }

  play(name: AnimationClipName): void {
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
    const nextClip = this.clips.get(next)

    if (!nextClip) {
      throw new Error(`Animation is not loaded: ${next}`)
    }

    const nextAction = this.actions.get(next) ?? this.mixer.clipAction(nextClip)
    const currentAction = this.currentName ? this.actions.get(this.currentName) : undefined
    this.actions.set(next, nextAction)
    nextAction.reset().setLoop(THREE.LoopRepeat, Number.POSITIVE_INFINITY).play()

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
