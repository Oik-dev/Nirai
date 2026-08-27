import * as THREE from 'three'
import { VRMHumanBoneName, type VRM } from '@pixiv/three-vrm'

export type UnderwaterPose = 'idle' | 'seabed' | 'swim' | 'afk' | 'grounded-afk' | 'sleep'

interface BoneOffset {
  readonly node: THREE.Object3D
  readonly rotation: THREE.Quaternion
}

interface FaceAdjustment {
  readonly pitchRadians: number
  readonly yawRadians: number
}

const X_AXIS = new THREE.Vector3(1, 0, 0)
const Y_AXIS = new THREE.Vector3(0, 1, 0)
const Z_AXIS = new THREE.Vector3(0, 0, 1)

/**
 * Adds a deliberately quiet water-current layer after each VRMA mixer update.
 * The previous layer is removed before the next mixer tick, so bones that are
 * not keyed by a particular clip never accumulate rotation.
 */
export class UnderwaterMotionController {
  private elapsed = 0
  private frameDelta = 0
  private swimWeight = 0
  private legSwingElapsed = 0
  private legSwingWeight = 0
  private faceTowardViewerWeight = 0
  private readonly appliedOffsets: BoneOffset[] = []

  constructor(
    private readonly vrm: VRM,
    private readonly phase: number
  ) {}

  beginFrame(delta: number): void {
    // Rotations must be removed in the reverse order they were applied.
    // For example, base * pitch * yaw can only be restored with
    // yaw^-1 followed by pitch^-1. Removing them in forward order causes
    // non-commutative drift and can make multi-axis head adjustments spin.
    for (let index = this.appliedOffsets.length - 1; index >= 0; index -= 1) {
      const { node, rotation } = this.appliedOffsets[index]
      node.quaternion.multiply(rotation.clone().invert())
      node.quaternion.normalize()
    }
    this.appliedOffsets.length = 0
    this.frameDelta = THREE.MathUtils.clamp(delta, 0, 0.1)
    this.elapsed += this.frameDelta
  }

  apply(
    pose: UnderwaterPose,
    legSwingEnabled = false,
    faceAdjustment: FaceAdjustment | null = null
  ): void {
    if (!this.vrm.humanoid) {
      return
    }

    const targetSwimWeight = pose === 'swim' ? 1 : 0
    this.swimWeight = THREE.MathUtils.damp(
      this.swimWeight,
      targetSwimWeight,
      4.2,
      this.frameDelta
    )

    const slow = this.elapsed * 0.58 + this.phase
    const tide = Math.sin(slow)
    const counterTide = Math.sin(slow * 0.73 + 1.4)
    const stroke = Math.sin(this.elapsed * 1.12 + this.phase * 0.67)

    if (this.swimWeight > 0.0001) {
      const weight = this.swimWeight
      this.rotate(VRMHumanBoneName.Hips, X_AXIS, (-0.16 + counterTide * 0.020) * weight)
      this.rotate(VRMHumanBoneName.Spine, X_AXIS, (0.045 + tide * 0.016) * weight)
      this.rotate(VRMHumanBoneName.Chest, Z_AXIS, tide * 0.028 * weight)
      this.rotate(VRMHumanBoneName.LeftUpperArm, Z_AXIS, (-0.34 - stroke * 0.12) * weight)
      this.rotate(VRMHumanBoneName.RightUpperArm, Z_AXIS, (0.34 + stroke * 0.12) * weight)
      this.rotate(VRMHumanBoneName.LeftLowerArm, Y_AXIS, (-0.12 + counterTide * 0.06) * weight)
      this.rotate(VRMHumanBoneName.RightLowerArm, Y_AXIS, (0.12 - counterTide * 0.06) * weight)
      this.rotate(VRMHumanBoneName.LeftUpperLeg, X_AXIS, stroke * 0.065 * weight)
      this.rotate(VRMHumanBoneName.RightUpperLeg, X_AXIS, -stroke * 0.065 * weight)
      this.rotate(VRMHumanBoneName.LeftLowerLeg, X_AXIS, (-0.05 - stroke * 0.045) * weight)
      this.rotate(VRMHumanBoneName.RightLowerLeg, X_AXIS, (-0.05 + stroke * 0.045) * weight)
    }

    const targetStrength = pose === 'sleep'
      ? 0
      : pose === 'grounded-afk'
        ? 0.14
        : pose === 'afk'
          ? 1
          : pose === 'seabed'
            ? 0.46
            : 0.68
    const strength = targetStrength * (1 - this.swimWeight)
    this.rotate(VRMHumanBoneName.Spine, X_AXIS, counterTide * 0.012 * strength)
    this.rotate(VRMHumanBoneName.Chest, Z_AXIS, tide * 0.020 * strength)
    this.rotate(VRMHumanBoneName.UpperChest, Y_AXIS, counterTide * 0.016 * strength)
    this.rotate(VRMHumanBoneName.LeftShoulder, Z_AXIS, -tide * 0.014 * strength)
    this.rotate(VRMHumanBoneName.RightShoulder, Z_AXIS, tide * 0.014 * strength)
    this.rotate(VRMHumanBoneName.LeftUpperArm, Z_AXIS, -0.025 * strength - tide * 0.018 * strength)
    this.rotate(VRMHumanBoneName.RightUpperArm, Z_AXIS, 0.025 * strength + tide * 0.018 * strength)

    if (pose === 'seabed') {
      const walkSwing = Math.sin(this.elapsed * 2.45 + this.phase * 0.31)
      this.rotate(VRMHumanBoneName.LeftUpperLeg, X_AXIS, walkSwing * 0.050)
      this.rotate(VRMHumanBoneName.RightUpperLeg, X_AXIS, -walkSwing * 0.050)
      this.rotate(VRMHumanBoneName.LeftLowerLeg, X_AXIS, -walkSwing * 0.020)
      this.rotate(VRMHumanBoneName.RightLowerLeg, X_AXIS, walkSwing * 0.020)
    }

    const targetFaceTowardViewerWeight = faceAdjustment ? 1 : 0
    this.faceTowardViewerWeight = THREE.MathUtils.damp(
      this.faceTowardViewerWeight,
      targetFaceTowardViewerWeight,
      4.4,
      this.frameDelta
    )
    if (this.faceTowardViewerWeight > 0.0001 && faceAdjustment) {
      // Public AFK-05: keep the body authored as-is and adjust only the face.
      this.rotate(
        VRMHumanBoneName.Head,
        X_AXIS,
        faceAdjustment.pitchRadians * this.faceTowardViewerWeight
      )
      this.rotate(
        VRMHumanBoneName.Head,
        Y_AXIS,
        faceAdjustment.yawRadians * this.faceTowardViewerWeight
      )
    }

    if (legSwingEnabled) {
      // Keep Move B's established onset unchanged; only its release is softened.
      this.legSwingWeight = 1
      this.legSwingElapsed += this.frameDelta
    } else if (this.legSwingWeight > 0.0001) {
      this.legSwingWeight = THREE.MathUtils.damp(
        this.legSwingWeight,
        0,
        5.2,
        this.frameDelta
      )
      this.legSwingElapsed += this.frameDelta
    } else {
      this.legSwingWeight = 0
      this.legSwingElapsed = 0
    }

    if (this.legSwingWeight > 0.0001) {
      const legSwing = Math.sin(this.legSwingElapsed * 2.45) * this.legSwingWeight
      this.rotate(VRMHumanBoneName.LeftUpperLeg, X_AXIS, legSwing * 0.18)
      this.rotate(VRMHumanBoneName.RightUpperLeg, X_AXIS, -legSwing * 0.18)
    }
  }

  dispose(): void {
    this.beginFrame(0)
  }

  getSwimWeight(): number {
    return this.swimWeight
  }

  getLegSwingWeight(): number {
    return this.legSwingWeight
  }

  private rotate(
    boneName: (typeof VRMHumanBoneName)[keyof typeof VRMHumanBoneName],
    axis: THREE.Vector3,
    radians: number
  ): void {
    const node = this.vrm.humanoid?.getNormalizedBoneNode(boneName)
    if (!node || Math.abs(radians) < 0.00001) {
      return
    }

    const rotation = new THREE.Quaternion().setFromAxisAngle(axis, radians)
    node.quaternion.multiply(rotation)
    node.quaternion.normalize()
    this.appliedOffsets.push({ node, rotation })
  }
}
