import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import { MovementController } from '../../src/renderer/src/world/MovementController'
import { createScreenSafeSwimBounds } from '../../src/renderer/src/runtime/worldConfig'

describe('MovementController', () => {
  it('moves on a shallow 3D arc while keeping the body mostly camera-facing', () => {
    const root = new THREE.Group()
    const arrived = vi.fn()
    const movement = new MovementController(root, 2)

    movement.moveTo(new THREE.Vector3(1, 0, 0), arrived)
    movement.update(0.25)

    expect(root.position.x).toBeGreaterThan(0.32)
    expect(root.position.x).toBeLessThan(0.52)
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

    first.moveTo(new THREE.Vector3(2, 0, 0))
    second.moveTo(new THREE.Vector3(2, 0, 0))
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

    movement.moveTo(new THREE.Vector3(1.8, 0.9, -1.1))
    movement.update(1.2)

    expect(root.position.y).toBeGreaterThan(0.2)
    expect(root.position.z).toBeLessThan(-0.2)
    expect(Math.abs(root.rotation.x)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(6))
    expect(Math.abs(root.rotation.z)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(7))
    expect(Math.abs(root.rotation.y)).toBeLessThanOrEqual(THREE.MathUtils.degToRad(28))
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

  it('narrows the horizontal swim volume for a portrait viewport and avatar width', () => {
    const camera = new THREE.PerspectiveCamera(49, 0.55, 0.1, 100)
    camera.position.set(0, 1.22, 4.15)
    const bounds = createScreenSafeSwimBounds(camera, 1.6)

    expect(bounds.max.x).toBeLessThan(1)
    expect(bounds.min.x).toBeCloseTo(-bounds.max.x)
    expect(bounds.min.y).toBe(0.3)
    expect(bounds.max.y).toBe(1.12)
  })
})
