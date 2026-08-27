import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import { MovementController } from '../../src/renderer/src/world/MovementController'
import { createScreenSafeSwimBounds } from '../../src/renderer/src/runtime/worldConfig'

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

  it('narrows the horizontal swim volume for a portrait viewport and avatar width', () => {
    const camera = new THREE.PerspectiveCamera(49, 0.55, 0.1, 100)
    camera.position.set(0, 1.22, 4.15)
    const bounds = createScreenSafeSwimBounds(camera, 1.6)

    expect(bounds.max.x).toBeLessThan(1)
    expect(bounds.min.x).toBeCloseTo(-bounds.max.x)
    expect(bounds.min.y).toBe(0)
    expect(bounds.max.y).toBe(1.12)
  })
})
