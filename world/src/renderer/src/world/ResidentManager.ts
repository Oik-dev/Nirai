import * as THREE from 'three'
import { ResidentInstance } from './ResidentInstance'

export interface ResidentViewDefinition {
  name: string
  avatar: string | null
}

export type ResidentFactory = (name: string) => ResidentInstance

export class ResidentManager {
  private readonly residents = new Map<string, ResidentInstance>()

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

  update(delta: number): void {
    for (const resident of this.residents.values()) {
      resident.update(delta)
    }
  }

  dispose(): void {
    for (const resident of this.residents.values()) {
      this.scene.remove(resident.root)
      resident.dispose()
    }
    this.residents.clear()
  }
}
