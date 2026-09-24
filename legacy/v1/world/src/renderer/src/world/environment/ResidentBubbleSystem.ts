import * as THREE from 'three'
import type { EnvironmentResidentFrame } from './EnvironmentController'

const BUBBLE_VERTEX_SHADER = /* glsl */ `
  attribute float size;
  attribute float life;
  attribute float phase;
  varying float vLife;
  varying float vPhase;

  void main() {
    vLife = life;
    vPhase = phase;
    vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = size * clamp(3.3 / max(-viewPosition.z, 0.65), 0.64, 2.9);
    gl_Position = projectionMatrix * viewPosition;
  }
`

const BUBBLE_FRAGMENT_SHADER = /* glsl */ `
  varying float vLife;
  varying float vPhase;

  void main() {
    vec2 bubble = gl_PointCoord - 0.5;
    float shapeMotion = sin(vPhase * 2.3 + vLife * 8.0);
    float aspect = 1.0 + 0.09 * shapeMotion;
    bubble.x *= aspect;
    bubble.y /= aspect;

    float angle = atan(bubble.y, bubble.x);
    float edgeRadius = 0.40
      + 0.018 * sin(angle * 2.0 + vPhase)
      + 0.009 * sin(angle * 3.0 - vLife * 6.0 + vPhase * 0.7);
    float radius = length(bubble);
    float distanceToFilm = edgeRadius - radius;
    if (distanceToFilm < 0.0) discard;

    float litSide = mix(0.65, 1.0, 0.5 + 0.5 * sin(angle + 2.25));
    float rim = (1.0 - smoothstep(0.0, 0.09, distanceToFilm)) * litSide;
    vec2 highlightUv = (bubble - vec2(-0.15, 0.145)) * vec2(0.92, 1.5);
    float highlightDistance = length(highlightUv);
    float crescent = smoothstep(0.055, 0.105, highlightDistance)
      * (1.0 - smoothstep(0.13, 0.19, highlightDistance));
    float film = (1.0 - smoothstep(0.0, edgeRadius, radius)) * 0.08;
    float fade = smoothstep(0.0, 0.18, vLife) * smoothstep(0.0, 0.12, 1.0 - vLife);
    float alpha = (rim * 0.80 + crescent * 0.64 + film) * fade;
    vec3 color = mix(vec3(0.46, 0.77, 0.84), vec3(0.88, 0.96, 0.99), crescent);
    gl_FragColor = vec4(color, alpha * 0.94);
  }
`

export interface BubbleDiagnostics {
  readonly activeCount: number
  readonly emittedTotal: number
}

export class ResidentBubbleSystem {
  readonly points: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>

  private readonly positions: Float32Array
  private readonly sizes: Float32Array
  private readonly lifeFractions: Float32Array
  private readonly phases: Float32Array
  private readonly remainingLife: Float32Array
  private readonly totalLife: Float32Array
  private readonly riseSpeeds: Float32Array
  private readonly lateralSpeeds: Float32Array
  private readonly previousResidentPosition = new THREE.Vector3()
  private readonly movementDirection = new THREE.Vector3(0, 0, 1)
  private readonly emissionOrigin = new THREE.Vector3()
  private readonly random: () => number
  private elapsedTime = 0
  private idleTimer = 0
  private movementEmission = 0
  private cursor = 0
  private hasPreviousResidentPosition = false
  private activeCount = 0
  private emittedTotal = 0

  constructor(private readonly capacity: number, seed = 0x42554242) {
    this.random = createRandom(seed)
    this.positions = new Float32Array(capacity * 3)
    this.sizes = new Float32Array(capacity)
    this.lifeFractions = new Float32Array(capacity)
    this.phases = new Float32Array(capacity)
    this.remainingLife = new Float32Array(capacity)
    this.totalLife = new Float32Array(capacity)
    this.riseSpeeds = new Float32Array(capacity)
    this.lateralSpeeds = new Float32Array(capacity)

    for (let index = 0; index < capacity; index += 1) {
      this.positions[index * 3 + 1] = -1000
      this.phases[index] = this.random() * Math.PI * 2
    }

    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(this.positions, 3))
    geometry.setAttribute('size', new THREE.BufferAttribute(this.sizes, 1))
    geometry.setAttribute('life', new THREE.BufferAttribute(this.lifeFractions, 1))
    geometry.setAttribute('phase', new THREE.BufferAttribute(this.phases, 1))

    const material = new THREE.ShaderMaterial({
      vertexShader: BUBBLE_VERTEX_SHADER,
      fragmentShader: BUBBLE_FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      blending: THREE.NormalBlending
    })

    this.points = new THREE.Points(geometry, material)
    this.points.name = 'Environment:bubbles'
    this.points.frustumCulled = false
  }

  update(delta: number, resident?: EnvironmentResidentFrame): void {
    const safeDelta = Math.max(0, Math.min(delta, 0.1))
    this.elapsedTime += safeDelta
    this.advanceActiveBubbles(safeDelta)

    if (!resident) {
      this.idleTimer = 0
      this.movementEmission = 0
      this.hasPreviousResidentPosition = false
      return
    }

    if (this.hasPreviousResidentPosition) {
      this.movementDirection.subVectors(resident.position, this.previousResidentPosition)
      if (this.movementDirection.lengthSq() > 0.000001) {
        this.movementDirection.normalize()
      }
    }

    const moving = resident.speed > 0.05
    if (moving) {
      this.idleTimer = 0
      this.movementEmission += safeDelta * (4.6 + resident.speed * 8.4)
      const emitCount = Math.floor(this.movementEmission)
      this.movementEmission -= emitCount
      for (let index = 0; index < emitCount; index += 1) {
        this.emissionOrigin.copy(resident.position)
          .addScaledVector(this.movementDirection, -0.18 - this.random() * 0.12)
        this.emissionOrigin.x += (this.random() - 0.5) * 0.22
        this.emissionOrigin.y += 0.16 + this.random() * Math.max(0.28, resident.height * 0.28)
        this.emissionOrigin.z += (this.random() - 0.5) * 0.18
        this.emit(this.emissionOrigin, false)
      }
    } else {
      this.movementEmission = 0
      this.idleTimer += safeDelta
      if (this.idleTimer >= 1.65) {
        this.idleTimer -= 1.65
        const breathCount = this.random() > 0.62 ? 2 : 1
        for (let index = 0; index < breathCount; index += 1) {
          this.emissionOrigin.copy(resident.position)
          this.emissionOrigin.x += (this.random() - 0.5) * 0.11
          this.emissionOrigin.y += resident.height * (0.78 + this.random() * 0.08)
          this.emissionOrigin.z += 0.03 + this.random() * 0.05
          this.emit(this.emissionOrigin, true)
        }
      }
    }

    this.previousResidentPosition.copy(resident.position)
    this.hasPreviousResidentPosition = true
  }

  getDiagnostics(): BubbleDiagnostics {
    return { activeCount: this.activeCount, emittedTotal: this.emittedTotal }
  }

  private advanceActiveBubbles(delta: number): void {
    let changed = false
    for (let index = 0; index < this.capacity; index += 1) {
      if (this.remainingLife[index] <= 0) continue

      this.remainingLife[index] = Math.max(0, this.remainingLife[index] - delta)
      if (this.remainingLife[index] === 0) {
        this.positions[index * 3 + 1] = -1000
        this.lifeFractions[index] = 0
        this.activeCount -= 1
      } else {
        const phase = this.phases[index]
        this.positions[index * 3] += Math.sin(this.elapsedTime * 1.2 + phase)
          * this.lateralSpeeds[index] * delta
        this.positions[index * 3 + 1] += this.riseSpeeds[index] * delta
        this.positions[index * 3 + 2] += Math.cos(this.elapsedTime * 0.9 + phase)
          * this.lateralSpeeds[index] * delta
        this.lifeFractions[index] = this.remainingLife[index] / this.totalLife[index]
      }
      changed = true
    }

    if (changed) this.markAttributesForUpdate()
  }

  private emit(origin: THREE.Vector3, idleBreath: boolean): void {
    const index = this.findAvailableIndex()
    if (index < 0) return

    const lifetime = (idleBreath ? 3.0 : 2.2) + this.random() * 1.7
    this.positions[index * 3] = origin.x
    this.positions[index * 3 + 1] = origin.y
    this.positions[index * 3 + 2] = origin.z
    this.sizes[index] = (idleBreath ? 6.2 : 4.0) + this.random() * (idleBreath ? 4.4 : 3.8)
    this.remainingLife[index] = lifetime
    this.totalLife[index] = lifetime
    this.lifeFractions[index] = 0.999
    this.riseSpeeds[index] = 0.20 + this.random() * 0.27
    this.lateralSpeeds[index] = 0.015 + this.random() * 0.035
    this.phases[index] = this.random() * Math.PI * 2
    this.activeCount += 1
    this.emittedTotal += 1
    this.markAttributesForUpdate()
  }

  private findAvailableIndex(): number {
    for (let offset = 0; offset < this.capacity; offset += 1) {
      const index = (this.cursor + offset) % this.capacity
      if (this.remainingLife[index] <= 0) {
        this.cursor = (index + 1) % this.capacity
        return index
      }
    }
    return -1
  }

  private markAttributesForUpdate(): void {
    ;(this.points.geometry.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true
    ;(this.points.geometry.getAttribute('size') as THREE.BufferAttribute).needsUpdate = true
    ;(this.points.geometry.getAttribute('life') as THREE.BufferAttribute).needsUpdate = true
    ;(this.points.geometry.getAttribute('phase') as THREE.BufferAttribute).needsUpdate = true
  }
}

function createRandom(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0
    return state / 0x100000000
  }
}
