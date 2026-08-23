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
          afk: ['/animations/afk.vrma'],
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
})
