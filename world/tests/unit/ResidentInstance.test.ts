import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import type { VRM } from '@pixiv/three-vrm'
import type { LoadedVrm } from '../../src/renderer/src/world/vrm/VrmLoader'
import type { AnimationClipName } from '../../src/renderer/src/world/vrm/AnimationController'
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

function advanceResident(resident: ResidentInstance, durationSec: number): void {
  let remaining = durationSec
  while (remaining > 0.000001) {
    const delta = Math.min(1 / 30, remaining)
    resident.update(delta)
    remaining -= delta
  }
}

describe('ResidentInstance', () => {
  it('provides head and full-pose bone data for the Focus camera rig', () => {
    const resident = new ResidentInstance(
      'CameraAnchor',
      {
        load: vi.fn(),
        update: vi.fn(),
        unload: vi.fn()
      },
      async () => new Uint8Array([1]),
      () => ({
        load: vi.fn(async () => undefined),
        play: vi.fn(),
        crossFade: vi.fn(),
        getCurrentName: vi.fn(() => 'stand' as const),
        update: vi.fn(),
        dispose: vi.fn()
      }),
      {
        stand: '/stand.vrma',
        walk: '/walk.vrma',
        afk: [],
        sleep: '/sleep.vrma'
      },
      () => ({
        setEmotion: vi.fn(),
        triggerBlink: vi.fn(),
        update: vi.fn(),
        dispose: vi.fn()
      })
    )
    const bones = new Map<string, THREE.Object3D>()
    const addBone = (name: string, y: number): void => {
      const bone = new THREE.Object3D()
      bone.position.y = y
      resident.root.add(bone)
      bones.set(name, bone)
    }

    addBone('hips', 1.0)
    addBone('spine', 1.2)
    addBone('chest', 1.4)
    addBone('upperChest', 1.6)
    addBone('neck', 1.8)
    addBone('head', 2.0)
    addBone('leftShoulder', 1.55)
    addBone('rightShoulder', 1.55)
    addBone('leftHand', 10.0)
    addBone('rightHand', -8.0)
    addBone('leftUpperLeg', 0.82)
    addBone('rightUpperLeg', 0.82)
    addBone('leftLowerLeg', 0.42)
    addBone('rightLowerLeg', 0.42)
    addBone('leftFoot', 0.0)
    addBone('rightFoot', 0.02)
    const requested: string[] = []
    resident.vrm = {
      humanoid: {
        getNormalizedBoneNode: (name: string) => {
          requested.push(name)
          return bones.get(name) ?? null
        }
      }
    } as unknown as VRM
    resident.root.updateMatrixWorld(true)

    expect(resident.getCameraHeadPosition()?.y).toBeCloseTo(2.0, 8)
    expect(requested).toEqual(['head'])

    requested.length = 0
    const framingBounds = resident.getCameraFramingBounds()
    expect(framingBounds).not.toBeNull()
    expect(framingBounds?.min.y).toBeLessThan(-8)
    expect(framingBounds?.max.y).toBeGreaterThan(10)
    expect(requested).toContain('leftHand')
    expect(requested).toContain('rightFoot')
  })

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
    await vi.waitFor(() => expect(animations[0].load).toHaveBeenCalledTimes(6))
    expect(animations[0].load).toHaveBeenNthCalledWith(1, 'stand', '/animations/stand.vrma')
    expect(animations[0].load).toHaveBeenNthCalledWith(2, 'walk', '/animations/walk.vrma')
    expect(animations[0].load).toHaveBeenNthCalledWith(3, 'sleep', '/animations/sleep.vrma')
    expect(animations[0].load).toHaveBeenNthCalledWith(4, 'afk-0', '/animations/afk-01.vrma')
    expect(animations[0].load).toHaveBeenNthCalledWith(5, 'afk-1', '/animations/afk-02.vrma')
    expect(animations[0].load).toHaveBeenNthCalledWith(6, 'afk-2', '/animations/afk-03.vrma')
    expect(resident.root.position.y).toBeGreaterThan(0)
    const rootBeforeAfk = resident.root.position.clone()
    expect(resident.playAnimation('afk')).toBe(true)
    expect(animations[0].crossFade).toHaveBeenNthCalledWith(1, 'afk-0', 1.35)
    advanceResident(resident, 0.7)
    expect(resident.root.position.toArray()).toEqual(rootBeforeAfk.toArray())
    advanceResident(resident, 0.7)
    expect(animations[0].crossFade).toHaveBeenCalledTimes(1)

    expect(resident.playAnimation('afk')).toBe(true)
    expect(animations[0].crossFade).toHaveBeenNthCalledWith(2, 'afk-1', 1.35)
    advanceResident(resident, 1.4)
    expect(animations[0].crossFade).toHaveBeenCalledTimes(2)

    animations[0].crossFade.mockClear()
    const beforeInterruptedSleep = resident.root.position.clone()
    expect(resident.playAnimation('sleep')).toBe(true)
    expect(resident.isSleepPresentationActive()).toBe(true)
    expect(animations[0].crossFade).toHaveBeenCalledWith('sleep', 1.8)
    expect(animations[0].crossFade).not.toHaveBeenCalledWith('stand', 0.8)
    advanceResident(resident, 0.2)
    expect(resident.root.position.y).toBeLessThan(beforeInterruptedSleep.y)
    expect(resident.root.position.y).toBeGreaterThan(0)
    expect(resident.root.position.x).toBeCloseTo(beforeInterruptedSleep.x, 8)
    expect(resident.root.position.z).toBeCloseTo(beforeInterruptedSleep.z, 8)
    expect(resident.playAnimation('stand')).toBe(true)
    expect(resident.isSleepPresentationActive()).toBe(false)
    expect(resident.movement.isMoving).toBe(false)
    expect(animations[0].crossFade).toHaveBeenLastCalledWith('stand', 0.9)
    advanceResident(resident, 0.1)
    expect(resident.root.position.y).toBeGreaterThan(0)
    expect(resident.movement.isMoving).toBe(false)
    advanceResident(resident, 0.9)
    expect(resident.root.position.y).toBeCloseTo(0.32, 3)
    expect(resident.movement.isMoving).toBe(false)

    animations[0].crossFade.mockClear()
    resident.root.position.set(2.0, resident.root.position.y, 0.3)
    const beforeSleep = resident.root.position.clone()
    expect(resident.playAnimation('sleep')).toBe(true)
    expect(resident.isSleepPresentationActive()).toBe(true)
    expect(animations[0].crossFade).toHaveBeenCalledWith('sleep', 1.8)
    expect(animations[0].crossFade).not.toHaveBeenCalledWith('stand', 0.8)
    advanceResident(resident, 1)
    expect(resident.root.position.y).toBeLessThan(beforeSleep.y)
    expect(resident.root.position.y).toBeGreaterThan(0)
    expect(resident.root.position.x).toBeLessThan(beforeSleep.x)
    expect(resident.root.position.z).toBeLessThan(beforeSleep.z)
    advanceResident(resident, 2.1)
    expect(resident.root.position.y).toBeCloseTo(0, 4)
    expect(resident.root.position.x).toBeCloseTo(1.73, 4)
    expect(resident.root.position.z).toBeCloseTo(0.18, 4)
    expect(resident.isSleepPresentationActive()).toBe(true)
    expect(animations[0].crossFade).toHaveBeenCalledWith('sleep', 1.8)
    expect(animations[0].crossFade).toHaveBeenCalledTimes(1)

    animations[0].crossFade.mockClear()
    expect(resident.playAnimation('stand')).toBe(true)
    expect(animations[0].crossFade).toHaveBeenCalledWith('stand', 0.9)
    advanceResident(resident, 1)
    expect(resident.root.position.y).toBeCloseTo(0.32, 4)

    const hoverHeight = resident.root.position.y
    expect(
      resident.moveTo(
        new THREE.Vector3(0.35, hoverHeight, -0.2),
        undefined,
        undefined
      )
    ).toBe(true)
    expect(animations[0].crossFade).toHaveBeenCalledWith('walk', 0.8)
    for (let index = 0; index < 20; index += 1) {
      resident.update(0.05)
      expect(resident.root.position.y).toBeCloseTo(hoverHeight, 6)
    }

    expect(resident.setEmotion('happy')).toBe(true)
    expect(expression.setEmotion).toHaveBeenCalledWith('happy')
    expect(resident.triggerBlink()).toBe(true)
    expect(expression.triggerBlink).toHaveBeenCalledWith(0.28)
    const masterTarget = new THREE.Object3D()
    expect(resident.face(masterTarget)).toBe(true)
    expect(newest.vrm.lookAt?.target).toBe(masterTarget)
    expect(newest.vrm.lookAt?.autoUpdate).toBe(true)
    animations[0].crossFade.mockClear()
    expect(resident.moveTo(new THREE.Vector3(1, 0.55, -0.45))).toBe(true)
    expect(resident.getProximityMode()).toBe('directed')
    expect(animations[0].crossFade).toHaveBeenCalledWith('walk', 0.8)
    advanceResident(resident, 2)
    expect(resident.root.position.toArray()).toEqual([1, 0.55, -0.45])
    expect(resident.getProximityMode()).toBe('natural')
    expect(animations[0].crossFade).toHaveBeenCalledTimes(2)
    expect(animations[0].crossFade).toHaveBeenNthCalledWith(2, 'stand', 0.9)
    advanceResident(resident, 1.5)
    expect(animations[0].crossFade).toHaveBeenCalledTimes(2)

    expect(resident.moveTo(new THREE.Vector3(-1, 0.55, -0.45))).toBe(true)
    advanceResident(resident, 0.2)
    const interruptedMovePosition = resident.root.position.clone()
    expect(resident.movement.isMoving).toBe(true)
    expect(resident.playAnimation('stand')).toBe(true)
    expect(resident.movement.isMoving).toBe(false)
    advanceResident(resident, 0.5)
    expect(resident.root.position.toArray()).toEqual(interruptedMovePosition.toArray())

    advanceResident(resident, 0.1)
    expect(expression.update).toHaveBeenCalled()
    expect(animations[1].dispose).toHaveBeenCalled()
  })

  it('redirects an active Move A to Move B without resetting the resident position', async () => {
    const loaded = createLoadedVrm('move-redirect')
    const loader = {
      load: vi.fn(async () => loaded),
      update: vi.fn(),
      unload: vi.fn()
    }
    let currentName: AnimationClipName | null = null
    const animation = {
      load: vi.fn(async () => undefined),
      play: vi.fn((name: AnimationClipName) => { currentName = name }),
      crossFade: vi.fn((name: AnimationClipName) => { currentName = name }),
      getCurrentName: vi.fn(() => currentName),
      update: vi.fn(),
      dispose: vi.fn()
    }
    const resident = new ResidentInstance(
      'MoveRedirect',
      loader,
      async () => new Uint8Array([1]),
      () => animation,
      {
        stand: '/animations/stand.vrma',
        walk: '/animations/walk.vrma',
        afk: ['/animations/afk-01.vrma'],
        sleep: '/animations/sleep.vrma'
      },
      () => ({
        setEmotion: vi.fn(),
        triggerBlink: vi.fn(),
        update: vi.fn(),
        dispose: vi.fn()
      }),
      () => 0.8
    )

    await resident.loadAvatar('move-redirect.vrm')
    await vi.waitFor(() => expect(animation.load).toHaveBeenCalledWith('walk', '/animations/walk.vrma'))
    await Promise.resolve()

    const bounds = {
      min: new THREE.Vector3(-2.15, 0, -1.42),
      max: new THREE.Vector3(2.15, 1.12, 0.36)
    }
    expect(resident.moveTo(new THREE.Vector3(-1.05, 0.32, -0.46), undefined, bounds)).toBe(true)
    advanceResident(resident, 0.4)
    const beforeRedirect = resident.root.position.clone()

    expect(resident.moveTo(new THREE.Vector3(1.05, 0.32, -0.68), undefined, bounds)).toBe(true)
    expect(resident.root.position.toArray()).toEqual(beforeRedirect.toArray())
    resident.update(1 / 60)

    expect(resident.root.position.distanceTo(beforeRedirect)).toBeLessThan(0.01)
    expect(resident.root.position.x).toBeGreaterThan(beforeRedirect.x)
  })

  it('ignores rapid AFK repeats until the current cross-fade has settled', async () => {
    const loaded = createLoadedVrm('afk-repeat-guard')
    const loader = {
      load: vi.fn(async () => loaded),
      update: vi.fn(),
      unload: vi.fn()
    }
    let currentName: AnimationClipName | null = null
    const animation = {
      load: vi.fn(async () => undefined),
      play: vi.fn((name: AnimationClipName) => { currentName = name }),
      crossFade: vi.fn((name: AnimationClipName) => { currentName = name }),
      getCurrentName: vi.fn(() => currentName),
      update: vi.fn(),
      dispose: vi.fn()
    }
    let randomValue = 0
    const resident = new ResidentInstance(
      'AfkRepeatGuard',
      loader,
      async () => new Uint8Array([1]),
      () => animation,
      {
        stand: '/animations/stand.vrma',
        walk: '/animations/walk.vrma',
        afk: ['/animations/afk-01.vrma', '/animations/afk-02.vrma'],
        sleep: '/animations/sleep.vrma'
      },
      () => ({
        setEmotion: vi.fn(),
        triggerBlink: vi.fn(),
        update: vi.fn(),
        dispose: vi.fn()
      }),
      () => randomValue
    )

    await resident.loadAvatar('afk-repeat-guard.vrm')
    await vi.waitFor(() => expect(animation.load).toHaveBeenCalledWith('afk-1', '/animations/afk-02.vrma'))

    expect(resident.playAnimation('afk')).toBe(true)
    expect(animation.crossFade).toHaveBeenCalledTimes(1)
    expect(currentName).toBe('afk-0')

    randomValue = 0.999
    expect(resident.playAnimation('afk')).toBe(false)
    expect(animation.crossFade).toHaveBeenCalledTimes(1)
    expect(currentName).toBe('afk-0')

    advanceResident(resident, 1.36)
    expect(resident.playAnimation('afk')).toBe(true)
    expect(animation.crossFade).toHaveBeenCalledTimes(2)
    expect(currentName).toBe('afk-1')
  })

  it('does not alter an active Move B when unavailable AFK or Sleep is requested', async () => {
    const loaded = createLoadedVrm('unavailable-action-noop')
    const loader = {
      load: vi.fn(async () => loaded),
      update: vi.fn(),
      unload: vi.fn()
    }
    const pending = new Map<string, () => void>()
    let currentName: AnimationClipName | null = null
    const animation = {
      load: vi.fn((name: AnimationClipName) => {
        if (name === 'stand' || name === 'walk') return Promise.resolve()
        return new Promise<void>((resolve) => pending.set(name, resolve))
      }),
      play: vi.fn((name: AnimationClipName) => { currentName = name }),
      crossFade: vi.fn((name: AnimationClipName) => { currentName = name }),
      getCurrentName: vi.fn(() => currentName),
      update: vi.fn(),
      dispose: vi.fn()
    }
    const resident = new ResidentInstance(
      'UnavailableActionNoop',
      loader,
      async () => new Uint8Array([1]),
      () => animation,
      {
        stand: '/animations/stand.vrma',
        walk: '/animations/walk.vrma',
        afk: ['/animations/afk-01.vrma'],
        sleep: '/animations/sleep.vrma'
      },
      () => ({
        setEmotion: vi.fn(),
        triggerBlink: vi.fn(),
        update: vi.fn(),
        dispose: vi.fn()
      })
    )

    await resident.loadAvatar('unavailable-action-noop.vrm')
    await vi.waitFor(() =>
      expect(animation.load).toHaveBeenCalledWith('walk', '/animations/walk.vrma')
    )

    const hoverHeight = resident.root.position.y
    expect(resident.moveTo(
      new THREE.Vector3(0.8, hoverHeight, -0.2),
      undefined,
      undefined
    )).toBe(true)
    expect(resident.movement.isMoving).toBe(true)
    expect((resident as unknown as { legSwingEnabled: boolean }).legSwingEnabled).toBe(true)

    expect(resident.playAnimation('sleep')).toBe(false)
    expect(resident.playAnimation('afk')).toBe(false)
    expect(resident.movement.isMoving).toBe(true)
    expect((resident as unknown as { legSwingEnabled: boolean }).legSwingEnabled).toBe(true)
    expect(currentName).toBe('walk')
  })

  it('cancels a deferred Move when AFK takes over before walk finishes loading', async () => {
    const loaded = createLoadedVrm('deferred-move-interrupt')
    const loader = {
      load: vi.fn(async () => loaded),
      update: vi.fn(),
      unload: vi.fn()
    }
    let currentName: AnimationClipName | null = null
    let walkLoadCount = 0
    let resolveWalkRetry!: () => void
    const animation = {
      load: vi.fn((name: AnimationClipName) => {
        if (name === 'walk') {
          walkLoadCount += 1
          if (walkLoadCount === 1) {
            return Promise.reject(new Error('transient walk load failure'))
          }
          return new Promise<void>((resolve) => { resolveWalkRetry = resolve })
        }
        return Promise.resolve()
      }),
      play: vi.fn((name: AnimationClipName) => { currentName = name }),
      crossFade: vi.fn((name: AnimationClipName) => { currentName = name }),
      getCurrentName: vi.fn(() => currentName),
      update: vi.fn(),
      dispose: vi.fn()
    }
    const resident = new ResidentInstance(
      'DeferredMoveInterrupt',
      loader,
      async () => new Uint8Array([1]),
      () => animation,
      {
        stand: '/animations/stand.vrma',
        walk: '/animations/walk.vrma',
        afk: ['/animations/afk-01.vrma'],
        sleep: '/animations/sleep.vrma'
      },
      () => ({
        setEmotion: vi.fn(),
        triggerBlink: vi.fn(),
        update: vi.fn(),
        dispose: vi.fn()
      }),
      () => 0
    )

    await resident.loadAvatar('deferred-move-interrupt.vrm')
    await vi.waitFor(() =>
      expect(animation.load).toHaveBeenCalledWith('afk-0', '/animations/afk-01.vrma')
    )

    const hoverHeight = resident.root.position.y
    expect(
      resident.moveTo(
        new THREE.Vector3(0.8, hoverHeight, -0.2),
        undefined,
        undefined
      )
    ).toBe(true)
    await vi.waitFor(() => expect(walkLoadCount).toBe(2))
    expect(resident.movement.isMoving).toBe(false)

    expect(resident.playAnimation('afk')).toBe(true)
    expect(currentName).toBe('afk-0')
    resolveWalkRetry()
    await Promise.resolve()
    await Promise.resolve()

    expect(resident.movement.isMoving).toBe(false)
    expect(currentName).toBe('afk-0')
    expect(animation.crossFade).not.toHaveBeenCalledWith('walk', 0.8)

    advanceResident(resident, 3)
    expect(animation.crossFade).not.toHaveBeenCalledWith('stand', 0.9)
  })

  it('shows the avatar after stand and loads the remaining motions in priority batches', async () => {
    const loaded = createLoadedVrm('fast-start')
    const loader = {
      load: vi.fn(async () => loaded),
      update: vi.fn(),
      unload: vi.fn()
    }
    const pending = new Map<string, () => void>()
    const loadAnimation = vi.fn((name: string) => {
      if (name === 'stand') {
        return Promise.resolve()
      }
      return new Promise<void>((resolve) => pending.set(name, resolve))
    })
    const animation = {
      load: loadAnimation,
      play: vi.fn(),
      crossFade: vi.fn(),
      getCurrentName: vi.fn(() => 'stand' as const),
      update: vi.fn(),
      dispose: vi.fn()
    }
    const resident = new ResidentInstance(
      'FastStart',
      loader,
      async () => new Uint8Array([1]),
      () => animation,
      {
        stand: '/animations/stand.vrma',
        walk: '/animations/walk.vrma',
        afk: [
          '/animations/afk-01.vrma',
          '/animations/afk-02.vrma',
          '/animations/afk-03.vrma',
          '/animations/afk-04.vrma'
        ],
        sleep: '/animations/sleep.vrma'
      },
      () => ({
        setEmotion: vi.fn(),
        triggerBlink: vi.fn(),
        update: vi.fn(),
        dispose: vi.fn()
      }),
      () => 0
    )

    await resident.loadAvatar('fast-start.vrm')

    expect(resident.vrm).toBe(loaded.vrm)
    expect(animation.play).toHaveBeenCalledWith('stand')
    expect(loadAnimation).toHaveBeenNthCalledWith(1, 'stand', '/animations/stand.vrma')
    expect(loadAnimation).toHaveBeenNthCalledWith(2, 'walk', '/animations/walk.vrma')
    expect(loadAnimation).toHaveBeenNthCalledWith(3, 'sleep', '/animations/sleep.vrma')
    expect(loadAnimation).toHaveBeenCalledWith('afk-0', '/animations/afk-01.vrma')
    expect(loadAnimation).toHaveBeenCalledWith('afk-1', '/animations/afk-02.vrma')
    expect(loadAnimation).toHaveBeenCalledWith('afk-2', '/animations/afk-03.vrma')
    expect(loadAnimation).not.toHaveBeenCalledWith('afk-3', '/animations/afk-04.vrma')
    expect(resident.playAnimation('sleep')).toBe(false)
    expect(resident.playAnimation('afk')).toBe(false)

    const hoverHeight = resident.root.position.y
    expect(
      resident.moveTo(
        new THREE.Vector3(0.6, hoverHeight, -0.2),
        undefined,
        undefined
      )
    ).toBe(true)
    expect(resident.movement.isMoving).toBe(false)

    pending.get('walk')?.()
    await vi.waitFor(() => expect(resident.movement.isMoving).toBe(true))
    expect(animation.crossFade).toHaveBeenCalledWith('walk', 0.8)

    pending.get('sleep')?.()
    pending.get('afk-0')?.()
    pending.get('afk-1')?.()
    pending.get('afk-2')?.()
    await vi.waitFor(() =>
      expect(loadAnimation).toHaveBeenCalledWith('afk-3', '/animations/afk-04.vrma')
    )
  })

  it('retires AFK-07/08, keeps pose fixes stable, grounds AFK-09, and holds sleeping motions closed', async () => {
    const loaded = createLoadedVrm('afk-presentation')
    const loader = {
      load: vi.fn(async () => loaded),
      update: vi.fn(),
      unload: vi.fn()
    }
    let currentName: AnimationClipName | null = null
    const animation = {
      load: vi.fn(async () => undefined),
      play: vi.fn((name: AnimationClipName) => { currentName = name }),
      crossFade: vi.fn((name: AnimationClipName) => { currentName = name }),
      getCurrentName: vi.fn(() => currentName),
      update: vi.fn(),
      dispose: vi.fn()
    }
    const setBlinkHeld = vi.fn()
    const expression = {
      setEmotion: vi.fn(),
      triggerBlink: vi.fn(),
      setBlinkHeld,
      update: vi.fn(),
      dispose: vi.fn()
    }
    let randomValue = 0
    const resident = new ResidentInstance(
      'AfkPresentation',
      loader,
      async () => new Uint8Array([1]),
      () => animation,
      {
        stand: '/animations/stand.vrma',
        walk: '/animations/walk.vrma',
        afk: Array.from({ length: 9 }, (_, index) =>
          `/animations/afk-${String(index + 1).padStart(2, '0')}.vrma`
        ),
        sleep: '/animations/sleep.vrma'
      },
      () => expression,
      () => randomValue
    )

    await resident.loadAvatar('afk-presentation.vrm')
    await vi.waitFor(() =>
      expect(animation.load).toHaveBeenCalledWith(
        'afk-8',
        '/animations/afk-09.vrma',
        { preserveAuthoredHipsHeight: true }
      )
    )
    expect(animation.load).not.toHaveBeenCalledWith('afk-6', '/animations/afk-07.vrma')
    expect(animation.load).not.toHaveBeenCalledWith('afk-7', '/animations/afk-08.vrma')

    expect(resident.getPoseAdjustMotionOptions().map((option) => option.label)).toEqual([
      'Stand',
      'AFK-01',
      'AFK-02',
      'AFK-03',
      'AFK-04',
      'AFK-05',
      'AFK-06',
      'AFK-09',
      'Sleep'
    ])
    expect(resident.playPoseAdjustmentMotion('walk')).toBe(false)
    const expectedPoseDefaults = [
      ['afk-0', 'root', 10.85, 64.4],
      ['afk-1', 'root', 10.85, 10.85],
      ['afk-2', 'root', 0.35, 14.35],
      ['afk-3', 'root', 10.5, 17.5],
      ['afk-3', 'head', -2.45, 1.75],
      ['afk-4', 'root', 9.2, 1.9],
      ['afk-4', 'head', -50.15, -12.25],
      ['afk-5', 'root', 26.1, 60.6],
      ['afk-5', 'head', -15.05, 24.85],
      ['afk-8', 'root', 9.45, -4.9],
      ['afk-8', 'head', -9.45, 1.05],
      ['sleep', 'root', 7.35, 65.8]
    ] as const
    for (const [clip, scope, pitchDeg, yawDeg] of expectedPoseDefaults) {
      expect(resident.getPoseAdjustment(clip, scope)).toEqual({ pitchDeg, yawDeg })
    }

    expect(resident.setPoseAdjustment('afk-4', 'head', { pitchDeg: 12, yawDeg: 90 })).toEqual({
      pitchDeg: 12,
      yawDeg: 90
    })

    expect(resident.playAnimation('afk')).toBe(true)
    expect(currentName).toBe('afk-0')
    expect(resident.isGroundedPresentationActive()).toBe(false)
    expect(setBlinkHeld).toHaveBeenLastCalledWith(true)

    expect(resident.playPoseAdjustmentMotion('afk-5')).toBe(true)
    expect(currentName).toBe('afk-5')
    expect(animation.crossFade).toHaveBeenLastCalledWith('afk-5', 1.35)
    expect(setBlinkHeld).toHaveBeenLastCalledWith(true)

    resident.root.position.y = 0.32
    expect(resident.playPoseAdjustmentMotion('afk-4')).toBe(true)
    expect(currentName).toBe('afk-4')
    expect(animation.crossFade).toHaveBeenLastCalledWith('afk-4', 1.35)
    expect(resident.root.position.y).toBeCloseTo(0.32, 6)
    advanceResident(resident, 0.1)
    expect(resident.root.position.y).toBeGreaterThan(0)
    expect(resident.root.position.y).toBeLessThan(0.32)
    expect(loaded.vrm.lookAt?.autoUpdate).toBe(false)

    expect(resident.playAnimation('stand')).toBe(true)
    advanceResident(resident, 0.1)
    expect(loaded.vrm.lookAt?.autoUpdate).toBe(true)
    expect(setBlinkHeld).toHaveBeenLastCalledWith(false)

    resident.movement.cancel()
    resident.root.position.y = 0.32
    randomValue = 0.999
    expect(resident.playAnimation('afk')).toBe(true)
    expect(currentName).toBe('afk-8')
    expect(resident.isGroundedPresentationActive()).toBe(true)
    advanceResident(resident, 0.6)
    expect(resident.root.position.y).toBeGreaterThan(0)
    expect(resident.root.position.y).toBeLessThan(0.32)
    advanceResident(resident, 2.1)
    expect(resident.root.position.y).toBe(0)
    expect(resident.isGroundedPresentationActive()).toBe(true)

    expect(resident.playPoseAdjustmentMotion('sleep')).toBe(true)
    expect(currentName).toBe('sleep')
    expect(resident.isSleepPresentationActive()).toBe(true)
    expect(animation.crossFade).toHaveBeenLastCalledWith('sleep', 1.8)
    expect(animation.crossFade).not.toHaveBeenCalledWith('sleep', 0.3)
    expect(setBlinkHeld).toHaveBeenLastCalledWith(true)
  })
})
