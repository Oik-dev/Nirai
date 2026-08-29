import * as THREE from 'three'
import type { EnvironmentOptions } from '../world/environment/EnvironmentController'
import type { SwimBounds } from '../world/MovementController'

// Locked M0 presentation. Location depth, vertical/depth swim limits, and
// environment quality are part of the approved look and Move B feel. Horizontal
// movement range is derived from the viewport so wide screens can use their width.
export const M0_WORLD_CONFIG = {
  residentName: 'Lapan',
  locations: {
    a: { side: -1, z: -0.46 },
    b: { side: 1, z: -0.68 }
  },
  swim: {
    radius: [0.78, 0.30, 0.58],
    bounds: {
      min: [-2.15, 0, -1.42],
      max: [2.15, 1.12, 0.36]
    }
  },
  environment: {
    quality: 'high'
  } satisfies EnvironmentOptions
} as const

export type M0LocationName = keyof typeof M0_WORLD_CONFIG.locations

const DIRECTIONAL_MOVE_MIN_WIDTH_RATIO = 0.18
const DIRECTIONAL_MOVE_MAX_WIDTH_RATIO = 0.42
const DIRECTIONAL_MOVE_EDGE_EPSILON = 0.03

export function createDirectionalMoveTarget(
  current: THREE.Vector3,
  location: M0LocationName,
  bounds: SwimBounds,
  random: () => number = Math.random
): THREE.Vector3 {
  const { side, z } = M0_WORLD_CONFIG.locations[location]
  const width = Math.max(0, bounds.max.x - bounds.min.x)
  const minimumDistance = width * DIRECTIONAL_MOVE_MIN_WIDTH_RATIO
  const maximumDistance = Math.max(minimumDistance, width * DIRECTIONAL_MOVE_MAX_WIDTH_RATIO)
  const requestedDistance = THREE.MathUtils.lerp(
    minimumDistance,
    maximumDistance,
    THREE.MathUtils.clamp(random(), 0, 1)
  )
  const edgeX = side < 0 ? bounds.min.x : bounds.max.x
  const availableDistance = Math.abs(edgeX - current.x)
  if (availableDistance < DIRECTIONAL_MOVE_EDGE_EPSILON) {
    return current.clone()
  }
  const distance = Math.min(requestedDistance, availableDistance)

  return new THREE.Vector3(
    THREE.MathUtils.clamp(current.x + side * distance, bounds.min.x, bounds.max.x),
    current.y,
    THREE.MathUtils.clamp(z, bounds.min.z, bounds.max.z)
  )
}

export function createConfiguredSwimBounds(): SwimBounds {
  return {
    min: new THREE.Vector3(...M0_WORLD_CONFIG.swim.bounds.min),
    max: new THREE.Vector3(...M0_WORLD_CONFIG.swim.bounds.max)
  }
}

export function createScreenSafeSwimBounds(
  camera: THREE.PerspectiveCamera,
  avatarHeight: number,
  referenceCameraZ = camera.position.z
): SwimBounds {
  const configured = createConfiguredSwimBounds()
  const configuredMin = configured.min
  const configuredMax = configured.max
  const nearestDepth = Math.max(camera.near, referenceCameraZ - configuredMax.z)
  const verticalHalfSpan = Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5)) * nearestDepth
  const avatarHalfWidth = Math.max(0.28, avatarHeight * 0.24)
  const visibleCenterHalfWidth = Math.max(
    0.1,
    verticalHalfSpan * camera.aspect - avatarHalfWidth
  )
  const horizontalLimit = visibleCenterHalfWidth

  return {
    min: new THREE.Vector3(-horizontalLimit, configuredMin.y, configuredMin.z),
    max: new THREE.Vector3(horizontalLimit, configuredMax.y, configuredMax.z)
  }
}
