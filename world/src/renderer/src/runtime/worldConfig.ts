import * as THREE from 'three'
import type { EnvironmentOptions } from '../world/environment/EnvironmentController'
import type { SwimBounds } from '../world/MovementController'

export const M0_WORLD_CONFIG = {
  residentName: 'M0 Resident',
  locations: {
    a: [-1.1, 0.62, -0.42],
    b: [1.05, 0.74, -0.68]
  },
  swim: {
    radius: [0.78, 0.30, 0.58],
    bounds: {
      min: [-2.15, 0.30, -1.42],
      max: [2.15, 1.12, 0.36]
    }
  },
  environment: {
    quality: 'high'
  } satisfies EnvironmentOptions
} as const

export type M0LocationName = keyof typeof M0_WORLD_CONFIG.locations

export function createScreenSafeSwimBounds(
  camera: THREE.PerspectiveCamera,
  avatarHeight: number
): SwimBounds {
  const configuredMin = new THREE.Vector3(...M0_WORLD_CONFIG.swim.bounds.min)
  const configuredMax = new THREE.Vector3(...M0_WORLD_CONFIG.swim.bounds.max)
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
