import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import { SceneRuntime } from '../../src/renderer/src/runtime/SceneRuntime'
import { createScreenSafeSwimBounds } from '../../src/renderer/src/runtime/worldConfig'
import type { ResidentInstance } from '../../src/renderer/src/world/ResidentInstance'
import { MovementController, type SwimBounds } from '../../src/renderer/src/world/MovementController'

interface ResidentHarness {
  readonly resident: ResidentInstance
  readonly root: THREE.Group
  readonly movement: MovementController
  readonly moveTo: ReturnType<typeof vi.fn>
  readonly constrainHorizontal: ReturnType<typeof vi.fn>
  readonly setPresentationBounds: ReturnType<typeof vi.fn>
}

interface SceneRuntimeInternals {
  readonly camera: THREE.PerspectiveCamera
  readonly cameraAim: THREE.Vector3
  readonly cameraTargetAim: THREE.Vector3
  readonly cameraTargetPosition: THREE.Vector3
  readonly worldCameraFarDistance: number
  initialRosterLayoutActive: boolean
  updateResidentPresentationBounds(reflowInitialLayout?: boolean): void
  updatePresentationOpticalDistanceScale(): void
  resolveWorldCameraRig(): void
}

interface MovementControllerInternals {
  readonly start: THREE.Vector3
  readonly controlA: THREE.Vector3
  readonly controlB: THREE.Vector3
  readonly target: THREE.Vector3 | null
}

function createResident(name: string, position: THREE.Vector3): ResidentHarness {
  const root = new THREE.Group()
  root.position.copy(position)
  const movement = new MovementController(root, 1.2, 0, () => 0.5)
  const moveTo = vi.fn((
    _target: THREE.Vector3,
    _onArrive?: () => void,
    _bounds?: SwimBounds,
    _preserveDepth?: boolean
  ) => true)
  const constrainHorizontal = vi.fn((bounds: SwimBounds) => movement.constrainHorizontal(bounds))
  const setPresentationBounds = vi.fn((_bounds: SwimBounds) => undefined)
  const resident = {
    name,
    root,
    moveTo,
    constrainHorizontal,
    setPresentationBounds,
    resetSeparationOffset: vi.fn(),
    getCameraFramingBounds: vi.fn((target = new THREE.Box3()) => target.setFromCenterAndSize(
      new THREE.Vector3(root.position.x, root.position.y + 0.8, root.position.z),
      new THREE.Vector3(0.6, 1.6, 0.5)
    ))
  } as unknown as ResidentInstance

  return { resident, root, movement, moveTo, constrainHorizontal, setPresentationBounds }
}

function createRuntimeHarness(
  positions: readonly THREE.Vector3[],
  initialRosterLayoutActive = true
): {
  readonly runtime: SceneRuntime
  readonly residents: readonly ResidentHarness[]
  readonly environment: {
    readonly resize: ReturnType<typeof vi.fn>
    readonly setPresentationOpticalDistanceScale: ReturnType<typeof vi.fn>
  }
  readonly postProcessing: {
    readonly setSize: ReturnType<typeof vi.fn>
    readonly setOpticalDistanceScale: ReturnType<typeof vi.fn>
  }
} {
  const residents = positions.map((position, index) => createResident(
    `Resident ${index + 1}`,
    position
  ))
  const entries = residents.map(({ resident }) => [resident.name, resident] as const)
  const byName = new Map(entries)
  const worldCameraAim = new THREE.Vector3(0, 1.184, -0.72)
  const worldCameraFarPosition = new THREE.Vector3(0, 1.632, 5.36)
  const worldCameraBoomDirection = worldCameraFarPosition.clone().sub(worldCameraAim).normalize()
  const camera = new THREE.PerspectiveCamera(56, 16 / 9, 0.1, 100)
  camera.position.set(0, 1.5, 1.1)
  const environment = {
    resize: vi.fn(),
    setPresentationOpticalDistanceScale: vi.fn()
  }
  const postProcessing = {
    setSize: vi.fn(),
    setOpticalDistanceScale: vi.fn()
  }

  const runtime = Object.create(SceneRuntime.prototype) as SceneRuntime
  Object.assign(runtime, {
    camera,
    renderer: { setSize: vi.fn() },
    environment,
    postProcessing,
    residents: {
      getEntries: () => entries,
      get: (name: string) => byName.get(name),
      get size() {
        return entries.length
      },
      setNaturalSeparationDistance: vi.fn()
    },
    residentHeight: 1.6,
    residentRosterOrder: entries.map(([name]) => name),
    focusedResidentName: null,
    focusChangeListener: null,
    focusZoom: 0,
    worldZoom: 0,
    cameraRigReady: true,
    worldCameraAim,
    worldCameraFarPosition,
    worldCameraBoomDirection,
    cameraAim: worldCameraAim.clone(),
    worldCameraFarDistance: worldCameraFarPosition.distanceTo(worldCameraAim),
    worldCameraNearDistance: worldCameraFarPosition.distanceTo(worldCameraAim) * 0.52,
    groupBounds: new THREE.Box3(),
    groupResidentBounds: new THREE.Box3(),
    groupBoundsCenter: new THREE.Vector3(),
    groupBoundsSize: new THREE.Vector3(),
    cameraTargetAim: new THREE.Vector3(),
    cameraTargetPosition: new THREE.Vector3(),
    initialRosterLayoutActive,
    initialRosterLayoutKey: `${entries.length}:${entries.map(([name]) => name).join('\u0000')}`,
    initialRosterCenterZ: null,
    primaryResidentName: entries[0]?.[0] ?? 'Resident 1',
    previousResidentPosition: new THREE.Vector3(),
    hasPreviousResidentPosition: false
  })

  return { runtime, residents, environment, postProcessing }
}

function snapshotPositions(residents: readonly ResidentHarness[]): readonly number[][] {
  return residents.map(({ root }) => root.position.toArray())
}

function snapshotMovement(movement: MovementController): readonly (readonly number[] | null)[] {
  const internals = movement as unknown as MovementControllerInternals
  return [
    internals.start.toArray(),
    internals.controlA.toArray(),
    internals.controlB.toArray(),
    internals.target?.toArray() ?? null
  ]
}

const LANDSCAPE_POSITIONS = [
  new THREE.Vector3(-2.4, 0.32, -0.55),
  new THREE.Vector3(-0.15, 0.32, -0.42),
  new THREE.Vector3(2.2, 0.32, -0.62)
] as const

describe('SceneRuntime resize presentation', () => {
  it('keeps every Resident root fixed through a focused portrait resize', () => {
    const { runtime, residents } = createRuntimeHarness(LANDSCAPE_POSITIONS)
    const before = snapshotPositions(residents)

    expect(runtime.focusResident('Resident 2')).toBe(true)
    runtime.resize(540, 960)

    expect(snapshotPositions(residents)).toEqual(before)
    for (const resident of residents) {
      expect(resident.setPresentationBounds).toHaveBeenCalledOnce()
      expect(resident.constrainHorizontal).not.toHaveBeenCalled()
    }
  })

  it('keeps the pre-resize layout after Focus is released', () => {
    const { runtime, residents } = createRuntimeHarness(LANDSCAPE_POSITIONS)
    const before = snapshotPositions(residents)

    runtime.focusResident('Resident 2')
    runtime.resize(540, 960)
    runtime.focusResident(null)

    expect(snapshotPositions(residents)).toEqual(before)
  })

  it('keeps explicit Focus moves inside the current Focus camera bounds', () => {
    const { runtime, residents } = createRuntimeHarness(LANDSCAPE_POSITIONS, false)
    const focused = residents[1]
    const internals = runtime as unknown as SceneRuntimeInternals

    expect(runtime.focusResident('Resident 2')).toBe(true)
    runtime.resize(540, 960)
    const expectedBounds = createScreenSafeSwimBounds(internals.camera, 1.6)
    const random = vi.spyOn(Math, 'random').mockReturnValue(1)
    try {
      expect(runtime.moveResidentTo('b')).toBe(true)
    } finally {
      random.mockRestore()
    }

    expect(focused.moveTo).toHaveBeenCalledOnce()
    const [target, , bounds] = focused.moveTo.mock.lastCall as [
      THREE.Vector3,
      (() => void) | undefined,
      SwimBounds,
      boolean
    ]
    expect(bounds.min.x).toBeCloseTo(expectedBounds.min.x, 8)
    expect(bounds.max.x).toBeCloseTo(expectedBounds.max.x, 8)
    expect(target.x).toBeGreaterThanOrEqual(expectedBounds.min.x)
    expect(target.x).toBeLessThanOrEqual(expectedBounds.max.x)
  })

  it('does not rewrite an active movement curve during resize', () => {
    const { runtime, residents } = createRuntimeHarness(LANDSCAPE_POSITIONS, false)
    const movingResident = residents[0]
    movingResident.movement.moveTo(
      new THREE.Vector3(2.8, 0.32, -0.2),
      undefined,
      undefined,
      'seabed'
    )
    const beforeRoot = movingResident.root.position.toArray()
    const beforeMovement = snapshotMovement(movingResident.movement)

    runtime.focusResident('Resident 2')
    runtime.resize(540, 960)

    expect(movingResident.root.position.toArray()).toEqual(beforeRoot)
    expect(snapshotMovement(movingResident.movement)).toEqual(beforeMovement)
  })

  it('backs the World camera out in portrait to fit the unchanged group', () => {
    const { runtime, residents, environment, postProcessing } = createRuntimeHarness(
      LANDSCAPE_POSITIONS,
      false
    )
    const before = snapshotPositions(residents)
    const internals = runtime as unknown as SceneRuntimeInternals

    internals.resolveWorldCameraRig()
    internals.camera.position.copy(internals.cameraTargetPosition)
    internals.cameraAim.copy(internals.cameraTargetAim)
    internals.updatePresentationOpticalDistanceScale()
    expect(environment.setPresentationOpticalDistanceScale).toHaveBeenLastCalledWith(1)

    runtime.resize(540, 960)
    internals.resolveWorldCameraRig()
    internals.camera.position.copy(internals.cameraTargetPosition)
    internals.cameraAim.copy(internals.cameraTargetAim)
    internals.updatePresentationOpticalDistanceScale()

    expect(snapshotPositions(residents)).toEqual(before)
    const portraitDistance = internals.cameraTargetPosition.distanceTo(internals.cameraTargetAim)
    expect(portraitDistance).toBeGreaterThan(internals.worldCameraFarDistance)
    const opticalDistanceScale = environment.setPresentationOpticalDistanceScale.mock.lastCall?.[0]
    expect(opticalDistanceScale).toBeLessThan(1)
    expect(portraitDistance * opticalDistanceScale).toBeCloseTo(
      internals.worldCameraFarDistance,
      8
    )
    expect(postProcessing.setOpticalDistanceScale).toHaveBeenLastCalledWith(opticalDistanceScale)
  })

  it('keeps every Resident root fixed when an existing Avatar is replaced', async () => {
    const { runtime, residents } = createRuntimeHarness(LANDSCAPE_POSITIONS, true)
    const before = snapshotPositions(residents)
    const target = residents[1]
    const runtimeInternals = runtime as unknown as SceneRuntimeInternals & {
      residentHeights: Map<string, number>
      residentMotionTunings: Map<string, unknown>
      speechAnalysers: Map<string, AnalyserNode>
      lastResidentRosterSize: number
      configureCameraRigs: ReturnType<typeof vi.fn>
    }
    const manager = runtime.residents as unknown as {
      changeAvatar: ReturnType<typeof vi.fn>
      spawn: ReturnType<typeof vi.fn>
    }

    Object.assign(target.resident, {
      vrm: { scene: new THREE.Group() },
      setMotionTuning: vi.fn(),
      face: vi.fn(),
      setLipSyncAnalyser: vi.fn(),
      update: vi.fn()
    })
    manager.changeAvatar = vi.fn(async () => undefined)
    manager.spawn = vi.fn(async () => undefined)
    runtimeInternals.residentHeights = new Map(
      residents.map(({ resident }) => [resident.name, 1.6])
    )
    runtimeInternals.residentMotionTunings = new Map()
    runtimeInternals.speechAnalysers = new Map()
    runtimeInternals.lastResidentRosterSize = residents.length
    runtimeInternals.configureCameraRigs = vi.fn()

    await runtime.loadResidentAvatar(target.resident.name, 'replacement.vrm')

    expect(manager.changeAvatar).toHaveBeenCalledOnce()
    expect(manager.spawn).not.toHaveBeenCalled()
    expect(snapshotPositions(residents)).toEqual(before)
    for (const resident of residents) {
      expect(resident.constrainHorizontal).not.toHaveBeenCalled()
      expect(resident.setPresentationBounds).toHaveBeenCalled()
    }
  })

  it.each([2, 3, 5])('retains the %i-Resident initial placement path outside resize', (count) => {
    const positions = Array.from(
      { length: count },
      () => new THREE.Vector3(0, 0.32, -0.5)
    )
    const { runtime, residents } = createRuntimeHarness(positions)
    const internals = runtime as unknown as SceneRuntimeInternals

    internals.updateResidentPresentationBounds(true)

    expect(new Set(residents.map(({ root }) => root.position.x.toFixed(6))).size).toBe(count)
  })
})
