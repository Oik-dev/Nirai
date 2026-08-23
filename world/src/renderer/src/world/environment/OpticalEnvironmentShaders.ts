import { CAUSTIC_FIELD_GLSL, SURFACE_WAVE_GLSL } from './UnderwaterOptics'

export const OPTICAL_BACKGROUND_VERTEX_SHADER = /* glsl */ `
  varying vec3 vWorldDirection;

  void main() {
    vWorldDirection = normalize(mat3(modelMatrix) * position);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`

export const OPTICAL_BACKGROUND_FRAGMENT_SHADER = /* glsl */ `
  uniform float time;
  uniform float waterSurfaceStrength;
  uniform float lightShaftStrength;
  uniform vec3 uSunDirection;
  uniform vec3 uSunSurfaceAnchor;
  uniform vec3 uSunRadiance;
  uniform vec3 uDeepColor;
  varying vec3 vWorldDirection;

  float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
  }

  ${SURFACE_WAVE_GLSL}

  void main() {
    vec3 direction = normalize(vWorldDirection);
    float upward = clamp(direction.y * 0.5 + 0.5, 0.0, 1.0);
    float horizon = pow(1.0 - abs(direction.y), 2.5);

    vec3 deep = uDeepColor;
    vec3 clearWater = vec3(0.004, 0.23, 0.58);
    vec3 color = mix(deep, clearWater, smoothstep(0.10, 0.96, upward));
    color = mix(color, vec3(0.003, 0.12, 0.32), horizon * 0.12);

    float sunAlignment = max(dot(direction, normalize(uSunDirection)), 0.0);
    float sunHalo = pow(sunAlignment, 34.0);
    float sunCore = pow(sunAlignment, 150.0);
    color += uSunRadiance * sunHalo * waterSurfaceStrength * 0.075;
    color += uSunRadiance * sunCore * waterSurfaceStrength * 0.24;

    float upperScatter = smoothstep(0.57, 0.96, upward) * (0.05 + sunHalo * 0.10);
    color += vec3(0.015, 0.10, 0.18) * upperScatter;

    float grain = hash21(floor(direction.xy * 175.0 + time * 0.012));
    color += (grain - 0.5) * 0.0025;
    gl_FragColor = vec4(color, 1.0);
  }
`

export const OPTICAL_WATER_VERTEX_SHADER = /* glsl */ `
  uniform float time;
  varying vec2 vUv;
  varying vec3 vWorldPosition;
  varying float vWave;

  ${SURFACE_WAVE_GLSL}

  void main() {
    vUv = uv;
    vec3 transformed = position;
    vWave = sampleSurfaceWave(position.xy, time);
    transformed.z += vWave * 0.105;
    vec4 worldPosition = modelMatrix * vec4(transformed, 1.0);
    vWorldPosition = worldPosition.xyz;
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`

export const OPTICAL_WATER_FRAGMENT_SHADER = /* glsl */ `
  uniform float time;
  uniform vec3 uSunDirection;
  uniform vec3 uSunSurfaceAnchor;
  uniform vec3 uSunRadiance;
  uniform vec3 uStageCenter;
  uniform float uSurfaceY;
  varying vec2 vUv;
  varying vec3 vWorldPosition;
  varying float vWave;

  ${SURFACE_WAVE_GLSL}

  void main() {
    vec2 worldXZ = vWorldPosition.xz;
    float surfaceDistance = length(cameraPosition - vWorldPosition);
    float detailFade = exp(-surfaceDistance * 0.085);
    vec2 broadSlope = sampleSurfaceSlope(worldXZ * 0.72, time) * 0.48;
    vec2 microSlope = sampleSurfaceSlope(worldXZ.yx * 1.90 + 3.2, time * 1.17)
      * 0.22 * detailFade;
    vec2 slope = broadSlope + microSlope;
    vec3 normal = normalize(vec3(-slope.x * 0.075, 1.0, -slope.y * 0.075));
    vec3 rayToSurface = normalize(vWorldPosition - cameraPosition);
    vec3 viewDirection = -rayToSurface;
    float viewCosine = clamp(abs(dot(normal, rayToSurface)), 0.0, 1.0);
    float fresnel = 0.02 + 0.98 * pow(1.0 - viewCosine, 5.0);

    vec3 refractedDirection = refract(rayToSurface, -normal, 1.333);
    float totalInternalReflection = 1.0 - step(0.0001, dot(refractedDirection, refractedDirection));
    vec3 skyDirection = normalize(refractedDirection + vec3(0.0001));
    float skyHeight = smoothstep(-0.10, 0.82, skyDirection.y);
    float skySunAlignment = max(dot(skyDirection, normalize(uSunDirection)), 0.0);
    float refractedSun = pow(skySunAlignment, 96.0);
    vec3 refractedSky = mix(vec3(0.055, 0.42, 0.70), vec3(0.56, 0.84, 0.94), skyHeight);
    refractedSky += uSunRadiance * refractedSun * 0.55;

    vec3 reflectedWater = mix(vec3(0.012, 0.19, 0.34), vec3(0.045, 0.34, 0.50), fresnel);
    vec3 color = mix(refractedSky, reflectedWater, fresnel);
    color = mix(color, reflectedWater, totalInternalReflection);

    float sunGlint = pow(
      max(dot(reflect(-normalize(uSunDirection), normal), viewDirection), 0.0),
      72.0
    );
    float waveFacet = smoothstep(0.30, 0.92, length(slope))
      * (0.70 + 0.30 * sin(time * 0.47 + worldXZ.x * 5.2 - worldXZ.y * 4.1));
    float sunFootprint = 1.0 - smoothstep(
      2.0,
      12.0,
      length((worldXZ - uSunSurfaceAnchor.xz) * vec2(0.46, 0.70))
    );
    float edgeFade = 1.0 - smoothstep(0.25, 0.74, length(vUv - 0.5));
    float horizonFade = smoothstep(0.025, 0.20, viewCosine);
    float surfaceIllumination = max(dot(normal, normalize(uSunDirection)), 0.0)
      * sunFootprint;
    color += vec3(0.10, 0.29, 0.34) * surfaceIllumination * 0.34;
    color += uSunRadiance * sunGlint * (0.20 + sunFootprint * 0.42);
    color += uSunRadiance * waveFacet * sunFootprint * (1.0 - fresnel) * 0.045;
    float alpha = (
      0.42 + fresnel * 0.22
      + totalInternalReflection * 0.18
      + refractedSun * 0.12
      + sunGlint * 0.10
    ) * mix(0.72, 1.0, edgeFade) * horizonFade;
    gl_FragColor = vec4(color, alpha);
  }
`

export const OPTICAL_CAUSTICS_VERTEX_SHADER = /* glsl */ `
  varying vec3 vWorldPosition;
  varying float vViewDepth;

  void main() {
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    vWorldPosition = worldPosition.xyz;
    vec4 viewPosition = viewMatrix * worldPosition;
    vViewDepth = -viewPosition.z;
    gl_Position = projectionMatrix * viewPosition;
  }
`

export const OPTICAL_CAUSTICS_FRAGMENT_SHADER = /* glsl */ `
  uniform float time;
  uniform vec3 fogColor;
  uniform float fogDensity;
  uniform vec3 uSunDirection;
  uniform vec3 uSunSurfaceAnchor;
  uniform vec3 uSunRadiance;
  uniform vec3 uStageCenter;
  uniform float uSurfaceY;
  uniform float uIntensity;
  varying vec3 vWorldPosition;
  varying float vViewDepth;

  ${CAUSTIC_FIELD_GLSL}

  void main() {
    float waterDepth = max(uSurfaceY - vWorldPosition.y, 0.0);
    vec2 surfacePoint = vWorldPosition.xz
      + uSunDirection.xz / max(uSunDirection.y, 0.15) * waterDepth;
    float caustic = sampleCausticField(surfacePoint * 3.05, time);
    float stageDistance = length((vWorldPosition.xz - uStageCenter.xz) * vec2(0.74, 0.50));
    float stage = 1.0 - smoothstep(3.0, 15.0, stageDistance);
    float sourceCoherence = 1.0 - smoothstep(7.0, 24.0, length(surfacePoint - uSunSurfaceAnchor.xz));
    float naturalPatches = 0.58 + 0.42 * sin(surfacePoint.x * 0.23 + surfacePoint.y * 0.17 + time * 0.10);
    float fogFactor = 1.0 - exp(-fogDensity * fogDensity * vViewDepth * vViewDepth);
    float causticRadiance = caustic * naturalPatches
      * (0.12 + stage * 0.72 + sourceCoherence * 0.16) * uIntensity;
    float alpha = causticRadiance * mix(0.32, 0.010, fogFactor);
    vec3 color = mix(uSunRadiance * vec3(0.46, 0.54, 0.62), fogColor, fogFactor * 0.46);
    gl_FragColor = vec4(color, alpha);
  }
`

export const OPTICAL_SHAFT_VERTEX_SHADER = /* glsl */ `
  uniform float time;
  uniform float phase;
  varying vec2 vUv;
  varying float vBreath;
  varying float vFacing;

  ${SURFACE_WAVE_GLSL}

  void main() {
    vUv = uv;
    vec3 transformed = position;
    float wave = sampleSurfaceWave(vec2(phase, uv.y * 2.7), time);
    float widthBreath = 1.0 + wave * 0.045;
    transformed.xz *= widthBreath;
    transformed.x += wave * (1.0 - uv.y) * 0.055;
    vBreath = 0.80 + 0.20 * sin(time * 0.27 + phase + wave);
    vec4 viewPosition = modelViewMatrix * vec4(transformed, 1.0);
    vec3 viewNormal = normalize(normalMatrix * normal);
    vFacing = abs(dot(viewNormal, normalize(-viewPosition.xyz)));
    gl_Position = projectionMatrix * viewPosition;
  }
`

export const OPTICAL_SHAFT_FRAGMENT_SHADER = /* glsl */ `
  uniform float opacity;
  uniform vec3 shaftColor;
  uniform float phase;
  varying vec2 vUv;
  varying float vBreath;
  varying float vFacing;

  void main() {
    float topFade = smoothstep(0.01, 0.20, vUv.y);
    float bottomFade = 1.0 - smoothstep(0.78, 1.0, vUv.y);
    float breakup = 0.78 + 0.22 * sin(vUv.y * 19.0 + phase * 3.1);
    float facingFade = smoothstep(0.03, 0.58, vFacing);
    float alpha = opacity * topFade * bottomFade * breakup * vBreath * facingFade;
    gl_FragColor = vec4(shaftColor, alpha);
  }
`
