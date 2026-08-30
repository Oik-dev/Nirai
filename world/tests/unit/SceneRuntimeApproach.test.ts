import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import {
  MAX_GROUP_CHAT_PARTICIPANTS,
  RESIDENT_CHAT_APPROACH_DISTANCE,
  resolveGroupConversationSlots,
  resolveResidentChatApproachPlan
} from '../../src/renderer/src/runtime/SceneRuntime'

describe('resident chat approach planning', () => {
  const bounds = {
    min: new THREE.Vector3(-2, 0, -2),
    max: new THREE.Vector3(2, 1, 1)
  }

  it('treats the second conversation as already arrived when roots remain at conversation distance', () => {
    const plan = resolveResidentChatApproachPlan(
      new THREE.Vector3(RESIDENT_CHAT_APPROACH_DISTANCE, 0.32, 0),
      new THREE.Vector3(0, 0.32, 0),
      new THREE.Vector3(0.96, 0.32, 0),
      new THREE.Vector3(0, 0.32, 0),
      bounds
    )

    expect(plan.arrived).toBe(true)
    expect(plan.destination).toBeNull()
  })

  it('uses presentation separation only to choose a direction when logical roots still need spacing', () => {
    const plan = resolveResidentChatApproachPlan(
      new THREE.Vector3(0, 0.32, 0),
      new THREE.Vector3(0, 0.32, 0),
      new THREE.Vector3(-0.48, 0.32, 0),
      new THREE.Vector3(0.48, 0.32, 0),
      bounds
    )

    expect(plan.arrived).toBe(false)
    expect(plan.destination?.x).toBeCloseTo(-RESIDENT_CHAT_APPROACH_DISTANCE, 8)
    expect(plan.destination?.y).toBeCloseTo(0.32, 8)
  })

  it('creates a three-resident conversation formation inside screen-safe bounds', () => {
    const slots = resolveGroupConversationSlots([
      new THREE.Vector3(-1.2, 0.32, 0),
      new THREE.Vector3(0, 0.32, 0),
      new THREE.Vector3(1.2, 0.32, 0)
    ], bounds)

    expect(slots).toHaveLength(3)
    expect(new Set(slots.map((slot) => `${slot.x.toFixed(4)}:${slot.z.toFixed(4)}`)).size).toBe(3)
    const sorted = [...slots].sort((left, right) => left.x - right.x)
    expect(sorted[1].z).toBeLessThan(sorted[0].z)
    expect(sorted[1].z).toBeLessThan(sorted[2].z)
    expect(sorted[0].distanceTo(sorted[1])).toBeGreaterThan(0.8)
    expect(sorted[1].distanceTo(sorted[2])).toBeGreaterThan(0.8)
    for (const slot of slots) {
      expect(slot.x).toBeGreaterThanOrEqual(bounds.min.x)
      expect(slot.x).toBeLessThanOrEqual(bounds.max.x)
      expect(slot.z).toBeGreaterThanOrEqual(bounds.min.z)
      expect(slot.z).toBeLessThanOrEqual(bounds.max.z)
      expect(slot.y).toBeCloseTo(0.32, 8)
    }
  })

  it('uses the same formation algorithm for ten residents', () => {
    const positions = Array.from({ length: MAX_GROUP_CHAT_PARTICIPANTS }, (_, index) =>
      new THREE.Vector3((index - 4.5) * 0.12, 0.32, 0)
    )
    const slots = resolveGroupConversationSlots(positions, bounds)

    expect(slots).toHaveLength(MAX_GROUP_CHAT_PARTICIPANTS)
    for (const slot of slots) {
      expect(slot.x).toBeGreaterThanOrEqual(bounds.min.x)
      expect(slot.x).toBeLessThanOrEqual(bounds.max.x)
      expect(slot.z).toBeGreaterThanOrEqual(bounds.min.z)
      expect(slot.z).toBeLessThanOrEqual(bounds.max.z)
    }
  })
})
