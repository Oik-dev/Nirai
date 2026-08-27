import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { VRMHumanBoneName, type VRM } from '@pixiv/three-vrm'
import { UnderwaterMotionController } from '../../src/renderer/src/world/UnderwaterMotionController'

describe('UnderwaterMotionController', () => {
  it('keeps the former Move B water-motion language as the standard move pose', () => {
    const nodes = new Map<string, THREE.Object3D>()
    for (const name of Object.values(VRMHumanBoneName)) {
      nodes.set(name, new THREE.Object3D())
    }
    const vrm = {
      humanoid: {
        getNormalizedBoneNode: (name: string) => nodes.get(name) ?? null
      }
    } as unknown as VRM
    const motion = new UnderwaterMotionController(vrm, 0.4)
    const leftUpperLeg = nodes.get(VRMHumanBoneName.LeftUpperLeg)!
    const original = leftUpperLeg.quaternion.clone()

    for (let index = 0; index < 45; index += 1) {
      motion.beginFrame(1 / 60)
      motion.apply('seabed', true)
    }
    expect(leftUpperLeg.quaternion.angleTo(original)).toBeGreaterThan(0.01)
    expect(motion.getLegSwingWeight()).toBe(1)

    motion.beginFrame(0)
    expect(leftUpperLeg.quaternion.angleTo(original)).toBeLessThan(0.00001)
  })

  it('adds the selected Move B leg swing without changing the upper-body current layer', () => {
    const createRig = (): { vrm: VRM; nodes: Map<string, THREE.Object3D> } => {
      const nodes = new Map<string, THREE.Object3D>()
      for (const name of Object.values(VRMHumanBoneName)) {
        nodes.set(name, new THREE.Object3D())
      }
      const vrm = {
        humanoid: {
          getNormalizedBoneNode: (name: string) => nodes.get(name) ?? null
        }
      } as unknown as VRM
      return { vrm, nodes }
    }

    const rigA = createRig()
    const rigB = createRig()
    const moveA = new UnderwaterMotionController(rigA.vrm, 0.4)
    const moveB = new UnderwaterMotionController(rigB.vrm, 0.4)

    moveA.beginFrame(1 / 60)
    moveA.apply('seabed', false)
    moveB.beginFrame(1 / 60)
    moveB.apply('seabed', true)

    const angleBetween = (boneName: (typeof VRMHumanBoneName)[keyof typeof VRMHumanBoneName]): number =>
      rigA.nodes.get(boneName)!.quaternion.angleTo(rigB.nodes.get(boneName)!.quaternion)

    expect(angleBetween(VRMHumanBoneName.LeftUpperLeg)).toBeGreaterThan(0.005)
    expect(angleBetween(VRMHumanBoneName.RightUpperLeg)).toBeGreaterThan(0.005)
    expect(angleBetween(VRMHumanBoneName.LeftUpperArm)).toBeLessThan(0.00001)
    expect(angleBetween(VRMHumanBoneName.RightUpperArm)).toBeLessThan(0.00001)
    expect(angleBetween(VRMHumanBoneName.LeftLowerLeg)).toBeLessThan(0.00001)
    expect(angleBetween(VRMHumanBoneName.RightLowerLeg)).toBeLessThan(0.00001)
    expect(angleBetween(VRMHumanBoneName.LeftFoot)).toBeLessThan(0.00001)
    expect(angleBetween(VRMHumanBoneName.RightFoot)).toBeLessThan(0.00001)

    expect(moveB.getLegSwingWeight()).toBe(1)
    moveB.beginFrame(1 / 60)
    moveB.apply('idle', false)
    expect(moveB.getLegSwingWeight()).toBeGreaterThan(0)
    expect(moveB.getLegSwingWeight()).toBeLessThan(1)

    for (let index = 0; index < 90; index += 1) {
      moveB.beginFrame(1 / 60)
      moveB.apply('idle', false)
    }
    expect(moveB.getLegSwingWeight()).toBeLessThan(0.001)

    moveB.beginFrame(0)
    expect(
      rigB.nodes.get(VRMHumanBoneName.LeftUpperLeg)!.quaternion.angleTo(new THREE.Quaternion())
    ).toBeLessThan(0.00001)
  })

  it('reduces the current layer for grounded AFK poses', () => {
    const afkChest = new THREE.Object3D()
    const groundedChest = new THREE.Object3D()
    const createVrm = (chest: THREE.Object3D): VRM => ({
      humanoid: {
        getNormalizedBoneNode: (name: string) =>
          name === VRMHumanBoneName.Chest ? chest : null
      }
    }) as unknown as VRM
    const afkMotion = new UnderwaterMotionController(createVrm(afkChest), 0.7)
    const groundedMotion = new UnderwaterMotionController(createVrm(groundedChest), 0.7)

    afkMotion.beginFrame(1)
    afkMotion.apply('afk')
    groundedMotion.beginFrame(1)
    groundedMotion.apply('grounded-afk')

    const identity = new THREE.Quaternion()
    expect(groundedChest.quaternion.angleTo(identity)).toBeGreaterThan(0)
    expect(groundedChest.quaternion.angleTo(identity)).toBeLessThan(
      afkChest.quaternion.angleTo(identity)
    )
  })

  it('keeps sleep untouched while idle still has a subtle current response', () => {
    const chest = new THREE.Object3D()
    const vrm = {
      humanoid: {
        getNormalizedBoneNode: (name: string) =>
          name === VRMHumanBoneName.Chest ? chest : null
      }
    } as unknown as VRM
    const motion = new UnderwaterMotionController(vrm, 1.1)

    motion.beginFrame(1)
    motion.apply('idle')
    expect(chest.quaternion.angleTo(new THREE.Quaternion())).toBeGreaterThan(0)
    motion.beginFrame(0)
    motion.apply('sleep')
    expect(chest.quaternion.angleTo(new THREE.Quaternion())).toBeLessThan(0.00001)
  })

  it('removes multi-axis head adjustments without accumulating rotational drift', () => {
    const head = new THREE.Object3D()
    const original = new THREE.Quaternion().setFromEuler(new THREE.Euler(0.2, -0.35, 0.1))
    head.quaternion.copy(original)
    const vrm = {
      humanoid: {
        getNormalizedBoneNode: (name: string) =>
          name === VRMHumanBoneName.Head ? head : null
      }
    } as unknown as VRM
    const motion = new UnderwaterMotionController(vrm, 0.3)

    for (let index = 0; index < 120; index += 1) {
      motion.beginFrame(1 / 60)
      motion.apply('afk', false, {
        pitchRadians: THREE.MathUtils.degToRad(25),
        yawRadians: THREE.MathUtils.degToRad(-70)
      })
    }

    motion.beginFrame(0)
    expect(head.quaternion.angleTo(original)).toBeLessThan(0.00001)
  })

  it('adds a small walk leg accent and can lift only the AFK-05 face', () => {
    const leftUpperLeg = new THREE.Object3D()
    const leftLowerLeg = new THREE.Object3D()
    const head = new THREE.Object3D()
    const vrm = {
      humanoid: {
        getNormalizedBoneNode: (name: string) => {
          if (name === VRMHumanBoneName.LeftUpperLeg) return leftUpperLeg
          if (name === VRMHumanBoneName.LeftLowerLeg) return leftLowerLeg
          if (name === VRMHumanBoneName.Head) return head
          return null
        }
      }
    } as unknown as VRM
    const motion = new UnderwaterMotionController(vrm, 0.8)

    motion.beginFrame(1 / 30)
    motion.apply('seabed')
    expect(leftUpperLeg.quaternion.angleTo(new THREE.Quaternion())).toBeGreaterThan(0.001)
    expect(leftLowerLeg.quaternion.angleTo(new THREE.Quaternion())).toBeGreaterThan(0.001)

    motion.beginFrame(1 / 30)
    motion.apply('afk', false, {
      pitchRadians: THREE.MathUtils.degToRad(-8),
      yawRadians: 0
    })
    expect(head.quaternion.angleTo(new THREE.Quaternion())).toBeGreaterThan(0.001)
  })
})
