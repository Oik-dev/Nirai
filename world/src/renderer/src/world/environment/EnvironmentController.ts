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
  BASE_UNDERWATER_DEEP_COLOR,
  BASE_UNDERWATER_SCATTERING_COLOR,
  BASE_UNDERWATER_SCATTERING_STRENGTH,
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
export type SeabedMaterialDebugLayer = 'colorMap' | 'ao' | 'macroVariation'
export type SeabedSurfaceDebugLayer = 'shadows' | 'geometry'

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
  waterSurface: true,
  caustics: true,
  suspendedParticles: true,
  luminousParticles: true,
  bubbles: true,
  lightShafts: true
}

const SEABED_WORLD_Y = -0.04
const CAUSTICS_SURFACE_OFFSET = 0.002
const BACKDROP_RADIUS = 72
const BASE_FOG_DENSITY = 0.018
const BASE_SAND_WHITE_MIX = 0.58
const BASE_SAND_NORMAL_SCALE = 0.075
const BASE_SAND_BUMP_SCALE = 0.012
const BASE_HEMISPHERE_INTENSITY = 0.72
const BASE_CYAN_FILL_INTENSITY = 0.36
const DARK_UNDERWATER_DEEP_COLOR = new THREE.Color(0.002, 0.035, 0.16)
const PALE_UNDERWATER_DEEP_COLOR = new THREE.Color(0.035, 0.16, 0.34)
const DARK_UNDERWATER_SCATTERING_COLOR = new THREE.Color(0.004, 0.10, 0.28)
const PALE_UNDERWATER_SCATTERING_COLOR = new THREE.Color(0.045, 0.32, 0.54)

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
  readonly ao: THREE.Texture
  readonly bump: THREE.Texture
  readonly gloss: THREE.Texture
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
  private readonly fog = new THREE.FogExp2(0x1689ab, BASE_FOG_DENSITY)
  private readonly originalFog: THREE.Fog | THREE.FogExp2 | null
  private readonly seabed: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshStandardMaterial>
  private readonly seabedMaterial: THREE.MeshStandardMaterial
  private readonly backgroundMaterial: THREE.ShaderMaterial
  private readonly backdrop: THREE.Mesh<THREE.SphereGeometry, THREE.ShaderMaterial>
  private readonly waterSurface: WaterThreeSurface
  private readonly waterSurfaceMaterial: THREE.ShaderMaterial
  private readonly lighting: THREE.Group
  private readonly causticsMaterial: THREE.ShaderMaterial
  private readonly causticsMesh: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>
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
    this.seabed = seabed
    this.seabedMaterial = seabed.material
    this.backgroundMaterial = createBackgroundMaterial(
      this.optics,
      sandTextures.backdrop,
      this.fog.color
    )
    this.backdrop = createOverheadGlow(this.backgroundMaterial)
    this.waterSurface = createWaterThreeSurface(this.optics)
    this.waterSurfaceMaterial = this.waterSurface.material
    this.lighting = createLighting(quality, this.optics)
    this.causticsMaterial = createCausticsMaterial(
      this.fog,
      this.optics,
      quality,
      sandTextures.caustics
    )
    const caustics = createCaustics(this.causticsMaterial)
    this.causticsMesh = caustics
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
      this.backdrop,
      this.waterSurface.mesh,
      this.lighting,
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
      const strength = enabled ? this.visualTuning.waterSurfacePresence : 0
      this.backgroundMaterial.uniforms.waterSurfaceStrength.value = strength
      this.waterSurface.setVisibility(strength)
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

  setSeabedMaterialLayerEnabled(name: SeabedMaterialDebugLayer, enabled: boolean): void {
    const strength = enabled ? 1 : 0
    if (name === 'colorMap') {
      const uniform = this.seabedMaterial.userData.sandColorMapStrengthUniform as
        | THREE.IUniform<number>
        | undefined
      if (uniform) uniform.value = strength
      return
    }
    if (name === 'ao') {
      this.seabedMaterial.aoMapIntensity = enabled ? 0.20 : 0
      return
    }
    const uniform = this.seabedMaterial.userData.sandMacroStrengthUniform as
      | THREE.IUniform<number>
      | undefined
    if (uniform) uniform.value = strength
  }

  getSeabedMaterialLayerEnabled(name: SeabedMaterialDebugLayer): boolean {
    if (name === 'colorMap') {
      const uniform = this.seabedMaterial.userData.sandColorMapStrengthUniform as
        | THREE.IUniform<number>
        | undefined
      return (uniform?.value ?? 1) > 0.5
    }
    if (name === 'ao') {
      return this.seabedMaterial.aoMapIntensity > 0
    }
    const uniform = this.seabedMaterial.userData.sandMacroStrengthUniform as
      | THREE.IUniform<number>
      | undefined
    return (uniform?.value ?? 1) > 0.5
  }

  setSeabedSurfaceLayerEnabled(name: SeabedSurfaceDebugLayer, enabled: boolean): void {
    if (name === 'shadows') {
      this.seabed.receiveShadow = enabled
      return
    }
    applySeabedGeometryProfile(this.seabed.geometry, enabled, true)
    applySeabedGeometryProfile(this.causticsMesh.geometry, enabled, false)
    this.seabed.userData.geometryProfile = enabled ? 'deformed' : 'flat-debug'
  }

  getSeabedSurfaceLayerEnabled(name: SeabedSurfaceDebugLayer): boolean {
    if (name === 'shadows') {
      return this.seabed.receiveShadow
    }
    return this.seabed.userData.geometryProfile !== 'flat-debug'
  }

  setVisualTuning(value: VisualTuning): void {
    this.visualTuning = { ...value }
    this.waterSurface.setCalmness(value.waterCalmness)
    this.ambientBubbles.material.uniforms.verticalDensity.value = value.bubbleVerticalDensity
    this.ambientBubbles.material.uniforms.horizontalDensity.value = value.bubbleHorizontalDensity

    this.fog.density = BASE_FOG_DENSITY * value.horizonHaze
    this.causticsMaterial.uniforms.fogDensity.value = this.fog.density
    this.backgroundMaterial.uniforms.uHorizonHaze.value = value.horizonHaze

    applyWaterPaleness(this.optics, value.waterPaleness)

    const sandWhiteMixUniform = this.seabedMaterial.userData.sandWhiteMixUniform as
      | THREE.IUniform<number>
      | undefined
    if (sandWhiteMixUniform) {
      sandWhiteMixUniform.value = THREE.MathUtils.clamp(
        BASE_SAND_WHITE_MIX * value.sandWhiteness,
        0,
        0.96
      )
    }
    const sandRelief = THREE.MathUtils.clamp(value.sandRelief, 0, 20)
    this.seabedMaterial.normalScale.setScalar(BASE_SAND_NORMAL_SCALE * sandRelief)
    this.seabedMaterial.bumpScale = BASE_SAND_BUMP_SCALE * sandRelief

    const surfaceStrength = this.effects.waterSurface ? value.waterSurfacePresence : 0
    this.backgroundMaterial.uniforms.waterSurfaceStrength.value = surfaceStrength
    this.waterSurface.setVisibility(surfaceStrength)

    const hemisphere = this.lighting.getObjectByName(
      'Environment:lighting:residentHemisphere'
    ) as THREE.HemisphereLight | undefined
    const cyanFill = this.lighting.getObjectByName(
      'Environment:lighting:residentFill'
    ) as THREE.DirectionalLight | undefined
    if (hemisphere) hemisphere.intensity = BASE_HEMISPHERE_INTENSITY * value.residentBrightness
    if (cyanFill) cyanFill.intensity = BASE_CYAN_FILL_INTENSITY * value.residentBrightness
  }

  resize(width: number, height: number): void {
    this.waterSurface.resize(width, height)
  }

  getSeabedWorldY(): number {
    return SEABED_WORLD_Y
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
  const color = loader.load(`${base}GroundSand005_COL_4K.jpg`)
  const normal = loader.load(`${base}GroundSand005_NRM_4K.jpg`)
  const ao = loader.load(`${base}GroundSand005_AO_4K.jpg`)
  const bump = loader.load(`${base}GroundSand005_BUMP_4K.jpg`)
  const gloss = loader.load(`${base}GroundSand005_GLOSS_4K.jpg`)
  const backdrop = loader.load(`${base}miyako-shallow-backdrop-v1.png`)
  const caustics = loader.load(`${base}caustics-organic-v1.png`)

  color.colorSpace = THREE.SRGBColorSpace
  backdrop.colorSpace = THREE.SRGBColorSpace
  ao.channel = 2
  for (const texture of [color, normal, ao, bump, gloss]) {
    texture.flipY = false
    texture.wrapS = THREE.RepeatWrapping
    texture.wrapT = THREE.RepeatWrapping
    texture.repeat.set(11.5, 11.5)
    texture.anisotropy = 16
  }
  backdrop.anisotropy = 8
  caustics.flipY = false
  caustics.wrapS = THREE.MirroredRepeatWrapping
  caustics.wrapT = THREE.MirroredRepeatWrapping
  caustics.anisotropy = 8

  return {
    color,
    normal,
    ao,
    bump,
    gloss,
    backdrop,
    caustics,
    all: [color, normal, ao, bump, gloss, backdrop, caustics]
  }
}

function sampleSeabedLocalHeight(x: number, z: number): number {
  // Keep the playable area almost level, then let the far seabed sink gently
  // below the view instead of flattening into a visible horizon. The old basin
  // depended almost entirely on Z, so its tessellated contour rows could read as
  // moving horizontal bands at grazing camera angles.
  const depthWarp = Math.sin(x * 0.055) * 1.35
    + Math.sin(x * 0.11 + z * 0.028) * 0.55
  const forwardDepth = Math.max(0, -z - 10 + depthWarp)
  const depthT = THREE.MathUtils.clamp(forwardDepth / 76, 0, 1)
  const smoothDepth = depthT * depthT * (3 - 2 * depthT)
  const farContinuation = Math.pow(Math.max(0, forwardDepth - 58) / 42, 1.35)

  const sideDistance = Math.max(0, Math.abs(x) - 18)
  const sideT = THREE.MathUtils.clamp(sideDistance / 58, 0, 1)
  const smoothSide = sideT * sideT * (3 - 2 * sideT)

  const broadUndulationFade = 1 - THREE.MathUtils.smoothstep(forwardDepth, 18, 46)
  const broadUndulation = (
    Math.sin(x * 0.085 + z * 0.052)
    + Math.cos(x * 0.047 - z * 0.071) * 0.62
  ) * 0.018 * broadUndulationFade

  const sink = smoothDepth * 7.4 + farContinuation * 4.8 + smoothSide * 1.15
  return broadUndulation - sink
}

function applySeabedGeometryProfile(
  geometry: THREE.PlaneGeometry,
  deformed: boolean,
  recomputeNormals: boolean
): void {
  const positions = geometry.getAttribute('position') as THREE.BufferAttribute
  for (let index = 0; index < positions.count; index += 1) {
    positions.setY(
      index,
      deformed ? sampleSeabedLocalHeight(positions.getX(index), positions.getZ(index)) : 0
    )
  }
  positions.needsUpdate = true
  if (recomputeNormals) geometry.computeVertexNormals()
}

function applyWaterPaleness(optics: UnderwaterOpticsState, value: number): void {
  const paleness = THREE.MathUtils.clamp(value, 0, 2)
  if (paleness <= 1) {
    optics.deepColor.value.copy(DARK_UNDERWATER_DEEP_COLOR).lerp(
      BASE_UNDERWATER_DEEP_COLOR,
      paleness
    )
    optics.scatteringColor.value.copy(DARK_UNDERWATER_SCATTERING_COLOR).lerp(
      BASE_UNDERWATER_SCATTERING_COLOR,
      paleness
    )
    optics.scatteringStrength.value = THREE.MathUtils.lerp(
      0.24,
      BASE_UNDERWATER_SCATTERING_STRENGTH,
      paleness
    )
    return
  }

  const paleMix = paleness - 1
  optics.deepColor.value.copy(BASE_UNDERWATER_DEEP_COLOR).lerp(
    PALE_UNDERWATER_DEEP_COLOR,
    paleMix
  )
  optics.scatteringColor.value.copy(BASE_UNDERWATER_SCATTERING_COLOR).lerp(
    PALE_UNDERWATER_SCATTERING_COLOR,
    paleMix
  )
  optics.scatteringStrength.value = THREE.MathUtils.lerp(
    BASE_UNDERWATER_SCATTERING_STRENGTH,
    0.60,
    paleMix
  )
}

function createSeabed(
  textures: SandTextures
): THREE.Mesh<THREE.PlaneGeometry, THREE.MeshStandardMaterial> {
  // The seabed is the physical floor now, not a transparent bridge to a photo.
  // Extend it beyond the camera far plane so its edge disappears into underwater fog.
  const geometry = new THREE.PlaneGeometry(220, 220, 320, 320)
  geometry.rotateX(-Math.PI / 2)
  const uv = geometry.getAttribute('uv') as THREE.BufferAttribute
  geometry.setAttribute('uv2', uv.clone())
  const positions = geometry.getAttribute('position') as THREE.BufferAttribute
  applySeabedGeometryProfile(geometry, true, true)

  const sandWhiteMixUniform: THREE.IUniform<number> = { value: BASE_SAND_WHITE_MIX }
  const sandColorMapStrengthUniform: THREE.IUniform<number> = { value: 1 }
  const sandMacroStrengthUniform: THREE.IUniform<number> = { value: 1 }
  const material = new THREE.MeshStandardMaterial({
    color: 0xfffbf2,
    map: textures.color,
    normalMap: textures.normal,
    normalScale: new THREE.Vector2(BASE_SAND_NORMAL_SCALE, BASE_SAND_NORMAL_SCALE),
    aoMap: textures.ao,
    aoMapIntensity: 0.20,
    bumpMap: textures.bump,
    bumpScale: BASE_SAND_BUMP_SCALE,
    roughness: 1,
    metalness: 0,
    emissive: 0xdce7df,
    emissiveIntensity: 0.018,
    transparent: false,
    opacity: 1,
    depthWrite: true,
    blending: THREE.NormalBlending,
    fog: true
  })

  // Preserve the pale white-sand grade while restoring just enough micro detail
  // to break the flat-sheet look. The broad modulation is intentionally tiny and
  // fades with view distance so it reads as natural sand variation, not staining.
  // Gloss remains unused and roughness stays fully matte.
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uSandWhiteMix = sandWhiteMixUniform
    shader.uniforms.uSandColorMapStrength = sandColorMapStrengthUniform
    shader.uniforms.uSandMacroStrength = sandMacroStrengthUniform
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        '#include <common>\nuniform float uSandWhiteMix;\nuniform float uSandColorMapStrength;\nuniform float uSandMacroStrength;'
      )
      .replace(
        '#include <map_fragment>',
        `vec4 sandBaseDiffuseColor = diffuseColor;
       #include <map_fragment>
       diffuseColor = mix(sandBaseDiffuseColor, diffuseColor, uSandColorMapStrength);
       float sandLuma = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));
       vec3 sandNeutral = mix(diffuseColor.rgb, vec3(sandLuma), 0.46);
       diffuseColor.rgb = mix(sandNeutral, vec3(1.0, 0.995, 0.985), uSandWhiteMix);

       float sandMacroField = 0.50
         + 0.26 * sin(vMapUv.x * 0.74 + vMapUv.y * 0.31)
         + 0.24 * cos(vMapUv.x * -0.42 + vMapUv.y * 0.83);
       float sandMacroDistanceFade = 1.0 - smoothstep(18.0, 52.0, length(vViewPosition));
       float sandMacroVariation = (sandMacroField - 0.50) * 0.05 * sandMacroDistanceFade;
       diffuseColor.rgb *= 1.0 + sandMacroVariation * uSandMacroStrength;`
      )
  }
  material.customProgramCacheKey = () => 'nirai-ground-sand-005-pale-white-debuggable-v5'
  material.userData.sandWhiteMixUniform = sandWhiteMixUniform
  material.userData.sandColorMapStrengthUniform = sandColorMapStrengthUniform
  material.userData.sandMacroStrengthUniform = sandMacroStrengthUniform
  material.userData.roughnessSource = 'constant-matte-trial'
  material.userData.colorGrade = 'pale-white-desaturated-trial'
  material.userData.macroVariation = 'low-frequency-near-mid-distance-fade'
  material.userData.distanceTransition = 'scene-fog'

  const mesh = new THREE.Mesh(geometry, material)
  mesh.name = 'Environment:seabed'
  mesh.position.y = SEABED_WORLD_Y
  mesh.receiveShadow = true
  mesh.userData.geometryProfile = 'smooth-far-sink-v2'
  return mesh
}

function createBackgroundMaterial(
  optics: UnderwaterOpticsState,
  backdrop: THREE.Texture,
  fogColor: THREE.Color
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      uBackdropMap: { value: backdrop },
      time: { value: 0 },
      waterSurfaceStrength: { value: 1 },
      lightShaftStrength: { value: 1 },
      uSunDirection: optics.sunDirection,
      uSunSurfaceAnchor: optics.sunSurfaceAnchor,
      uSunRadiance: optics.sunRadiance,
      uDeepColor: optics.deepColor,
      uFogColor: { value: fogColor.clone() },
      uHorizonHaze: { value: 1 }
    },
    vertexShader: OPTICAL_BACKGROUND_VERTEX_SHADER,
    fragmentShader: OPTICAL_BACKGROUND_FRAGMENT_SHADER,
    side: THREE.BackSide,
    depthTest: false,
    depthWrite: false,
    fog: false
  })
}

function createOverheadGlow(
  material: THREE.ShaderMaterial
): THREE.Mesh<THREE.SphereGeometry, THREE.ShaderMaterial> {
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(BACKDROP_RADIUS, 64, 32),
    material
  )
  mesh.name = 'Environment:overheadGlow'
  mesh.frustumCulled = false
  mesh.renderOrder = -1000
  mesh.userData.renderMode = 'inner-skydome'
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

  const hemisphere = new THREE.HemisphereLight(
    0xb0f3ff,
    0x1c7898,
    BASE_HEMISPHERE_INTENSITY
  )
  hemisphere.name = 'Environment:lighting:residentHemisphere'
  const sunlight = new THREE.DirectionalLight(0xf2ffff, 1.42)
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
  const cyanFill = new THREE.DirectionalLight(0x63cce5, BASE_CYAN_FILL_INTENSITY)
  cyanFill.name = 'Environment:lighting:residentFill'
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
  // Caustics must live on the same physical surface as the sand. A separate
  // horizontal quad reads as a floating light sheet once the seabed slopes.
  // Keep only a tiny offset to avoid z-fighting with the physical sand mesh.
  const geometry = new THREE.PlaneGeometry(56, 56, 96, 96)
  geometry.rotateX(-Math.PI / 2)
  applySeabedGeometryProfile(geometry, true, false)

  const mesh = new THREE.Mesh(geometry, material)
  mesh.name = 'Environment:caustics'
  mesh.position.y = SEABED_WORLD_Y + CAUSTICS_SURFACE_OFFSET
  mesh.renderOrder = 2
  mesh.userData.surfaceAttachment = 'seabed-following'
  mesh.userData.surfaceOffset = CAUSTICS_SURFACE_OFFSET
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
