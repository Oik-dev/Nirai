import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import type { VRM } from '@pixiv/three-vrm'
import type { LoadedVrm } from '../../src/renderer/src/world/vrm/VrmLoader'
import { ResidentInstance } from '../../src/renderer/src/world/ResidentInstance'
import { ResidentManager } from '../../src/renderer/src/world/ResidentManager'

function createLoadedVrm(): LoadedVrm {
  const scene = new THREE.Group()

  return {
    vrm: { scene, update: vi.fn() } as unknown as VRM,
    mixer: {
      update: vi.fn(),
      stopAllAction: vi.fn(),
      uncacheRoot: vi.fn()
    } as unknown as THREE.AnimationMixer
  }
}

describe('ResidentManager', () => {
  it('spawns once through the manager and removes the resident cleanly', async () => {
    const scene = new THREE.Scene()
    const loaded = createLoadedVrm()
    const loader = {
      load: vi.fn(async () => loaded),
      update: vi.fn(),
      unload: vi.fn()
    }
    const factory = (name: string) =>
      new ResidentInstance(
        name,
        loader,
        async () => new Uint8Array([1]),
        () => ({
          load: async () => undefined,
          play: vi.fn(),
          crossFade: vi.fn(),
          getCurrentName: vi.fn(() => null),
          update: vi.fn(),
          dispose: vi.fn()
        }),
        {
          stand: '/animations/stand.vrma',
          walk: '/animations/walk.vrma',
          afk: ['/animations/afk-01.vrma'],
          sleep: '/animations/sleep.vrma'
        }
      )
    const manager = new ResidentManager(scene, factory)

    await manager.spawn({ name: 'Lapan', avatar: 'lapan.vrm' })
    await manager.spawn({ name: 'Lapan', avatar: 'duplicate.vrm' })

    expect(loader.load).toHaveBeenCalledTimes(1)
    expect(manager.get('Lapan')).toBeDefined()
    expect(scene.children).toEqual([manager.get('Lapan')?.root])

    manager.remove('Lapan')

    expect(manager.get('Lapan')).toBeUndefined()
    expect(scene.children).toHaveLength(0)
    expect(loader.unload).toHaveBeenCalledWith(loaded)
  })

  it('separates natural residents more than directed residents without changing logical targets', async () => {
    const scene = new THREE.Scene()
    const factory = (name: string) => {
      const loader = {
        load: vi.fn(async () => createLoadedVrm()),
        update: vi.fn(),
        unload: vi.fn()
      }
      return new ResidentInstance(
        name,
        loader,
        async () => new Uint8Array([1]),
        () => ({
          load: async () => undefined,
          play: vi.fn(),
          crossFade: vi.fn(),
          getCurrentName: vi.fn(() => 'stand' as const),
          update: vi.fn(),
          dispose: vi.fn()
        }),
        {
          stand: '/animations/stand.vrma',
          walk: '/animations/walk.vrma',
          afk: ['/animations/afk-01.vrma'],
          sleep: '/animations/sleep.vrma'
        }
      )
    }
    const manager = new ResidentManager(scene, factory)
    await manager.spawn({ name: 'A', avatar: 'a.vrm' })
    await manager.spawn({ name: 'B', avatar: 'b.vrm' })
    const left = manager.get('A')!
    const right = manager.get('B')!
    left.root.position.set(0, 0.32, 0)
    right.root.position.set(0, 0.32, 0)

    for (let index = 0; index < 60; index += 1) manager.update(1 / 60)
    const naturalDistance = left.getPresentationPosition().distanceTo(right.getPresentationPosition())
    expect(naturalDistance).toBeGreaterThan(0.6)
    expect(left.root.position.distanceTo(right.root.position)).toBe(0)

    left.setProximityMode('directed')
    for (let index = 0; index < 90; index += 1) manager.update(1 / 60)
    const directedDistance = left.getPresentationPosition().distanceTo(right.getPresentationPosition())
    expect(directedDistance).toBeGreaterThan(0.24)
    expect(directedDistance).toBeLessThan(naturalDistance - 0.2)
    expect(left.root.position.distanceTo(right.root.position)).toBe(0)
  })
})
