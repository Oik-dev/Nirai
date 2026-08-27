import * as THREE from 'three'

// Locked former Move B presentation. Yaw/pitch/roll limits and damping are
// the approved travel feel. Do not replace with a "more underwater" retune.
const MAX_BODY_YAW = THREE.MathUtils.degToRad(28)
const MAX_BODY_PITCH = THREE.MathUtils.degToRad(6)
const MAX_SWIM_ROLL = THREE.MathUtils.degToRad(18)
const MAX_SEABED_ROLL = THREE.MathUtils.degToRad(1.8)
const MAX_IDLE_YAW = THREE.MathUtils.degToRad(8)
const TURN_DAMPING = 4.2
const BANK_DAMPING = 3.4
const SEABED_EPSILON = 0.035

export type LocomotionMedium = 'seabed' | 'swim'

export interface SwimBounds {
  readonly min: THREE.Vector3
  readonly max: THREE.Vector3
}

export class MovementController {
  private readonly start = new THREE.Vector3()
  private readonly controlA = new THREE.Vector3()
  private readonly controlB = new THREE.Vector3()
  private readonly direction = new THREE.Vector3()
  private target: THREE.Vector3 | null = null
  private onArrive: (() => void) | null = null
  private progress = 0
  private pathLength = 1
  private pathSerial = 0
  private readonly idleYaw: number
  private medium: LocomotionMedium = 'swim'

  constructor(
    private readonly root: THREE.Object3D,
    private readonly speed = 1.2,
    private readonly pathPhase = 0,
    private readonly random: () => number = Math.random
  ) {
    this.idleYaw = Math.sin(pathPhase * 1.37 + 0.71) * MAX_IDLE_YAW
  }

  get isMoving(): boolean {
    return this.target !== null
  }

  get locomotionMedium(): LocomotionMedium {
    return this.medium
  }

  swimNear(
    anchor: THREE.Vector3,
    radius: THREE.Vector3,
    bounds: SwimBounds,
    onArrive?: () => void
  ): THREE.Vector3 {
    const target = new THREE.Vector3(
      anchor.x + (this.random() * 2 - 1) * radius.x,
      anchor.y + (this.random() * 2 - 1) * radius.y,
      anchor.z + (this.random() * 2 - 1) * radius.z
    ).clamp(bounds.min, bounds.max)
    this.root.position.x = THREE.MathUtils.clamp(this.root.position.x, bounds.min.x, bounds.max.x)
    this.root.position.z = THREE.MathUtils.clamp(this.root.position.z, bounds.min.z, bounds.max.z)
    this.moveTo(target, onArrive, bounds, 'swim')
    return target.clone()
  }

  moveTo(
    target: THREE.Vector3,
    onArrive: (() => void) | undefined,
    bounds: SwimBounds | undefined,
    medium: LocomotionMedium
  ): void {
    this.start.copy(this.root.position)
    this.target = target.clone()
    this.onArrive = onArrive ?? null
    this.progress = 0
    this.pathSerial += 1
    this.medium = medium

    const direct = this.target.clone().sub(this.start)
    const directDistance = direct.length()
    const flatDirection = new THREE.Vector3(direct.x, 0, direct.z)
    const horizontalDistance = flatDirection.length()
    const arcSign = Math.sin(this.pathSerial * 1.618 + this.pathPhase) >= 0 ? 1 : -1
    const variation = 0.72 + this.random() * 0.56
    const perpendicular = horizontalDistance > 1e-5
      ? new THREE.Vector3(-flatDirection.z, 0, flatDirection.x).normalize()
      : new THREE.Vector3(Math.cos(this.pathPhase), 0, Math.sin(this.pathPhase))
    const arcAmount = medium === 'seabed'
      ? Math.min(0.24, horizontalDistance * 0.065 * variation)
      : horizontalDistance <= 1e-5
        ? 0
        : Math.min(0.82, (0.14 + horizontalDistance * 0.19) * variation)
    const verticalLift = medium === 'seabed'
      ? 0
      : Math.min(0.32, 0.10 + directDistance * 0.075)

    this.controlA.copy(this.start).addScaledVector(direct, 0.31)
    this.controlA.addScaledVector(perpendicular, arcAmount * arcSign)
    this.controlA.y += verticalLift

    this.controlB.copy(this.start).addScaledVector(direct, 0.70)
    this.controlB.addScaledVector(perpendicular, arcAmount * -0.38 * arcSign)
    this.controlB.y += verticalLift * 0.58

    if (medium === 'seabed') {
      const floorY = Math.abs(this.target.y) <= SEABED_EPSILON ? 0 : this.target.y
      this.start.y = floorY
      this.controlA.y = floorY
      this.controlB.y = floorY
      this.target.y = floorY
    }

    if (bounds) {
      this.start.x = THREE.MathUtils.clamp(this.start.x, bounds.min.x, bounds.max.x)
      this.start.z = THREE.MathUtils.clamp(this.start.z, bounds.min.z, bounds.max.z)
      this.controlA.clamp(bounds.min, bounds.max)
      this.controlB.clamp(bounds.min, bounds.max)
      this.target.clamp(bounds.min, bounds.max)
    }

    this.pathLength = Math.max(
      0.001,
      directDistance * (medium === 'seabed' ? 1.32 : 1.12 + arcAmount * 0.12)
    )
  }

  settleAt(minimumHeight: number, bounds: SwimBounds, onArrive?: () => void): void {
    const coastDirection = this.direction.lengthSq() > 1e-5
      ? this.direction.clone()
      : new THREE.Vector3()
    const target = this.root.position.clone().addScaledVector(coastDirection, 0.34)
    target.y = Math.max(minimumHeight, target.y)
    target.clamp(bounds.min, bounds.max)
    this.moveTo(target, onArrive, bounds, 'swim')
  }

  constrainHorizontal(bounds: SwimBounds): void {
    for (const point of [this.root.position, this.start, this.controlA, this.controlB]) {
      point.x = THREE.MathUtils.clamp(point.x, bounds.min.x, bounds.max.x)
      point.z = THREE.MathUtils.clamp(point.z, bounds.min.z, bounds.max.z)
    }
    if (this.target) {
      this.target.x = THREE.MathUtils.clamp(this.target.x, bounds.min.x, bounds.max.x)
      this.target.z = THREE.MathUtils.clamp(this.target.z, bounds.min.z, bounds.max.z)
    }
  }

  update(delta: number): void {
    if (delta <= 0) {
      return
    }

    if (!this.target) {
      this.root.rotation.y = THREE.MathUtils.damp(
        this.root.rotation.y,
        this.idleYaw,
        TURN_DAMPING,
        delta
      )
      this.root.rotation.x = THREE.MathUtils.damp(this.root.rotation.x, 0, BANK_DAMPING, delta)
      this.root.rotation.z = THREE.MathUtils.damp(this.root.rotation.z, 0, BANK_DAMPING, delta)
      return
    }

    const mediumSpeed = this.medium === 'seabed' ? this.speed * 0.58 : this.speed * 0.82
    this.progress = Math.min(1, this.progress + (mediumSpeed * delta) / this.pathLength)
    const t = smootherStep(this.progress)
    const inverse = 1 - t

    this.root.position.set(
      inverse * inverse * inverse * this.start.x
        + 3 * inverse * inverse * t * this.controlA.x
        + 3 * inverse * t * t * this.controlB.x
        + t * t * t * this.target.x,
      inverse * inverse * inverse * this.start.y
        + 3 * inverse * inverse * t * this.controlA.y
        + 3 * inverse * t * t * this.controlB.y
        + t * t * t * this.target.y,
      inverse * inverse * inverse * this.start.z
        + 3 * inverse * inverse * t * this.controlA.z
        + 3 * inverse * t * t * this.controlB.z
        + t * t * t * this.target.z
    )

    this.direction.set(
      3 * inverse * inverse * (this.controlA.x - this.start.x)
        + 6 * inverse * t * (this.controlB.x - this.controlA.x)
        + 3 * t * t * (this.target.x - this.controlB.x),
      3 * inverse * inverse * (this.controlA.y - this.start.y)
        + 6 * inverse * t * (this.controlB.y - this.controlA.y)
        + 3 * t * t * (this.target.y - this.controlB.y),
      3 * inverse * inverse * (this.controlA.z - this.start.z)
        + 6 * inverse * t * (this.controlB.z - this.controlA.z)
        + 3 * t * t * (this.target.z - this.controlB.z)
    )
    const directionLength = this.direction.length()
    if (directionLength > 1e-5) {
      this.direction.multiplyScalar(1 / directionLength)
    }

    const desiredYaw = THREE.MathUtils.clamp(
      this.idleYaw * 0.28 + this.direction.x * MAX_BODY_YAW,
      -MAX_BODY_YAW,
      MAX_BODY_YAW
    )
    const pitchLimit = this.medium === 'seabed' ? MAX_BODY_PITCH * 0.18 : MAX_BODY_PITCH
    const rollLimit = this.medium === 'seabed' ? MAX_SEABED_ROLL : MAX_SWIM_ROLL
    const desiredPitch = THREE.MathUtils.clamp(-this.direction.y * pitchLimit, -pitchLimit, pitchLimit)
    const desiredRoll = THREE.MathUtils.clamp(-this.direction.x * rollLimit, -rollLimit, rollLimit)
    this.root.rotation.y = THREE.MathUtils.damp(this.root.rotation.y, desiredYaw, TURN_DAMPING, delta)
    this.root.rotation.x = THREE.MathUtils.damp(this.root.rotation.x, desiredPitch, BANK_DAMPING, delta)
    this.root.rotation.z = THREE.MathUtils.damp(this.root.rotation.z, desiredRoll, BANK_DAMPING, delta)

    if (this.progress >= 1) {
      this.root.position.copy(this.target)
      const onArrive = this.onArrive
      this.target = null
      this.onArrive = null
      this.progress = 0
      onArrive?.()
    }
  }

  cancel(): void {
    this.target = null
    this.onArrive = null
    this.progress = 0
  }
}

function smootherStep(value: number): number {
  return value * value * value * (value * (value * 6 - 15) + 10)
}
