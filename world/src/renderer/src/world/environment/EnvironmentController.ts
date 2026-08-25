import * as THREE from 'three'
import { ResidentBubbleSystem, type BubbleDiagnostics } from './ResidentBubbleSystem'
import { createAmbientBubbleField } from './AmbientBubbleField'
import {
  OPTICAL_BACKGROUND_FRAGMENT_SHADER,
  OPTICAL_BACKGROUND_VERTEX_SHADER,
  OPTICAL_CAUSTICS_FRAGMENT_SHADER,
  OPTICAL_CAUSTICS_VERTEX_SHADER,
  OPTICAL_WATER_FRAGMENT_SHADER,
  OPTICAL_WATER_VERTEX_SHADER
} from './OpticalEnvironmentShaders'
import {
  createUnderwaterOpticsState,
  type UnderwaterOpticsState
} from './UnderwaterOptics'
import { createWaterThreeSurface, type WaterThreeSurface } from './WaterThreeSurface'
import {
  DEFAULT_VISUAL_TUNING,
  type VisualTuning
} from '../../runtime/VisualTuning'

export type EnvironmentEffectName =
  | 'seabed'
  | 'fog'
  | 'lighting'
  | 'overheadGlow'
  | 'waterSurface'
  | 'caustics'
  | 'suspendedParticles'
  | 'luminousParticles'
  | 'bubbles'
  | 'lightShafts'

export type EnvironmentQuality = 'low' | 'medium' | 'high'

export interface EnvironmentOptions {
  readonly quality?: EnvironmentQuality
  readonly effects?: Partial<Record<EnvironmentEffectName, boolean>>
}

export interface TextureLoaderPort {
  load(url: string): THREE.Texture
}

export interface EnvironmentDependencies {
  readonly assetBaseUrl?: string
  readonly textureLoader?: TextureLoaderPort
}

export interface EnvironmentResidentFrame {
  readonly position: THREE.Vector3
  readonly height: number
  readonly speed: number
  readonly animation: string | null
}

export interface EnvironmentFrameContext {
  readonly resident?: EnvironmentResidentFrame
}

const DEFAULT_EFFECTS: Readonly<Record<EnvironmentEffectName, boolean>> = {
  seabed: true,
  fog: true,
  lighting: true,
  overheadGlow: true,
  waterSurface: false,
  caustics: true,
  suspendedParticles: true,
  luminousParticles: true,
  bubbles: true,
  lightShafts: true
}

const QUALITY_COUNTS = {
  low: {
    bubbles: 72,
    ambientBubbles: 48,
    ambientStreams: 2,
    particles: 420,
    luminousParticles: 48
  },
  medium: {
    bubbles: 180,
    ambientBubbles: 72,
    ambientStreams: 3,
    particles: 980,
    luminousParticles: 132
  },
  high: {
    bubbles: 360,
    ambientBubbles: 84,
    ambientStreams: 3,
    particles: 1800,
    luminousParticles: 280
  }
} as const

const BACKGROUND_VERTEX_SHADER = /* glsl */ `
  varying vec3 vViewPosition;

  void main() {
    vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
    vViewPosition = viewPosition.xyz;
    gl_Position = projectionMatrix * viewPosition;
  }
`
const BACKGROUND_FRAGMENT_SHADER = /* glsl */ `
  uniform float time;
  uniform float waterSurfaceStrength;
  uniform float lightShaftStrength;
  varying vec3 vViewPosition;

  float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
  }

  vec2 hash22(vec2 p) {
    p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
    return fract(sin(p) * 43758.5453);
  }

  float surfaceCellEdge(vec2 p, float phase) {
    vec2 cell = floor(p);
    vec2 local = fract(p);
    float nearest = 10.0;
    float secondNearest = 10.0;
    for (int y = -1; y <= 1; y++) {
      for (int x = -1; x <= 1; x++) {
        vec2 offset = vec2(float(x), float(y));
        vec2 point = hash22(cell + offset);
        point = 0.5 + 0.37 * sin(phase + 6.28318 * point);
        float distanceToPoint = length(offset + point - local);
        if (distanceToPoint < nearest) {
          secondNearest = nearest;
          nearest = distanceToPoint;
        } else if (distanceToPoint < secondNearest) {
          secondNearest = distanceToPoint;
        }
      }
    }
    return 1.0 - smoothstep(0.018, 0.105, secondNearest - nearest);
  }

  float softBeam(float x, float height, float origin, float drift, float baseWidth, float phase) {
    float descent = clamp((0.88 - height) / 0.68, 0.0, 1.0);
    float center = origin + drift * descent;
    float width = baseWidth + descent * 0.052;
    float lateral = 1.0 - smoothstep(width * 0.12, width, abs(x - center));
    float vertical = smoothstep(0.18, 0.34, height) * (1.0 - smoothstep(0.84, 0.94, height));
    float shimmer = 0.86 + 0.14 * sin(time * 0.16 + phase + descent * 8.0);
    return lateral * vertical * shimmer;
  }

  void main() {
    vec3 direction = normalize(vViewPosition);
    float height = direction.y * 0.5 + 0.5;

    vec3 abyss = vec3(0.001, 0.022, 0.11);
    vec3 deepBlue = vec3(0.001, 0.105, 0.34);
    vec3 cyanWater = vec3(0.010, 0.34, 0.62);
    vec3 color = mix(abyss, deepBlue, smoothstep(0.08, 0.58, height));
    color = mix(color, cyanWater, smoothstep(0.66, 0.94, height));

    float centerGlow = exp(-direction.x * direction.x * 3.2)
      * smoothstep(0.42, 0.96, height);
    color += vec3(0.08, 0.27, 0.42) * centerGlow;

    float shafts = 0.0;
    shafts += softBeam(direction.x, height, -0.48, -0.12, 0.016, 0.4);
    shafts += softBeam(direction.x, height, -0.31, -0.08, 0.022, 1.4);
    shafts += softBeam(direction.x, height, -0.14, -0.04, 0.018, 2.5);
    shafts += softBeam(direction.x, height,  0.02,  0.02, 0.026, 3.2);
    shafts += softBeam(direction.x, height,  0.19,  0.06, 0.020, 3.8);
    shafts += softBeam(direction.x, height,  0.38,  0.11, 0.017, 5.1);
    shafts = min(shafts, 1.0) * lightShaftStrength * 0.34;
    float shaftNoise = 0.86 + 0.14 * sin(direction.x * 43.0 + height * 31.0 + time * 0.10);
    color += vec3(0.11, 0.30, 0.45) * shafts * shaftNoise;

    float surfaceMask = smoothstep(0.67, 0.91, height) * waterSurfaceStrength;
    vec2 surfacePoint = direction.xz / max(direction.y, 0.16);
    vec2 flowA = surfacePoint * 4.8 + vec2(time * 0.055, -time * 0.038);
    vec2 flowB = mat2(0.78, -0.63, 0.63, 0.78) * surfacePoint * 6.4
      + vec2(-time * 0.032, time * 0.047);
    float cellA = surfaceCellEdge(flowA, time * 0.35);
    float cellB = surfaceCellEdge(flowB, -time * 0.27);
    float glint = pow(clamp(cellA * 0.62 + cellB * 0.48 - 0.18, 0.0, 1.0), 2.15);
    float broadSun = exp(-direction.x * direction.x * 8.0)
      * smoothstep(0.72, 0.96, height);
    vec3 surfaceColor = mix(vec3(0.10, 0.48, 0.76), vec3(0.50, 0.86, 1.0), surfaceMask);
    color = mix(color, surfaceColor, surfaceMask * 0.34);
    color += vec3(1.35, 1.55, 1.62) * glint * surfaceMask * (0.32 + broadSun * 0.92);
    color += vec3(1.08, 1.32, 1.42) * broadSun * surfaceMask * 1.05;

    float overhead = pow(smoothstep(0.02, 0.34, direction.y), 2.0);
    color += vec3(0.055, 0.13, 0.20) * overhead;

    float grain = hash21(floor(direction.xy * 190.0 + time * 0.015));
    color += (grain - 0.5) * 0.004;
    gl_FragColor = vec4(color, 1.0);
  }
`
const WATER_SURFACE_VERTEX_SHADER = /* glsl */ `
  uniform float time;
  varying vec2 vUv;
  varying vec3 vWorldPosition;
  varying float vWave;

  float waveHeight(vec2 point) {
    float broad = sin(point.x * 0.48 + time * 0.34) * 0.10
      + cos(point.y * 0.61 - time * 0.29) * 0.08;
    float crossing = sin((point.x + point.y) * 1.23 + time * 0.47) * 0.045
      + sin((point.x - point.y) * 1.76 - time * 0.38) * 0.032;
    float fine = sin(point.x * 3.8 + sin(point.y * 1.7 + time * 0.8)) * 0.014;
    return broad + crossing + fine;
  }

  void main() {
    vUv = uv;
    vec3 transformed = position;
    vWave = waveHeight(position.xy);
    transformed.z += vWave;
    vec4 worldPosition = modelMatrix * vec4(transformed, 1.0);
    vWorldPosition = worldPosition.xyz;
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`
const WATER_SURFACE_FRAGMENT_SHADER = /* glsl */ `
  uniform float time;
  uniform vec3 cameraPosition;
  varying vec2 vUv;
  varying vec3 vWorldPosition;
  varying float vWave;

  float rippleNetwork(vec2 p) {
    float a = sin(p.x * 128.0 + sin(p.y * 53.0 + time * 0.7));
    float b = sin(p.y * 147.0 - time * 0.9 + sin(p.x * 61.0));
    float c = sin((p.x + p.y) * 106.0 + time * 0.53);
    float ridges = abs(a + b + c) / 3.0;
    return pow(smoothstep(0.45, 0.94, ridges), 2.1);
  }

  void main() {
    vec2 centered = vUv - 0.5;
    float network = rippleNetwork(vUv + vec2(time * 0.003, -time * 0.002));
    vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
    vec3 approximateNormal = normalize(vec3(
      sin(vUv.x * 37.0 + time * 0.42) * 0.16,
      1.0,
      cos(vUv.y * 41.0 - time * 0.36) * 0.16
    ));
    float fresnel = pow(1.0 - abs(dot(viewDirection, approximateNormal)), 2.2);
    float sun = exp(-dot(centered * vec2(2.2, 1.25), centered * vec2(2.2, 1.25)) * 4.2);
    float shimmer = 0.72 + 0.28 * sin(time * 0.42 + vWave * 24.0);
    vec3 water = mix(vec3(0.008, 0.16, 0.39), vec3(0.08, 0.48, 0.82), fresnel);
    vec3 whiteGlint = vec3(1.85, 2.02, 2.10) * network * (0.58 + sun * 1.72) * shimmer;
    vec3 color = water * (0.42 + fresnel * 0.34) + whiteGlint;
    float edgeFade = 1.0 - smoothstep(0.22, 0.78, length(centered));
    float alpha = (0.025 + network * 0.68 + sun * 0.06) * mix(0.58, 1.0, edgeFade);
    gl_FragColor = vec4(color, alpha);
  }
`
const CAUSTICS_VERTEX_SHADER = /* glsl */ `
  varying vec2 vWorldXZ;
  varying float vViewDepth;

  void main() {
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    vWorldXZ = worldPosition.xz;
    vec4 viewPosition = viewMatrix * worldPosition;
    vViewDepth = -viewPosition.z;
    gl_Position = projectionMatrix * viewPosition;
  }
`
const CAUSTICS_FRAGMENT_SHADER = /* glsl */ `
  uniform float time;
  uniform vec3 fogColor;
  uniform float fogDensity;
  varying vec2 vWorldXZ;
  varying float vViewDepth;

  vec2 hash22(vec2 p) {
    p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
    return fract(sin(p) * 43758.5453);
  }

  float cellularEdge(vec2 p, float phase) {
    vec2 cell = floor(p);
    vec2 local = fract(p);
    float nearest = 10.0;
    float secondNearest = 10.0;

    for (int y = -1; y <= 1; y++) {
      for (int x = -1; x <= 1; x++) {
        vec2 offset = vec2(float(x), float(y));
        vec2 point = hash22(cell + offset);
        point = 0.5 + 0.40 * sin(phase + 6.28318 * point);
        float distanceToPoint = length(offset + point - local);
        if (distanceToPoint < nearest) {
          secondNearest = nearest;
          nearest = distanceToPoint;
        } else if (distanceToPoint < secondNearest) {
          secondNearest = distanceToPoint;
        }
      }
    }

    float edgeDistance = secondNearest - nearest;
    return 1.0 - smoothstep(0.012, 0.072, edgeDistance);
  }

  void main() {
    vec2 centered = vWorldXZ * 0.84;
    vec2 flowA = centered * 0.96 + vec2(time * 0.040, time * -0.026);
    vec2 flowB = mat2(0.82, -0.57, 0.57, 0.82) * centered * 0.68
      + vec2(time * -0.021, time * 0.034);
    float edgeA = cellularEdge(flowA, time * 0.31);
    float edgeB = cellularEdge(flowB, time * -0.24);
    float network = pow(clamp(edgeA * 0.60 + edgeB * 0.40 - 0.08, 0.0, 1.0), 2.55);
    float variation = 0.54 + 0.46 * sin(centered.x * 0.46 + centered.y * 0.34 + time * 0.18);
    float patchField = 0.52
      + 0.24 * sin(centered.x * 0.22 + centered.y * 0.17 + time * 0.05)
      + 0.24 * cos(centered.x * -0.15 + centered.y * 0.27 - time * 0.04);
    float patchMask = smoothstep(0.38, 0.72, patchField);
    float fogFactor = 1.0 - exp(-fogDensity * fogDensity * vViewDepth * vViewDepth);
    float radialFade = 1.0 - smoothstep(24.0, 54.0, length(vWorldXZ));
    float alpha = network * variation * patchMask * radialFade
      * mix(1.46, 0.015, fogFactor);
    vec3 color = mix(vec3(0.94, 1.04, 1.06), fogColor, fogFactor * 0.42);
    gl_FragColor = vec4(color, alpha);
  }
`
const PARTICLE_VERTEX_SHADER = /* glsl */ `
  uniform float time;
  uniform vec3 stageCenter;
  attribute float size;
  attribute float phase;
  attribute float drift;
  attribute float speed;
  attribute float layer;
  attribute float lightAffinity;
  varying float vPulse;
  varying float vLight;

  void main() {
    vec3 animated = position;
    float primaryMotion = time * speed + phase;
    float secondaryMotion = time * speed * 0.47 + phase * 2.13;
    animated.x += (
      sin(primaryMotion) + sin(secondaryMotion) * 0.34
    ) * drift;
    animated.y += (
      sin(time * speed * 0.63 + phase * 1.7) * 0.24
      + sin(time * speed * 0.19 + phase * 0.71) * 0.10
    ) * drift;
    animated.z += (
      cos(time * speed * 0.81 + phase) * 0.62
      + sin(secondaryMotion * 0.83 + 1.4) * 0.18
    ) * drift;
    vec4 worldPosition = modelMatrix * vec4(animated, 1.0);
    vec4 viewPosition = viewMatrix * worldPosition;
    gl_PointSize = size * clamp(2.8 / max(-viewPosition.z, 0.7), 0.42, 2.3);
    gl_Position = projectionMatrix * viewPosition;
    vPulse = 0.78 + 0.22 * sin(time * speed * 1.4 + phase);
    float stage = 1.0 - smoothstep(2.0, 11.0, length((worldPosition.xz - stageCenter.xz) * vec2(0.7, 0.48)));
    vLight = (0.48 + stage * 0.52) * mix(0.72, 1.35, lightAffinity) * mix(1.0, 0.62, layer);
  }
`
const PARTICLE_FRAGMENT_SHADER = /* glsl */ `
  uniform vec3 particleColor;
  uniform float particleOpacity;
  uniform float glowStrength;
  varying float vPulse;
  varying float vLight;

  void main() {
    float distanceFromCenter = length(gl_PointCoord - vec2(0.5));
    if (distanceFromCenter > 0.5) discard;
    float softDisc = 1.0 - smoothstep(0.08, 0.5, distanceFromCenter);
    float core = 1.0 - smoothstep(0.0, 0.18, distanceFromCenter);
    float alpha = (softDisc + core * glowStrength) * particleOpacity * vPulse * vLight;
    gl_FragColor = vec4(particleColor, alpha);
  }
`

interface SandTextures {
  readonly color: THREE.Texture
  readonly normal: THREE.Texture
  readonly backdrop: THREE.Texture
  readonly caustics: THREE.Texture
  readonly all: readonly THREE.Texture[]
}

export class EnvironmentController {
  readonly group = new THREE.Group()
  readonly optics: UnderwaterOpticsState = createUnderwaterOpticsState()
  elapsedTime = 0
  private waterElapsedTime = 0
  private causticsElapsedTime = 0
  private bubbleElapsedTime = 0
  private visualTuning: VisualTuning = DEFAULT_VISUAL_TUNING

  private readonly effects: Record<EnvironmentEffectName, boolean>
  private readonly fog = new THREE.FogExp2(0x075184, 0.014)
  private readonly originalFog: THREE.Fog | THREE.FogExp2 | null
  private readonly backgroundMaterial: THREE.ShaderMaterial
  private readonly waterSurface: WaterThreeSurface
  private readonly waterSurfaceMaterial: THREE.ShaderMaterial
  private readonly causticsMaterial: THREE.ShaderMaterial
  private readonly bubbles: ResidentBubbleSystem
  private readonly ambientBubbles: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>
  private readonly suspendedParticles: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>
  private readonly luminousParticles: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>
  private readonly lightShafts: THREE.Group
  private readonly ownedTextures: THREE.Texture[]

  constructor(
    private readonly scene: THREE.Scene,
    options: EnvironmentOptions = {},
    dependencies: EnvironmentDependencies = {}
  ) {
    const quality = options.quality ?? 'medium'
    const counts = QUALITY_COUNTS[quality]
    this.effects = { ...DEFAULT_EFFECTS, ...options.effects }
    this.originalFog = scene.fog
    this.group.name = 'Environment'

    const textureLoader = dependencies.textureLoader ?? new THREE.TextureLoader()
    const assetBaseUrl = dependencies.assetBaseUrl ?? resolveEnvironmentAssetBaseUrl()
    const sandTextures = loadSandTextures(textureLoader, assetBaseUrl)
    this.ownedTextures = [...sandTextures.all]

    const seabed = createSeabed(sandTextures)
    this.backgroundMaterial = createBackgroundMaterial(this.optics, sandTextures.backdrop)
    const overheadGlow = createOverheadGlow(this.backgroundMaterial)
    this.waterSurface = createWaterThreeSurface(this.optics)
    this.waterSurfaceMaterial = this.waterSurface.material
    const lighting = createLighting(quality, this.optics)
    this.causticsMaterial = createCausticsMaterial(
      this.fog,
      this.optics,
      quality,
      sandTextures.caustics
    )
    const caustics = createCaustics(this.causticsMaterial)
    this.suspendedParticles = createSuspendedParticles(counts.particles, this.optics)
    this.luminousParticles = createLuminousParticles(counts.luminousParticles, this.optics)
    this.bubbles = new ResidentBubbleSystem(counts.bubbles)
    this.bubbles.points.name = 'Environment:bubbles:resident'
    this.ambientBubbles = createAmbientBubbleField(counts.ambientBubbles, counts.ambientStreams)
    const bubbleGroup = new THREE.Group()
    bubbleGroup.name = 'Environment:bubbles'
    bubbleGroup.add(this.bubbles.points, this.ambientBubbles)
    this.lightShafts = createLightShafts(this.optics)

    this.group.add(
      overheadGlow,
      this.waterSurface.mesh,
      lighting,
      seabed,
      caustics,
      this.suspendedParticles,
      this.luminousParticles,
      bubbleGroup,
      this.lightShafts
    )
    this.scene.add(this.group)

    for (const [name, enabled] of Object.entries(this.effects)) {
      this.setEffectEnabled(name as EnvironmentEffectName, enabled)
    }
  }

  setEffectEnabled(name: EnvironmentEffectName, enabled: boolean): void {
    this.effects[name] = enabled

    if (name === 'fog') {
      if (enabled) {
        this.scene.fog = this.fog
      } else if (this.scene.fog === this.fog) {
        this.scene.fog = this.originalFog
      }
      return
    }

    if (name === 'waterSurface') {
      this.backgroundMaterial.uniforms.waterSurfaceStrength.value = enabled ? 1 : 0
    } else if (name === 'lightShafts') {
      this.backgroundMaterial.uniforms.lightShaftStrength.value = enabled ? 1 : 0
    }

    const object = this.group.getObjectByName(`Environment:${name}`)
    if (object) {
      object.visible = enabled
    }
  }

  isEffectEnabled(name: EnvironmentEffectName): boolean {
    return this.effects[name]
  }

  setVisualTuning(value: VisualTuning): void {
    this.visualTuning = { ...value }
    this.waterSurface.setCalmness(value.waterCalmness)
    this.ambientBubbles.material.uniforms.verticalDensity.value = value.bubbleVerticalDensity
    this.ambientBubbles.material.uniforms.horizontalDensity.value = value.bubbleHorizontalDensity
  }

  resize(width: number, height: number): void {
    this.backgroundMaterial.uniforms.uViewportResolution.value.set(
      Math.max(1, width),
      Math.max(1, height)
    )
    this.waterSurface.resize(width, height)
  }

  update(delta: number, frameContext: EnvironmentFrameContext = {}): void {
    const safeDelta = Math.max(0, delta)
    this.elapsedTime += safeDelta
    this.waterElapsedTime += safeDelta * this.visualTuning.waterSpeed
    this.causticsElapsedTime += safeDelta * this.visualTuning.causticsSpeed
    this.bubbleElapsedTime += safeDelta * this.visualTuning.bubbleRiseSpeed
    this.optics.time.value = this.elapsedTime
    this.backgroundMaterial.uniforms.time.value = this.elapsedTime
    this.waterSurfaceMaterial.uniforms.uTime.value = this.waterElapsedTime
    this.causticsMaterial.uniforms.time.value = this.causticsElapsedTime
    this.ambientBubbles.material.uniforms.time.value = this.bubbleElapsedTime

    if (this.effects.bubbles) {
      this.bubbles.update(safeDelta, frameContext.resident)
    }
    if (this.effects.suspendedParticles) {
      this.suspendedParticles.material.uniforms.time.value = this.elapsedTime
    }
    if (this.effects.luminousParticles) {
      this.luminousParticles.material.uniforms.time.value = this.elapsedTime
    }
  }

  getBubbleDiagnostics(): BubbleDiagnostics {
    return this.bubbles.getDiagnostics()
  }

  dispose(): void {
    this.scene.remove(this.group)
    if (this.scene.fog === this.fog) {
      this.scene.fog = this.originalFog
    }
    disposeObjectTree(this.group)
    this.ownedTextures.forEach((texture) => texture.dispose())
    this.group.clear()
  }
}

function resolveEnvironmentAssetBaseUrl(): string {
  return new URL(
    `${import.meta.env.BASE_URL}materials/underwater-hybrid/`,
    window.location.href
  ).href
}

function loadSandTextures(loader: TextureLoaderPort, assetBaseUrl: string): SandTextures {
  const base = assetBaseUrl.endsWith('/') ? assetBaseUrl : `${assetBaseUrl}/`
  const color = loader.load(`${base}ground-sand-005-color-2k.webp`)
  const normal = loader.load(`${base}ground-sand-005-normal-2k.webp`)
  const backdrop = loader.load(`${base}miyako-shallow-backdrop-v1.png`)
  const caustics = loader.load(`${base}caustics-organic-v1.png`)

  color.colorSpace = THREE.SRGBColorSpace
  backdrop.colorSpace = THREE.SRGBColorSpace
  for (const texture of [color, normal]) {
    texture.flipY = false
    texture.wrapS = THREE.RepeatWrapping
    texture.wrapT = THREE.RepeatWrapping
    texture.repeat.set(6.4, 6.4)
    texture.anisotropy = 16
  }
  caustics.flipY = false
  caustics.wrapS = THREE.MirroredRepeatWrapping
  caustics.wrapT = THREE.MirroredRepeatWrapping
  caustics.anisotropy = 8

  return { color, normal, backdrop, caustics, all: [color, normal, backdrop, caustics] }
}

function createSeabed(
  textures: SandTextures
): THREE.Mesh<THREE.PlaneGeometry, THREE.MeshStandardMaterial> {
  const geometry = new THREE.PlaneGeometry(120, 120, 192, 192)
  geometry.rotateX(-Math.PI / 2)
  const positions = geometry.getAttribute('position') as THREE.BufferAttribute
  for (let index = 0; index < positions.count; index += 1) {
    const x = positions.getX(index)
    const z = positions.getZ(index)
    const depth = Math.max(0, -z - 4)
    const side = Math.max(0, Math.abs(x) - 12)
    const basin = Math.pow(depth / 42, 1.55) * 3.8 + Math.pow(side / 44, 1.6) * 1.2
    const dunes = Math.sin(x * 0.13) * Math.cos(z * 0.10) * 0.045
    positions.setY(index, dunes - basin)
  }
  positions.needsUpdate = true
  geometry.computeVertexNormals()
  const material = new THREE.MeshStandardMaterial({
    color: 0xfff7e8,
    map: textures.color,
    normalMap: textures.normal,
    normalScale: new THREE.Vector2(0.075, 0.075),
    emissive: 0xc8b994,
    emissiveIntensity: 0.4,
    roughness: 0.97,
    metalness: 0,
    transparent: true,
    opacity: 0.055,
    depthWrite: false,
    blending: THREE.AdditiveBlending
  })
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        '#include <common>\nvarying vec3 vSandWorldPosition;'
      )
      .replace(
        '#include <begin_vertex>',
        '#include <begin_vertex>\nvSandWorldPosition = (modelMatrix * vec4(transformed, 1.0)).xyz;'
      )
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        '#include <common>\nvarying vec3 vSandWorldPosition;'
      )
      .replace(
        '#include <opaque_fragment>',
        `float sandDistance = length(vSandWorldPosition.xz - cameraPosition.xz);
         diffuseColor.a *= 1.0 - smoothstep(4.5, 14.0, sandDistance);
         #include <opaque_fragment>`
      )
  }
  material.customProgramCacheKey = () => 'nirai-ground-sand-hybrid-v1'
  const mesh = new THREE.Mesh(geometry, material)
  mesh.name = 'Environment:seabed'
  mesh.position.y = -0.04
  mesh.receiveShadow = true
  return mesh
}

function createBackgroundMaterial(
  optics: UnderwaterOpticsState,
  backdrop: THREE.Texture
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      uBackdropMap: { value: backdrop },
      uViewportResolution: { value: new THREE.Vector2(16, 9) },
      uBackdropAspect: { value: 1672 / 941 },
      time: { value: 0 },
      waterSurfaceStrength: { value: 1 },
      lightShaftStrength: { value: 1 },
      uSunDirection: optics.sunDirection,
      uSunSurfaceAnchor: optics.sunSurfaceAnchor,
      uSunRadiance: optics.sunRadiance,
      uDeepColor: optics.deepColor
    },
    vertexShader: OPTICAL_BACKGROUND_VERTEX_SHADER,
    fragmentShader: OPTICAL_BACKGROUND_FRAGMENT_SHADER,
    side: THREE.FrontSide,
    depthTest: false,
    depthWrite: false,
    fog: false
  })
}

function createOverheadGlow(
  material: THREE.ShaderMaterial
): THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial> {
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material)
  mesh.name = 'Environment:overheadGlow'
  mesh.frustumCulled = false
  mesh.renderOrder = -1000
  return mesh
}

function createWaterSurface(
  optics: UnderwaterOpticsState
): THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial> {
  const geometry = new THREE.PlaneGeometry(72, 72, 160, 160)
  const material = new THREE.ShaderMaterial({
    uniforms: {
      time: { value: 0 },
      uSunDirection: optics.sunDirection,
      uSunSurfaceAnchor: optics.sunSurfaceAnchor,
      uSunRadiance: optics.sunRadiance,
      uStageCenter: optics.stageCenter,
      uSurfaceY: optics.surfaceY
    },
    vertexShader: OPTICAL_WATER_VERTEX_SHADER,
    fragmentShader: OPTICAL_WATER_FRAGMENT_SHADER,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    blending: THREE.NormalBlending
  })
  const surface = new THREE.Mesh(geometry, material)
  surface.name = 'Environment:waterSurface'
  surface.position.set(0, optics.surfaceY.value, -4.2)
  surface.rotation.x = -Math.PI / 2
  surface.renderOrder = 5
  return surface
}

function createLighting(
  quality: EnvironmentQuality,
  optics: UnderwaterOpticsState
): THREE.Group {
  const group = new THREE.Group()
  group.name = 'Environment:lighting'

  const hemisphere = new THREE.HemisphereLight(0x8be8ff, 0x07518a, 0.54)
  const sunlight = new THREE.DirectionalLight(0xf2ffff, 1.34)
  sunlight.position.copy(optics.sunSurfaceAnchor.value)
  sunlight.target.position.copy(optics.stageCenter.value)
  sunlight.castShadow = true
  const shadowSize = quality === 'high' ? 2048 : 1024
  sunlight.shadow.mapSize.set(shadowSize, shadowSize)
  sunlight.shadow.camera.near = 0.5
  sunlight.shadow.camera.far = 18
  sunlight.shadow.camera.left = -7
  sunlight.shadow.camera.right = 7
  sunlight.shadow.camera.top = 7
  sunlight.shadow.camera.bottom = -7
  sunlight.shadow.bias = -0.0004

  const stageLight = new THREE.SpotLight(0xe8fcff, 7.65, 18, 0.48, 0.84, 1.18)
  stageLight.position.copy(optics.sunSurfaceAnchor.value)
  stageLight.target.position.copy(optics.stageCenter.value)
  const cyanFill = new THREE.DirectionalLight(0x45b7d7, 0.28)
  cyanFill.position.set(4.5, 2.6, 3.0)
  group.add(hemisphere, sunlight, sunlight.target, stageLight, stageLight.target, cyanFill)
  return group
}

function createLightShafts(optics: UnderwaterOpticsState): THREE.Group {
  const group = new THREE.Group()
  group.name = 'Environment:lightShafts'
  group.userData.renderMode = 'depth-aware-analytic-volume'
  group.userData.densitySource = 'shared-surface-caustics'
  group.userData.sunSurfaceAnchor = optics.sunSurfaceAnchor.value
  return group
}

function createCausticsMaterial(
  fog: THREE.FogExp2,
  optics: UnderwaterOpticsState,
  quality: EnvironmentQuality,
  causticsMap: THREE.Texture
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      time: { value: 0 },
      uCausticsMap: { value: causticsMap },
      fogColor: { value: fog.color.clone() },
      fogDensity: { value: fog.density },
      uSunDirection: optics.sunDirection,
      uSunSurfaceAnchor: optics.sunSurfaceAnchor,
      uSunRadiance: optics.sunRadiance,
      uStageCenter: optics.stageCenter,
      uSurfaceY: optics.surfaceY,
      uIntensity: {
        value: quality === 'high' ? 0.74 : quality === 'medium' ? 0.66 : 0.58
      }
    },
    vertexShader: OPTICAL_CAUSTICS_VERTEX_SHADER,
    fragmentShader: OPTICAL_CAUSTICS_FRAGMENT_SHADER,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide
  })
}

function createCaustics(
  material: THREE.ShaderMaterial
): THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial> {
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(56, 56), material)
  mesh.name = 'Environment:caustics'
  mesh.position.y = 0.012
  mesh.rotation.x = -Math.PI / 2
  mesh.renderOrder = 2
  return mesh
}

function createSuspendedParticles(
  count: number,
  optics: UnderwaterOpticsState
): THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial> {
  return createDriftingParticles(count, 0x4e495241, false, optics)
}

function createLuminousParticles(
  count: number,
  optics: UnderwaterOpticsState
): THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial> {
  return createDriftingParticles(count, 0x4c554d49, true, optics)
}

function createDriftingParticles(
  count: number,
  seed: number,
  luminous: boolean,
  optics: UnderwaterOpticsState
): THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial> {
  const random = createRandom(seed)
  const positions = new Float32Array(count * 3)
  const sizes = new Float32Array(count)
  const phases = new Float32Array(count)
  const drifts = new Float32Array(count)
  const speeds = new Float32Array(count)
  const layers = new Float32Array(count)
  const lightAffinities = new Float32Array(count)
  for (let index = 0; index < count; index += 1) {
    const depthBias = Math.pow(random(), 0.72)
    const centerBias = luminous && random() < 0.48
    positions[index * 3] = (random() - 0.5) * (centerBias ? 8 : 18 + depthBias * 18)
    positions[index * 3 + 1] = 0.12 + random() * 5.1
    positions[index * 3 + 2] = centerBias ? 0.2 - depthBias * 10 : 2.2 - depthBias * 20
    sizes[index] = luminous ? 0.46 + random() * 0.46 : 0.68 + random() * 0.92
    phases[index] = random() * Math.PI * 2
    drifts[index] = 0.035 + random() * (luminous ? 0.13 : 0.08)
    speeds[index] = 0.08 + random() * 0.18
    layers[index] = depthBias < 0.32 ? 0 : depthBias < 0.72 ? 0.5 : 1
    lightAffinities[index] = centerBias ? 0.82 + random() * 0.18 : random()
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1))
  geometry.setAttribute('phase', new THREE.BufferAttribute(phases, 1))
  geometry.setAttribute('drift', new THREE.BufferAttribute(drifts, 1))
  geometry.setAttribute('speed', new THREE.BufferAttribute(speeds, 1))
  geometry.setAttribute('layer', new THREE.BufferAttribute(layers, 1))
  geometry.setAttribute('lightAffinity', new THREE.BufferAttribute(lightAffinities, 1))

  const material = new THREE.ShaderMaterial({
    uniforms: {
      time: { value: 0 },
      particleColor: {
        value: new THREE.Color().setRGB(
          luminous ? 0.36 : 0.30,
          luminous ? 0.66 : 0.52,
          luminous ? 0.67 : 0.54
        )
      },
      particleOpacity: { value: luminous ? 0.68 : 0.28 },
      glowStrength: { value: 0 },
      stageCenter: optics.stageCenter
    },
    vertexShader: PARTICLE_VERTEX_SHADER,
    fragmentShader: PARTICLE_FRAGMENT_SHADER,
    transparent: true,
    depthWrite: false,
    blending: THREE.NormalBlending
  })

  const points = new THREE.Points(geometry, material)
  points.name = luminous
    ? 'Environment:luminousParticles'
    : 'Environment:suspendedParticles'
  return points
}

function createRandom(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0
    return state / 0x100000000
  }
}

function disposeObjectTree(root: THREE.Object3D): void {
  const geometries = new Set<THREE.BufferGeometry>()
  const materials = new Set<THREE.Material>()
  root.traverse((object) => {
    const renderable = object as THREE.Mesh | THREE.Points
    if (renderable.geometry) geometries.add(renderable.geometry)
    if (renderable.material) {
      const objectMaterials = Array.isArray(renderable.material) ? renderable.material : [renderable.material]
      objectMaterials.forEach((material) => materials.add(material))
    }
  })
  geometries.forEach((geometry) => geometry.dispose())
  materials.forEach((material) => material.dispose())
}
