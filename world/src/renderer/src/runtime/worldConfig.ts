import * as THREE from 'three'
import type { EnvironmentOptions } from '../world/environment/EnvironmentController'
import type { SwimBounds } from '../world/MovementController'

// Locked M0 presentation. Location XY, swim volume, and environment quality
// are part of the approved look and Move B feel. Do not retune here as cleanup.
export const M0_WORLD_CONFIG = {
  residentName: 'M0 Resident',
  locations: {
    a: [-1.05, -0.46],
    b: [1.05, -0.68]
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

export function createConfiguredSwimBounds(): SwimBounds {
  return {
    min: new THREE.Vector3(...M0_WORLD_CONFIG.swim.bounds.min),
    max: new THREE.Vector3(...M0_WORLD_CONFIG.swim.bounds.max)
  }
}

export function createScreenSafeSwimBounds(
  camera: THREE.PerspectiveCamera,
  avatarHeight: number
): SwimBounds {
  const configured = createConfiguredSwimBounds()
  const configuredMin = configured.min
  const configuredMax = configured.max
  const nearestDepth = Math.max(camera.near, camera.position.z - configuredMax.z)
  const verticalHalfSpan = Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5)) * nearestDepth
  const avatarHalfWidth = Math.max(0.28, avatarHeight * 0.24)
  const visibleCenterHalfWidth = Math.max(
    0.1,
    verticalHalfSpan * camera.aspect - avatarHalfWidth
  )
  const horizontalLimit = Math.min(
    Math.abs(configuredMin.x),
    configuredMax.x,
    visibleCenterHalfWidth
  )

  return {
    min: new THREE.Vector3(-horizontalLimit, configuredMin.y, configuredMin.z),
    max: new THREE.Vector3(horizontalLimit, configuredMax.y, configuredMax.z)
  }
}
