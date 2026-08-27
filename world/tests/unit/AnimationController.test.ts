import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import type { VRM } from '@pixiv/three-vrm'
import { AnimationController } from '../../src/renderer/src/world/vrm/AnimationController'

describe('AnimationController', () => {
  it('loads stand and plays it as a repeating animation', async () => {
    const root = new THREE.Group()
    const mixer = new THREE.AnimationMixer(root)
    const clip = new THREE.AnimationClip('stand', 1, [])
    const controller = new AnimationController(
      mixer,
      { scene: root } as VRM,
      async () => clip
    )

    await controller.load('stand', '/animations/stand.vrma')
    expect(controller.getCurrentName()).toBeNull()
    controller.play('stand')
    controller.update(0.1)

    const action = mixer.existingAction(clip)
    expect(action).not.toBeNull()
    expect(action?.loop).toBe(THREE.LoopRepeat)
    expect(action?.isRunning()).toBe(true)
    expect(controller.getCurrentName()).toBe('stand')

    const timeBeforeSelfTransition = action?.time
    controller.crossFade('stand', 0.8)
    expect(action?.time).toBe(timeBeforeSelfTransition)
  })

  it('restarts a reused action from its first frame before crossfading back to it', async () => {
    const root = new THREE.Group()
    const mixer = new THREE.AnimationMixer(root)
    const stand = new THREE.AnimationClip('stand', 2, [])
    const afk = new THREE.AnimationClip('afk-0', 2, [])
    const controller = new AnimationController(
      mixer,
      { scene: root } as VRM,
      async (url) => url.includes('stand') ? stand : afk
    )

    await controller.load('stand', '/animations/stand.vrma')
    await controller.load('afk-0', '/animations/afk-01.vrma')
    controller.play('stand')
    controller.update(0.6)
    const standAction = mixer.existingAction(stand)
    expect(standAction?.time).toBeGreaterThan(0.5)

    controller.crossFade('afk-0', 0.2)
    controller.update(0.3)
    controller.crossFade('stand', 0.2)
    expect(standAction?.time).toBe(0)
  })

  it('keeps interrupted Stand -> AFK -> Move blends normalized instead of leaking a stale action', async () => {
    const root = new THREE.Group()
    const mixer = new THREE.AnimationMixer(root)
    const stand = new THREE.AnimationClip('stand', 2, [])
    const afk = new THREE.AnimationClip('afk-0', 2, [])
    const walk = new THREE.AnimationClip('walk', 2, [])
    const controller = new AnimationController(
      mixer,
      { scene: root } as VRM,
      async (url) => url.includes('stand') ? stand : url.includes('afk') ? afk : walk
    )

    await controller.load('stand', '/animations/stand.vrma')
    await controller.load('afk-0', '/animations/afk-01.vrma')
    await controller.load('walk', '/animations/walk.vrma')
    controller.play('stand')
    controller.update(0.3)

    controller.crossFade('afk-0', 1.35)
    controller.update(0.27)
    const standAction = mixer.existingAction(stand)!
    const afkAction = mixer.existingAction(afk)!
    expect(standAction.getEffectiveWeight() + afkAction.getEffectiveWeight()).toBeCloseTo(1, 5)
    expect(standAction.getEffectiveWeight()).toBeGreaterThan(0)
    expect(afkAction.getEffectiveWeight()).toBeGreaterThan(0)

    controller.crossFade('walk', 0.8)
    const walkAction = mixer.existingAction(walk)!
    controller.update(0)
    expect(
      standAction.getEffectiveWeight()
      + afkAction.getEffectiveWeight()
      + walkAction.getEffectiveWeight()
    ).toBeCloseTo(1, 5)

    controller.update(0.4)
    expect(
      standAction.getEffectiveWeight()
      + afkAction.getEffectiveWeight()
      + walkAction.getEffectiveWeight()
    ).toBeCloseTo(1, 5)
    expect(walkAction.getEffectiveWeight()).toBeGreaterThan(0.45)
    expect(walkAction.getEffectiveWeight()).toBeLessThan(0.55)
  })

  it('preserves authored hips height for grounded AFKs while rebasing X/Z', async () => {
    const root = new THREE.Group()
    const hips = new THREE.Group()
    hips.name = 'NormalizedHips'
    root.add(hips)
    const mixer = new THREE.AnimationMixer(root)
    const clip = new THREE.AnimationClip('afk-4', 1, [
      new THREE.VectorKeyframeTrack(
        'NormalizedHips.position',
        [0, 1],
        [0.02, 0.6, -0.04, 0.03, 0.7, -0.02]
      )
    ])
    const vrm = {
      scene: root,
      humanoid: {
        normalizedRestPose: {
          hips: { position: [0, 1, 0] }
        }
      }
    } as unknown as VRM
    const controller = new AnimationController(mixer, vrm, async () => clip)

    await controller.load('afk-4', '/animations/afk-05.vrma', {
      preserveAuthoredHipsHeight: true
    })
    controller.play('afk-4')
    controller.update(0.25)

    expect(hips.position.x).toBeCloseTo(0.0025, 3)
    expect(hips.position.y).toBeCloseTo(0.625, 3)
    expect(hips.position.z).toBeCloseTo(0.005, 3)
  })

  it('rebases only hips translation and leaves other authored position tracks intact', async () => {
    const root = new THREE.Group()
    const hips = new THREE.Group()
    hips.name = 'NormalizedHips'
    hips.position.set(0, 1, 0)
    const hand = new THREE.Group()
    hand.name = 'NormalizedLeftHand'
    root.add(hips, hand)
    const mixer = new THREE.AnimationMixer(root)
    const clip = new THREE.AnimationClip('stand', 1, [
      new THREE.VectorKeyframeTrack(
        'NormalizedHips.position',
        [0, 1],
        [0.02, 0.86, -0.04, 0.03, 0.96, -0.02]
      ),
      new THREE.VectorKeyframeTrack(
        'NormalizedLeftHand.position',
        [0, 1],
        [0.4, 0.2, 0.1, 0.6, 0.4, 0.3]
      )
    ])
    const vrm = {
      scene: root,
      humanoid: {
        normalizedRestPose: {
          hips: { position: [0, 1, 0] }
        }
      }
    } as unknown as VRM
    const controller = new AnimationController(mixer, vrm, async () => clip)

    await controller.load('stand', '/animations/stand.vrma')
    controller.play('stand')
    controller.update(0)
    expect(hips.position.toArray()).toEqual([0, 1, 0])

    controller.update(0.5)
    expect(hips.position.y).toBeCloseTo(1.05)
    expect(hips.position.z).toBeCloseTo(0.01)
    expect(hand.position.x).toBeCloseTo(0.5)
    expect(hand.position.y).toBeCloseTo(0.3)
    expect(hand.position.z).toBeCloseTo(0.2)
  })
})
