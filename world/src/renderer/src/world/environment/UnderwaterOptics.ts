import * as THREE from 'three'

export const BASE_UNDERWATER_DEEP_COLOR = new THREE.Color(0.012, 0.080, 0.25)
export const BASE_UNDERWATER_SCATTERING_COLOR = new THREE.Color(0.015, 0.20, 0.40)
export const BASE_UNDERWATER_SCATTERING_STRENGTH = 0.40

export interface UnderwaterOpticsState {
  readonly time: THREE.Uniform<number>
  readonly sunDirection: THREE.Uniform<THREE.Vector3>
  readonly surfaceY: THREE.Uniform<number>
  readonly stageCenter: THREE.Uniform<THREE.Vector3>
  readonly sunSurfaceAnchor: THREE.Uniform<THREE.Vector3>
  readonly sunRadiance: THREE.Uniform<THREE.Color>
  readonly deepColor: THREE.Uniform<THREE.Color>
  readonly absorption: THREE.Uniform<THREE.Color>
  readonly scatteringColor: THREE.Uniform<THREE.Color>
  readonly scatteringStrength: THREE.Uniform<number>
  /** @deprecated Use absorption. Kept as a compatibility alias during M0. */
  readonly extinction: THREE.Uniform<THREE.Color>
}

export function calculateSunSurfaceAnchor(
  stageCenter: THREE.Vector3,
  surfaceY: number,
  sunDirection: THREE.Vector3
): THREE.Vector3 {
  if (surfaceY <= stageCenter.y) {
    return stageCenter.clone()
  }

  const safeSunY = Math.max(sunDirection.y, 0.15)
  const distanceToSurface = (surfaceY - stageCenter.y) / safeSunY
  const anchor = stageCenter.clone().addScaledVector(sunDirection, distanceToSurface)
  anchor.y = surfaceY
  return anchor
}

export function createUnderwaterOpticsState(): UnderwaterOpticsState {
  const sunDirection = new THREE.Vector3(-0.12, 0.72, -0.68).normalize()
  const surfaceY = 4.05
  const stageCenter = new THREE.Vector3(0, 0, -0.55)
  const absorption = new THREE.Uniform(new THREE.Color(0.075, 0.025, 0.012))

  return {
    time: new THREE.Uniform(0),
    sunDirection: new THREE.Uniform(sunDirection),
    surfaceY: new THREE.Uniform(surfaceY),
    stageCenter: new THREE.Uniform(stageCenter),
    sunSurfaceAnchor: new THREE.Uniform(
      calculateSunSurfaceAnchor(stageCenter, surfaceY, sunDirection)
    ),
    sunRadiance: new THREE.Uniform(new THREE.Color(1.18, 1.48, 1.62)),
    deepColor: new THREE.Uniform(BASE_UNDERWATER_DEEP_COLOR.clone()),
    absorption,
    scatteringColor: new THREE.Uniform(BASE_UNDERWATER_SCATTERING_COLOR.clone()),
    scatteringStrength: new THREE.Uniform(BASE_UNDERWATER_SCATTERING_STRENGTH),
    extinction: absorption
  }
}

export function beerLambertTransmittance(
  distance: number,
  extinction: THREE.Color
): THREE.Color {
  const safeDistance = Math.max(0, distance)
  return new THREE.Color(
    Math.exp(-extinction.r * safeDistance),
    Math.exp(-extinction.g * safeDistance),
    Math.exp(-extinction.b * safeDistance)
  )
}

// The compact wave/caustic structure is adapted from the MIT-licensed
// WaterThreeJS project. See THIRD_PARTY_NOTICES.md for attribution.
export const SURFACE_WAVE_GLSL = /* glsl */ `
  float sampleSurfaceWave(vec2 point, float waveTime) {
    vec2 p = point * 0.72;
    float wave = sin(p.x * 1.13 + waveTime * 0.42) * 0.46;
    wave += sin(p.y * 1.41 - waveTime * 0.35) * 0.34;
    wave += sin((p.x + p.y) * 2.17 + waveTime * 0.51) * 0.15;
    wave += sin((p.x - p.y) * 3.31 - waveTime * 0.39) * 0.05;
    return wave;
  }

  vec2 sampleSurfaceSlope(vec2 point, float waveTime) {
    const float epsilon = 0.035;
    float center = sampleSurfaceWave(point, waveTime);
    return vec2(
      sampleSurfaceWave(point + vec2(epsilon, 0.0), waveTime) - center,
      sampleSurfaceWave(point + vec2(0.0, epsilon), waveTime) - center
    ) / epsilon;
  }
`

export const CAUSTIC_FIELD_GLSL = /* glsl */ `
  ${SURFACE_WAVE_GLSL}

  float sampleCausticField(vec2 surfacePoint, float waveTime) {
    vec2 slope = sampleSurfaceSlope(surfacePoint, waveTime);
    vec2 slopeAhead = sampleSurfaceSlope(
      surfacePoint + vec2(0.055, -0.041),
      waveTime + 0.018
    );
    float focusing = length(slopeAhead - slope);
    float ripple = sampleSurfaceWave(surfacePoint * 1.37 + slope * 0.31, waveTime);
    float ridgeA = pow(
      1.0 - abs(sin(surfacePoint.x * 1.06 + surfacePoint.y * 0.61 + ripple * 2.8)),
      9.0
    );
    float ridgeB = pow(
      1.0 - abs(sin(surfacePoint.x * -0.68 + surfacePoint.y * 0.91 - ripple * 2.1 + 1.7)),
      11.0
    );
    float focusRidge = 1.0 - smoothstep(0.045, 0.24, focusing);
    return clamp(ridgeA * 0.52 + ridgeB * 0.42 + focusRidge * 0.18, 0.0, 1.0);
  }
`
