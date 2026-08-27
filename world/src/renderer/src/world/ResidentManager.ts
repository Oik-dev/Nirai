import * as THREE from 'three'
import { ResidentInstance } from './ResidentInstance'

export interface ResidentViewDefinition {
  name: string
  avatar: string | null
}

export type ResidentFactory = (name: string) => ResidentInstance

// Separation distances keep overlapping presentations apart without changing
// directed move targets. Do not retune as a collision-system rewrite.
const NATURAL_SEPARATION_DISTANCE = 0.72
const DIRECTED_HARD_COLLISION_DISTANCE = 0.30
const MAX_PRESENTATION_SEPARATION = 0.36
const SEPARATION_VERTICAL_LIMIT = 0.72

export class ResidentManager {
  private readonly residents = new Map<string, ResidentInstance>()
  private readonly separationScratch = new Map<string, THREE.Vector3>()

  constructor(
    private readonly scene: THREE.Scene,
    private readonly createResident: ResidentFactory = (name) => new ResidentInstance(name)
  ) {}

  async spawn(definition: ResidentViewDefinition): Promise<void> {
    if (this.residents.has(definition.name) || definition.avatar === null) {
      return
    }

    const resident = this.createResident(definition.name)
    this.residents.set(definition.name, resident)

    try {
      await resident.loadAvatar(definition.avatar)

      if (this.residents.get(definition.name) === resident) {
        this.scene.add(resident.root)
      }
    } catch (error) {
      if (this.residents.get(definition.name) === resident) {
        this.residents.delete(definition.name)
      }
      resident.dispose()
      throw error
    }
  }

  remove(residentName: string): void {
    const resident = this.residents.get(residentName)

    if (!resident) {
      return
    }

    this.residents.delete(residentName)
    this.scene.remove(resident.root)
    resident.dispose()
  }

  async changeAvatar(residentName: string, avatarPath: string): Promise<void> {
    const resident = this.residents.get(residentName)

    if (!resident) {
      await this.spawn({ name: residentName, avatar: avatarPath })
      return
    }

    await resident.loadAvatar(avatarPath)
  }

  get(residentName: string): ResidentInstance | undefined {
    return this.residents.get(residentName)
  }

  getAll(): readonly ResidentInstance[] {
    return [...this.residents.values()]
  }

  getEntries(): readonly (readonly [string, ResidentInstance])[] {
    return [...this.residents.entries()]
  }

  get size(): number {
    return this.residents.size
  }

  update(delta: number): void {
    this.updateSeparationTargets()
    for (const resident of this.residents.values()) {
      resident.update(delta)
    }
  }

  private updateSeparationTargets(): void {
    const entries = [...this.residents.entries()]
    this.separationScratch.clear()
    for (const [name] of entries) {
      this.separationScratch.set(name, new THREE.Vector3())
    }

    for (let leftIndex = 0; leftIndex < entries.length; leftIndex += 1) {
      const [leftName, left] = entries[leftIndex]
      for (let rightIndex = leftIndex + 1; rightIndex < entries.length; rightIndex += 1) {
        const [rightName, right] = entries[rightIndex]
        if (Math.abs(left.root.position.y - right.root.position.y) > SEPARATION_VERTICAL_LIMIT) {
          continue
        }

        const dx = right.root.position.x - left.root.position.x
        const dz = right.root.position.z - left.root.position.z
        const distance = Math.hypot(dx, dz)
        const bothNatural = left.getProximityMode() === 'natural'
          && right.getProximityMode() === 'natural'
        const minimumDistance = bothNatural
          ? NATURAL_SEPARATION_DISTANCE
          : DIRECTED_HARD_COLLISION_DISTANCE
        if (distance >= minimumDistance) {
          continue
        }

        const direction = distance > 1e-5
          ? new THREE.Vector3(dx / distance, 0, dz / distance)
          : createDeterministicSeparationDirection(leftName, rightName)
        const push = Math.min(MAX_PRESENTATION_SEPARATION, (minimumDistance - distance) * 0.5)
        this.separationScratch.get(leftName)?.addScaledVector(direction, -push)
        this.separationScratch.get(rightName)?.addScaledVector(direction, push)
      }
    }

    for (const [name, resident] of entries) {
      const target = this.separationScratch.get(name) ?? new THREE.Vector3()
      if (target.length() > MAX_PRESENTATION_SEPARATION) {
        target.setLength(MAX_PRESENTATION_SEPARATION)
      }
      resident.setSeparationTarget(target)
    }
  }

  dispose(): void {
    for (const resident of this.residents.values()) {
      this.scene.remove(resident.root)
      resident.dispose()
    }
    this.residents.clear()
    this.separationScratch.clear()
  }
}

function createDeterministicSeparationDirection(leftName: string, rightName: string): THREE.Vector3 {
  const seed = `${leftName}|${rightName}`
  let hash = 2166136261
  for (let index = 0; index < seed.length; index += 1) {
    hash ^= seed.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  const angle = ((hash >>> 0) / 0xffffffff) * Math.PI * 2
  return new THREE.Vector3(Math.cos(angle), 0, Math.sin(angle))
}
