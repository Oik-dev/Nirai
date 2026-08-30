import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import { MovementController } from '../../src/renderer/src/world/MovementController'
import {
  createDirectionalMoveTarget,
  createScreenSafeSwimBounds,
  createThreeResidentInitialSlots,
  createTwoResidentInitialSlots
} from '../../src/renderer/src/runtime/worldConfig'

describe('MovementController', () => {
  it('moves on a shallow 3D arc while keeping the body mostly camera-facing', () => {
    const root = new THREE.Group()
    const arrived = vi.fn()
    const movement = new MovementController(root, 2)

    movement.moveTo(new THREE.Vector3(1, 0, 0), arrived, undefined, 'swim')
    movement.update(0.32)

    expect(root.position.x).toBeGreaterThan(0.28)
    expect(root.position.x).toBeLessThan(0.58)
    expect(Math.abs(root.position.z)).toBeGreaterThan(0.05)
    expect(root.position.y).toBeGreaterThan(0)
    expect(Math.abs(root.rotation.y)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(28))
    expect(movement.isMoving).toBe(true)
    expect(arrived).not.toHaveBeenCalled()

    movement.update(1)

    expect(root.position.toArray()).toEqual([1, 0, 0])
    expect(movement.isMoving).toBe(false)
    expect(arrived).toHaveBeenCalledOnce()

    movement.update(0.5)
    expect(Math.abs(root.rotation.y)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(9))
  })

  it('can hold a full body-facing yaw while idle and releases it back to the normal idle angle', () => {
    const root = new THREE.Group()
    const movement = new MovementController(root, 1.2, 0)

    movement.setIdleFacingYaw(THREE.MathUtils.degToRad(150))
    for (let index = 0; index < 30; index += 1) movement.update(1 / 60)
    expect(root.rotation.y).toBeGreaterThan(THREE.MathUtils.degToRad(100))

    movement.setIdleFacingYaw(null)
    for (let index = 0; index < 90; index += 1) movement.update(1 / 60)
    expect(Math.abs(root.rotation.y)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(9))
  })

  it('gives different residents different path arcs instead of synchronized fish-like motion', () => {
    const firstRoot = new THREE.Group()
    const secondRoot = new THREE.Group()
    const first = new MovementController(firstRoot, 1.2, 0)
    const second = new MovementController(secondRoot, 1.2, Math.PI)

    first.moveTo(new THREE.Vector3(2, 0, 0), undefined, undefined, 'swim')
    second.moveTo(new THREE.Vector3(2, 0, 0), undefined, undefined, 'swim')
    first.update(0.4)
    second.update(0.4)

    expect(Math.sign(firstRoot.position.z)).not.toBe(Math.sign(secondRoot.position.z))
  })

  it('chooses a randomized XYZ destination inside the configured screen-safe swim volume', () => {
    const root = new THREE.Group()
    const values = [0.9, 0.1, 0.8]
    let index = 0
    const movement = new MovementController(root, 1.2, 0, () => values[index++ % values.length])

    const target = movement.swimNear(
      new THREE.Vector3(0, 0.55, -0.3),
      new THREE.Vector3(1.8, 0.45, 0.9),
      {
        min: new THREE.Vector3(-2.2, 0.28, -1.35),
        max: new THREE.Vector3(2.2, 1.12, 0.55)
      }
    )

    expect(target.x).toBeGreaterThan(0)
    expect(target.y).toBeLessThan(0.55)
    expect(target.z).toBeGreaterThan(-0.3)
    expect(target.x).toBeLessThanOrEqual(2.2)
    expect(target.y).toBeGreaterThanOrEqual(0.28)
    expect(target.z).toBeLessThanOrEqual(0.55)
    expect(movement.isMoving).toBe(true)
  })

  it('uses a cubic 3D curve with gentle banking while keeping the face readable', () => {
    const root = new THREE.Group()
    const movement = new MovementController(root, 1, Math.PI * 0.37, () => 0.83)

    movement.moveTo(new THREE.Vector3(1.8, 0.9, -1.1), undefined, undefined, 'swim')
    movement.update(1.8)

    expect(root.position.y).toBeGreaterThan(0.2)
    expect(root.position.z).toBeLessThan(-0.2)
    expect(Math.abs(root.rotation.x)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(6))
    expect(Math.abs(root.rotation.z)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(18))
    expect(Math.abs(root.rotation.y)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(28))
  })

  it('keeps a same-height underwater move on the sand with Move B restrained banking', () => {
    const root = new THREE.Group()
    const movement = new MovementController(root, 1.2, 0.4, () => 0.8)

    movement.moveTo(new THREE.Vector3(1.2, 0, -0.4), undefined, undefined, 'seabed')
    for (let index = 0; index < 20; index += 1) {
      movement.update(0.1)
      expect(root.position.y).toBe(0)
      expect(Math.abs(root.rotation.x)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(1.1))
      expect(Math.abs(root.rotation.z)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(1.8))
    }
    expect(movement.locomotionMedium).toBe('seabed')
  })

  it('keeps the whole seabed path on the same depth when multi-resident movement preserves depth', () => {
    const root = new THREE.Group()
    root.position.set(0, 0.32, 0.12)
    const movement = new MovementController(root, 1.2, 0.4, () => 0.8)
    const bounds = {
      min: new THREE.Vector3(-4, 0, -1.42),
      max: new THREE.Vector3(4, 1.12, 0.36)
    }

    movement.moveTo(new THREE.Vector3(2, 0.32, 0.12), undefined, bounds, 'seabed', true)
    for (let index = 0; index < 80; index += 1) {
      movement.update(0.05)
      expect(root.position.z).toBeCloseTo(0.12, 8)
    }
  })

  it('lifts from a grounded pose into a hovered seabed move without a first-frame snap', () => {
    const root = new THREE.Group()
    root.position.set(0.24, 0, -0.18)
    const movement = new MovementController(root, 1.2, 0.4, () => 0.8)

    movement.moveTo(new THREE.Vector3(0.9, 0.32, -0.4), undefined, undefined, 'seabed')
    movement.update(1 / 60)

    expect(root.position.y).toBeGreaterThanOrEqual(0)
    expect(root.position.y).toBeLessThan(0.02)
    expect(root.position.x).toBeGreaterThan(0.24)

    for (let index = 0; index < 120; index += 1) movement.update(1 / 60)
    expect(root.position.y).toBeCloseTo(0.32, 6)
  })

  it('keeps a purely vertical swim descent on the same XZ coordinates', () => {
    const root = new THREE.Group()
    root.position.set(0.24, 0.32, -0.18)
    const movement = new MovementController(root, 1.2, Math.PI * 0.37, () => 0.8)

    movement.moveTo(new THREE.Vector3(0.24, 0, -0.18), undefined, undefined, 'swim')
    for (let index = 0; index < 30; index += 1) {
      movement.update(0.05)
      expect(root.position.x).toBeCloseTo(0.24, 8)
      expect(root.position.z).toBeCloseTo(-0.18, 8)
    }

    expect(root.position.y).toBe(0)
  })

  it('keeps every sampled point of an edge path inside the declared swim bounds', () => {
    const root = new THREE.Group()
    root.position.set(0, 0.31, 0)
    const movement = new MovementController(root, 1.2, 0, () => 1)
    const bounds = {
      min: new THREE.Vector3(-0.1, 0.28, -0.1),
      max: new THREE.Vector3(0.1, 0.4, 0.1)
    }

    movement.swimNear(bounds.max, new THREE.Vector3(), bounds)
    for (let index = 0; index < 40; index += 1) {
      movement.update(0.1)
      expect(root.position.x).toBeGreaterThanOrEqual(bounds.min.x)
      expect(root.position.x).toBeLessThanOrEqual(bounds.max.x)
      expect(root.position.y).toBeGreaterThanOrEqual(bounds.min.y)
      expect(root.position.y).toBeLessThanOrEqual(bounds.max.y)
      expect(root.position.z).toBeGreaterThanOrEqual(bounds.min.z)
      expect(root.position.z).toBeLessThanOrEqual(bounds.max.z)
    }
  })

  it('reclamps an active path when presentation bounds change', () => {
    const root = new THREE.Group()
    const movement = new MovementController(root, 1.2, 0.2, () => 0.8)

    movement.moveTo(new THREE.Vector3(1.2, 0.32, -0.4), undefined, undefined, 'seabed')
    movement.update(0.2)
    movement.constrainHorizontal({
      min: new THREE.Vector3(-0.18, 0, -1),
      max: new THREE.Vector3(0.18, 1, 1)
    })

    for (let index = 0; index < 40; index += 1) {
      movement.update(0.1)
      expect(root.position.x).toBeGreaterThanOrEqual(-0.18)
      expect(root.position.x).toBeLessThanOrEqual(0.18)
    }
    expect(root.position.x).toBeCloseTo(0.18, 8)
  })

  it('turns toward a reversed seabed direction before meaningful translation begins', () => {
    const root = new THREE.Group()
    root.position.set(-3.4, 0.32, -0.46)
    root.rotation.y = THREE.MathUtils.degToRad(-58)
    const movement = new MovementController(root, 1.2, 0.4, () => 0.8)

    movement.moveTo(new THREE.Vector3(0.2, 0.32, -0.68), undefined, undefined, 'seabed')
    const before = root.position.clone()
    movement.update(0.12)

    expect(root.rotation.y).toBeGreaterThan(0)
    expect(root.position.distanceTo(before)).toBeLessThan(0.01)
  })

  it('lets the debug turn-speed scale slow both travel and return rotation', () => {
    const createMovement = (scale: number): { root: THREE.Group; movement: MovementController } => {
      const root = new THREE.Group()
      root.position.set(-3.4, 0.32, -0.46)
      root.rotation.y = THREE.MathUtils.degToRad(-58)
      const movement = new MovementController(root, 1.2, 0.4, () => 0.8)
      movement.setTurnSpeedScale(scale)
      movement.moveTo(new THREE.Vector3(0.2, 0.32, -0.68), undefined, undefined, 'seabed')
      return { root, movement }
    }

    const normal = createMovement(1)
    const slow = createMovement(0.5)
    normal.movement.update(0.08)
    slow.movement.update(0.08)
    expect(slow.root.rotation.y).toBeLessThan(normal.root.rotation.y)

    normal.movement.cancel()
    slow.movement.cancel()
    normal.root.rotation.y = THREE.MathUtils.degToRad(50)
    slow.root.rotation.y = THREE.MathUtils.degToRad(50)
    normal.movement.update(0.2)
    slow.movement.update(0.2)
    expect(Math.abs(slow.root.rotation.y)).toBeGreaterThan(Math.abs(normal.root.rotation.y))
  })

  it('keeps position continuous when a second seabed move replaces an active path', () => {
    const root = new THREE.Group()
    root.position.set(0, 0.32, 0)
    const movement = new MovementController(root, 1.2, 0.4, () => 0.8)
    const bounds = {
      min: new THREE.Vector3(-2.15, 0, -1.42),
      max: new THREE.Vector3(2.15, 1.12, 0.36)
    }

    movement.moveTo(new THREE.Vector3(-1.05, 0.32, -0.46), undefined, bounds, 'seabed')
    for (let index = 0; index < 8; index += 1) movement.update(0.05)
    const beforeRedirect = root.position.clone()

    movement.moveTo(new THREE.Vector3(1.05, 0.32, -0.68), undefined, bounds, 'seabed')
    expect(root.position.toArray()).toEqual(beforeRedirect.toArray())
    movement.update(1 / 60)

    expect(root.position.distanceTo(beforeRedirect)).toBeLessThan(0.01)
    expect(root.position.x).toBeGreaterThan(beforeRedirect.x)
  })

  it('does not snap to a newly narrowed bound when redirecting from the current position', () => {
    const root = new THREE.Group()
    root.position.set(-0.82, 0.32, -0.28)
    const movement = new MovementController(root, 1.2, 0.4, () => 0.8)
    const narrowedBounds = {
      min: new THREE.Vector3(-0.18, 0, -1.42),
      max: new THREE.Vector3(0.18, 1.12, 0.36)
    }
    const beforeRedirect = root.position.clone()

    movement.moveTo(new THREE.Vector3(0.18, 0.32, -0.68), undefined, narrowedBounds, 'seabed')
    expect(root.position.toArray()).toEqual(beforeRedirect.toArray())
    movement.update(1 / 60)

    expect(root.position.distanceTo(beforeRedirect)).toBeLessThan(0.02)
    expect(root.position.x).toBeGreaterThan(beforeRedirect.x)
  })

  it('chooses a random directional Move distance instead of crossing edge-to-edge in one command', () => {
    const bounds = {
      min: new THREE.Vector3(-4, 0, -1.42),
      max: new THREE.Vector3(4, 1.12, 0.36)
    }
    const current = new THREE.Vector3(-3.8, 0.32, -0.46)
    const shortest = createDirectionalMoveTarget(current, 'b', bounds, () => 0)
    const longest = createDirectionalMoveTarget(current, 'b', bounds, () => 1)

    expect(shortest.x).toBeGreaterThan(current.x)
    expect(longest.x).toBeGreaterThan(shortest.x)
    expect(longest.x).toBeLessThan(bounds.max.x)
    expect(shortest.x - current.x).toBeCloseTo(8 * 0.18, 8)
    expect(longest.x - current.x).toBeCloseTo(8 * 0.42, 8)

    const nearRightEdge = new THREE.Vector3(3.7, 0.32, -0.68)
    const clamped = createDirectionalMoveTarget(nearRightEdge, 'b', bounds, () => 1)
    expect(clamped.x).toBe(4)

    const atRightEdge = new THREE.Vector3(4, 0.32, -0.2)
    const noOp = createDirectionalMoveTarget(atRightEdge, 'b', bounds, () => 1)
    expect(noOp.toArray()).toEqual(atRightEdge.toArray())
  })

  it('preserves the current depth for directional moves in a multi-resident layout', () => {
    const bounds = {
      min: new THREE.Vector3(-4, 0, -1.42),
      max: new THREE.Vector3(4, 1.12, 0.36)
    }
    const current = new THREE.Vector3(0, 0.32, 0.11)

    const singleResident = createDirectionalMoveTarget(current, 'a', bounds, () => 0.5)
    const multiResident = createDirectionalMoveTarget(current, 'a', bounds, () => 0.5, true)

    expect(singleResident.z).toBeCloseTo(-0.46, 8)
    expect(multiResident.z).toBeCloseTo(current.z, 8)
    expect(multiResident.x).toBeLessThan(current.x)
  })

  it('derives horizontal swim volume from viewport width instead of a fixed world cap', () => {
    const portrait = new THREE.PerspectiveCamera(49, 0.55, 0.1, 100)
    portrait.position.set(0, 1.22, 4.15)
    const portraitBounds = createScreenSafeSwimBounds(portrait, 1.6)

    const landscape = new THREE.PerspectiveCamera(49, 16 / 9, 0.1, 100)
    landscape.position.copy(portrait.position)
    const landscapeBounds = createScreenSafeSwimBounds(landscape, 1.6)

    expect(portraitBounds.max.x).toBeLessThan(1)
    expect(landscapeBounds.max.x).toBeGreaterThan(2.15)
    expect(landscapeBounds.max.x).toBeGreaterThan(portraitBounds.max.x * 4)
    expect(portraitBounds.min.x).toBeCloseTo(-portraitBounds.max.x)
    expect(landscapeBounds.min.x).toBeCloseTo(-landscapeBounds.max.x)
    expect(portraitBounds.min.y).toBe(0)
    expect(portraitBounds.max.y).toBe(1.12)
  })

  it('places two residents at one-third/two-thirds of the safe width on the same depth', () => {
    const narrowBounds = {
      min: new THREE.Vector3(-2, 0, -1.4),
      max: new THREE.Vector3(2, 1.12, 0.4)
    }
    const wideBounds = {
      min: new THREE.Vector3(-4, 0, -1.4),
      max: new THREE.Vector3(4, 1.12, 0.4)
    }

    const narrow = createTwoResidentInitialSlots(narrowBounds, -0.2)
    const wide = createTwoResidentInitialSlots(wideBounds, -0.2)

    expect(narrow[0].x).toBeCloseTo(-2 / 3, 8)
    expect(narrow[1].x).toBeCloseTo(2 / 3, 8)
    expect(wide[0].x).toBeCloseTo(-4 / 3, 8)
    expect(wide[1].x).toBeCloseTo(4 / 3, 8)
    expect((narrow[0].x - narrowBounds.min.x) / 4).toBeCloseTo(1 / 3, 8)
    expect((narrow[1].x - narrowBounds.min.x) / 4).toBeCloseTo(2 / 3, 8)
    expect(narrow[0].z).toBeCloseTo(-0.2, 8)
    expect(narrow[1].z).toBeCloseTo(-0.2, 8)
  })

  it('places three residents at 25/50/75% with the center slightly forward', () => {
    const narrowBounds = {
      min: new THREE.Vector3(-2, 0, -1.4),
      max: new THREE.Vector3(2, 1.12, 0.4)
    }
    const wideBounds = {
      min: new THREE.Vector3(-4, 0, -1.4),
      max: new THREE.Vector3(4, 1.12, 0.4)
    }

    const narrow = createThreeResidentInitialSlots(narrowBounds, -0.2)
    const wide = createThreeResidentInitialSlots(wideBounds, -0.2)

    expect(narrow.map((slot) => slot.x)).toEqual([-1, 0, 1])
    expect(wide.map((slot) => slot.x)).toEqual([-2, 0, 2])
    expect(narrow[1].z).toBeGreaterThan(narrow[0].z)
    expect(narrow[2].z).toBeCloseTo(narrow[0].z, 8)
    expect(narrow[1].z - narrow[0].z).toBeCloseTo(1.8 * 0.10, 8)
  })

  it('narrows movement width when the current camera zooms closer', () => {
    const camera = new THREE.PerspectiveCamera(56, 16 / 9, 0.1, 100)
    camera.position.set(0, 1.22, 5.36)
    const wideBounds = createScreenSafeSwimBounds(camera, 1.6)

    camera.position.set(0, 1.1, 3.2)
    const zoomedBounds = createScreenSafeSwimBounds(camera, 1.6)

    expect(zoomedBounds.max.x).toBeLessThan(wideBounds.max.x)
    expect(zoomedBounds.min.x).toBeGreaterThan(wideBounds.min.x)
  })
})
