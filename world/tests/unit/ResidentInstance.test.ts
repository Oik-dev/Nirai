import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import type { VRM } from '@pixiv/three-vrm'
import type { LoadedVrm } from '../../src/renderer/src/world/vrm/VrmLoader'
import { ResidentInstance } from '../../src/renderer/src/world/ResidentInstance'

function createLoadedVrm(name: string): LoadedVrm {
  const scene = new THREE.Group()
  scene.name = name
  scene.add(new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.6, 0.4)))

  return {
    vrm: {
      scene,
      lookAt: {
        target: null,
        autoUpdate: false
      },
      update: vi.fn()
    } as unknown as VRM,
    mixer: {
      update: vi.fn(),
      stopAllAction: vi.fn(),
      uncacheRoot: vi.fn()
    } as unknown as THREE.AnimationMixer
  }
}

describe('ResidentInstance', () => {
  it('keeps only the newest avatar when an older load finishes last', async () => {
    const pending = new Map<string, (loaded: LoadedVrm) => void>()
    const loader = {
      load: vi.fn((bytes: Uint8Array) => {
        const key = new TextDecoder().decode(bytes)
        return new Promise<LoadedVrm>((resolve) => pending.set(key, resolve))
      }),
      update: vi.fn(),
      unload: vi.fn()
    }
    const readAvatar = vi.fn(async (path: string) => new TextEncoder().encode(path))
    const animations: Array<{
      load: ReturnType<typeof vi.fn>
      play: ReturnType<typeof vi.fn>
      crossFade: ReturnType<typeof vi.fn>
      getCurrentName: ReturnType<typeof vi.fn>
      update: ReturnType<typeof vi.fn>
      dispose: ReturnType<typeof vi.fn>
    }> = []
    const createAnimation = vi.fn(() => {
      const animation = {
        load: vi.fn(async () => undefined),
        play: vi.fn(),
        crossFade: vi.fn(),
        getCurrentName: vi.fn(() => null),
        update: vi.fn(),
        dispose: vi.fn()
      }
      animations.push(animation)
      return animation
    })
    const expression = {
      setEmotion: vi.fn(),
      triggerBlink: vi.fn(),
      update: vi.fn(),
      dispose: vi.fn()
    }
    const createExpression = vi.fn(() => expression)
    const resident = new ResidentInstance(
      'Lapan',
      loader,
      readAvatar,
      createAnimation,
      {
        stand: '/animations/stand.vrma',
        walk: '/animations/walk.vrma',
        afk: [
          '/animations/afk-01.vrma',
          '/animations/afk-02.vrma',
          '/animations/afk-03.vrma'
        ],
        sleep: '/animations/sleep.vrma'
      },
      createExpression,
      () => 0
    )

    const olderLoad = resident.loadAvatar('older.vrm')
    await vi.waitFor(() => expect(loader.load).toHaveBeenCalledTimes(1))
    const newestLoad = resident.loadAvatar('newest.vrm')
    await vi.waitFor(() => expect(loader.load).toHaveBeenCalledTimes(2))

    const newest = createLoadedVrm('newest')
    pending.get('newest.vrm')?.(newest)
    await newestLoad

    const older = createLoadedVrm('older')
    pending.get('older.vrm')?.(older)
    await olderLoad

    expect(resident.vrm).toBe(newest.vrm)
    expect(resident.root.children).toEqual([newest.vrm.scene])
    expect(loader.unload).toHaveBeenCalledWith(older)
    expect(animations[0].play).toHaveBeenCalledWith('stand')
    expect(animations[0].update).toHaveBeenCalledWith(0)
    expect(loader.update).toHaveBeenCalledWith(newest, 0)
    expect(animations[0].load).toHaveBeenNthCalledWith(1, 'stand', '/animations/stand.vrma')
    expect(animations[0].load).toHaveBeenNthCalledWith(2, 'walk', '/animations/walk.vrma')
    expect(animations[0].load).toHaveBeenNthCalledWith(3, 'afk-0', '/animations/afk-01.vrma')
    expect(animations[0].load).toHaveBeenNthCalledWith(4, 'afk-1', '/animations/afk-02.vrma')
    expect(animations[0].load).toHaveBeenNthCalledWith(5, 'afk-2', '/animations/afk-03.vrma')
    expect(animations[0].load).toHaveBeenNthCalledWith(6, 'sleep', '/animations/sleep.vrma')
    expect(resident.root.position.y).toBeGreaterThan(0)
    expect(resident.playAnimation('afk')).toBe(true)
    expect(animations[0].crossFade).not.toHaveBeenCalled()
    resident.update(2.5)
    expect(animations[0].crossFade).not.toHaveBeenCalled()
    resident.update(0.2)
    expect(animations[0].crossFade).toHaveBeenNthCalledWith(1, 'afk-0', 1.35)
    expect(resident.root.position.y).toBeGreaterThan(0)

    expect(resident.playAnimation('afk')).toBe(true)
    resident.update(2.7)
    expect(animations[0].crossFade).toHaveBeenNthCalledWith(2, 'afk-1', 1.35)

    expect(resident.playAnimation('sleep')).toBe(true)
    expect(animations[0].crossFade).toHaveBeenCalledTimes(2)
    resident.update(1)
    expect(resident.playAnimation('walk')).toBe(true)
    expect(animations[0].crossFade).toHaveBeenNthCalledWith(3, 'walk', 0.8)
    resident.update(1)
    expect(resident.root.position.y).toBeCloseTo(0.32, 4)
    expect(animations[0].crossFade).toHaveBeenCalledTimes(3)

    expect(resident.playAnimation('sleep')).toBe(true)
    resident.update(3.2)
    expect(animations[0].crossFade).toHaveBeenCalledTimes(3)
    resident.update(0.3)
    expect(animations[0].crossFade).toHaveBeenNthCalledWith(4, 'sleep', 1.8)
    expect(resident.root.position.y).toBeCloseTo(0, 4)

    expect(resident.playAnimation('walk')).toBe(true)
    expect(animations[0].crossFade).toHaveBeenNthCalledWith(5, 'walk', 0.8)
    resident.update(1)
    expect(resident.root.position.y).toBeCloseTo(0.32, 4)
    expect(resident.setEmotion('happy')).toBe(true)
    expect(expression.setEmotion).toHaveBeenCalledWith('happy')
    expect(resident.triggerBlink()).toBe(true)
    expect(expression.triggerBlink).toHaveBeenCalledOnce()
    const masterTarget = new THREE.Object3D()
    expect(resident.face(masterTarget)).toBe(true)
    expect(newest.vrm.lookAt?.target).toBe(masterTarget)
    expect(newest.vrm.lookAt?.autoUpdate).toBe(true)
    expect(resident.moveTo(new THREE.Vector3(1, 0.55, -0.45))).toBe(true)
    expect(animations[0].crossFade).toHaveBeenNthCalledWith(6, 'walk', 0.8)
    resident.update(2)
    expect(resident.root.position.toArray()).toEqual([1, 0.55, -0.45])
    expect(animations[0].crossFade).toHaveBeenCalledTimes(6)
    resident.update(1.4)
    expect(animations[0].crossFade).toHaveBeenCalledTimes(6)
    resident.update(0.2)
    expect(animations[0].crossFade).toHaveBeenNthCalledWith(7, 'stand', 0.9)
    resident.update(0.1)
    expect(expression.update).toHaveBeenCalledWith(0.1)
    expect(animations[1].dispose).toHaveBeenCalled()
  })
})
