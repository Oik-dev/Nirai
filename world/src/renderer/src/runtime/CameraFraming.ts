import * as THREE from 'three'

// World/Focus framing helpers. Distances and the Camera Y floor keep the
// approved rig from clipping the seabed. Do not retune as cleanup.
export function resolvePerspectiveFitDistance(
  size: THREE.Vector3,
  fovDegrees: number,
  aspect: number,
  padding: number
): number {
  const verticalTan = Math.tan(THREE.MathUtils.degToRad(fovDegrees * 0.5))
  const horizontalTan = Math.max(0.05, verticalTan * Math.max(0.05, aspect))
  const paddedWidth = size.x + padding * 2
  const paddedHeight = size.y + padding * 2
  const depthAllowance = size.z * 0.5
  return Math.max(
    paddedWidth * 0.5 / horizontalTan + depthAllowance,
    paddedHeight * 0.5 / Math.max(0.05, verticalTan) + depthAllowance
  )
}

export function resolveFocusAim(
  wideAim: THREE.Vector3,
  closeAim: THREE.Vector3,
  zoom: number
): THREE.Vector3 {
  return wideAim.clone().lerp(closeAim, THREE.MathUtils.clamp(zoom, 0, 1))
}

export function resolveFocusDistance(
  wideDistance: number,
  closeDistance: number,
  zoom: number
): number {
  return THREE.MathUtils.lerp(
    wideDistance,
    closeDistance,
    THREE.MathUtils.clamp(zoom, 0, 1)
  )
}

export function resolveWorldZoomDistance(
  farDistance: number,
  nearDistance: number,
  groupSafeDistance: number,
  zoom: number
): number {
  const effectiveFarDistance = Math.max(farDistance, groupSafeDistance)
  const effectiveNearDistance = Math.max(nearDistance, groupSafeDistance)
  return THREE.MathUtils.lerp(
    effectiveFarDistance,
    Math.min(effectiveFarDistance, effectiveNearDistance),
    THREE.MathUtils.clamp(zoom, 0, 1)
  )
}

export function resolveWorldNominalZoomDistance(
  farDistance: number,
  nearDistance: number,
  zoom: number
): number {
  const safeFarDistance = Math.max(0, farDistance)
  const safeNearDistance = THREE.MathUtils.clamp(nearDistance, 0, safeFarDistance)
  return THREE.MathUtils.lerp(
    safeFarDistance,
    safeNearDistance,
    THREE.MathUtils.clamp(zoom, 0, 1)
  )
}

export function resolvePresentationOpticalDistanceScale(
  nominalDistance: number,
  cameraDistance: number
): number {
  if (
    !Number.isFinite(nominalDistance)
    || !Number.isFinite(cameraDistance)
    || nominalDistance < 0
    || cameraDistance <= 0
  ) {
    return 1
  }
  return THREE.MathUtils.clamp(nominalDistance / cameraDistance, 0, 1)
}

export function resolvePresentationReferenceCameraZ(
  focused: boolean,
  currentCameraZ: number,
  worldAim: THREE.Vector3,
  worldBoomDirection: THREE.Vector3,
  worldDistance: number
): number {
  if (!focused) return currentCameraZ
  const direction = worldBoomDirection.clone().normalize()
  return worldAim.z + direction.z * Math.max(0, worldDistance)
}

export function resolveWorldGroupAim(
  baseAim: THREE.Vector3,
  groupCenter: THREE.Vector3,
  residentCount: number
): THREE.Vector3 {
  const aim = baseAim.clone()
  if (residentCount > 1) {
    aim.x = groupCenter.x
  }
  return aim
}

export function resolveBoomCameraPosition(
  aim: THREE.Vector3,
  boomDirection: THREE.Vector3,
  distance: number,
  minimumY: number
): THREE.Vector3 {
  const direction = boomDirection.clone().normalize()
  const position = aim.clone().addScaledVector(direction, Math.max(0, distance))
  position.y = Math.max(minimumY, position.y)
  return position
}
