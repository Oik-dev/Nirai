import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import {
  resolveBoomCameraPosition,
  resolveFocusAim,
  resolveFocusDistance,
  resolvePerspectiveFitDistance,
  resolveWorldGroupAim
} from '../../src/renderer/src/runtime/CameraFraming'

describe('camera framing', () => {
  it('computes a wide distance that fits the full pose and grows with padding', () => {
    const size = new THREE.Vector3(1.0, 1.8, 0.4)
    const base = resolvePerspectiveFitDistance(size, 56, 16 / 9, 0.1)
    const padded = resolvePerspectiveFitDistance(size, 56, 16 / 9, 0.3)

    expect(base).toBeGreaterThan(0)
    expect(padded).toBeGreaterThan(base)
  })

  it('moves the Focus aim from full-body center toward the face as zoom increases', () => {
    const body = new THREE.Vector3(0, 0.9, 0)
    const face = new THREE.Vector3(0, 1.65, 0)

    expect(resolveFocusAim(body, face, 0).distanceTo(body)).toBeCloseTo(0, 8)
    expect(resolveFocusAim(body, face, 1).distanceTo(face)).toBeCloseTo(0, 8)
    expect(resolveFocusAim(body, face, 0.5).y).toBeCloseTo(1.275, 8)
  })

  it('lets close Focus zoom crop the lower body instead of enforcing the wide distance', () => {
    expect(resolveFocusDistance(2.6, 0.9, 0)).toBeCloseTo(2.6, 8)
    expect(resolveFocusDistance(2.6, 0.9, 0.5)).toBeCloseTo(1.75, 8)
    expect(resolveFocusDistance(2.6, 0.9, 1)).toBeCloseTo(0.9, 8)
  })

  it('centers multi-resident World framing on the visual group without moving single-resident framing', () => {
    const baseAim = new THREE.Vector3(0, 1.2, -0.72)
    const groupCenter = new THREE.Vector3(-0.44, 1.0, -0.5)

    expect(resolveWorldGroupAim(baseAim, groupCenter, 1).x).toBeCloseTo(0, 8)
    expect(resolveWorldGroupAim(baseAim, groupCenter, 2).x).toBeCloseTo(-0.44, 8)
    expect(resolveWorldGroupAim(baseAim, groupCenter, 3).x).toBeCloseTo(-0.44, 8)
    expect(baseAim.x).toBeCloseTo(0, 8)
  })

  it('keeps the camera above the seabed while preserving the fixed boom direction when possible', () => {
    const aim = new THREE.Vector3(0, 0.15, 0)
    const boom = new THREE.Vector3(0, 0.3, 1).normalize()
    const free = resolveBoomCameraPosition(aim, boom, 2, 0.32)
    const clamped = resolveBoomCameraPosition(new THREE.Vector3(0, -0.4, 0), boom, 0.5, 0.32)

    expect(free.z).toBeGreaterThan(0)
    expect(free.y).toBeGreaterThan(0.32)
    expect(clamped.y).toBeCloseTo(0.32, 8)
  })
})
