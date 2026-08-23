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
  })
})
