import { SURFACE_WAVE_GLSL } from './UnderwaterOptics'

export const OPTICAL_BACKGROUND_VERTEX_SHADER = /* glsl */ `
  varying vec2 vBackdropUv;

  void main() {
    vBackdropUv = uv;
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`

export const OPTICAL_BACKGROUND_FRAGMENT_SHADER = /* glsl */ `
  uniform sampler2D uBackdropMap;
  uniform float time;
  uniform float waterSurfaceStrength;
  uniform float lightShaftStrength;
  uniform vec3 uDeepColor;
  uniform vec3 uFogColor;
  uniform float uHorizonHaze;
  varying vec2 vBackdropUv;

  void main() {
    // The backdrop is a normal fixed-world skydome. Camera motion comes from
    // viewing that geometry, not from projective UV compensation.
    vec2 uv = vBackdropUv;
    float surfaceMask = smoothstep(0.58, 0.82, uv.y) * waterSurfaceStrength;
    vec2 animatedUv = uv;
    animatedUv.x += (
      sin(uv.y * 23.0 + time * 0.085)
      + sin(uv.y * 41.0 - time * 0.052) * 0.42
    ) * 0.00115 * surfaceMask;
    animatedUv.y += (
      sin(uv.x * 19.0 - time * 0.061)
      + sin(uv.x * 37.0 + time * 0.043) * 0.35
    ) * 0.00072 * surfaceMask;

    vec3 color = texture2D(uBackdropMap, animatedUv).rgb;

    // The source image may still contain beach sand in its lower region, but
    // that region is no longer allowed to represent the physical floor. Pull
    // water tone from the image's mid/upper band and dissolve the lower image
    // into underwater haze instead of mirroring, clamping or aligning it.
    vec2 waterSourceUv = vec2(animatedUv.x, 0.61 + animatedUv.y * 0.08);
    vec3 waterSource = texture2D(uBackdropMap, waterSourceUv).rgb;
    float horizonHaze = clamp(uHorizonHaze, 0.0, 3.0);
    vec3 waterHaze = mix(waterSource, uFogColor, clamp(0.62 * horizonHaze, 0.0, 1.0));
    float lowerSandRemoval = 1.0 - smoothstep(0.42, 0.66, uv.y);
    float lowerBlend = clamp(0.34 + 0.64 * horizonHaze, 0.0, 1.0);
    color = mix(color, waterHaze, lowerSandRemoval * lowerBlend);

    float slowBreath = 0.992 + sin(time * 0.055) * 0.008;
    color *= mix(1.0, slowBreath, surfaceMask);
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
    vec2 broadSlope = sampleSurfaceSlope(worldXZ * 0.72, time) * 0.46;
    vec2 crossedSlope = sampleSurfaceSlope(
      mat2(0.78, -0.63, 0.63, 0.78) * worldXZ * 1.16 + vec2(4.2, -2.7),
      time * 0.73 + 5.1
    ) * 0.20;
    vec2 microSlope = sampleSurfaceSlope(worldXZ.yx * 1.90 + 3.2, time * 1.17)
      * 0.18 * detailFade;
    vec2 slope = broadSlope + crossedSlope + microSlope;
    vec3 normal = normalize(vec3(-slope.x * 0.075, 1.0, -slope.y * 0.075));
    vec3 rayToSurface = normalize(vWorldPosition - cameraPosition);
    vec3 viewDirection = -rayToSurface;
    float viewCosine = clamp(abs(dot(normal, rayToSurface)), 0.0, 1.0);
    float fresnel = 0.04 + 0.96 * pow(1.0 - viewCosine, 5.0);

    vec3 refractedDirection = refract(rayToSurface, -normal, 1.333);
    float totalInternalReflection = 1.0 - step(0.0001, dot(refractedDirection, refractedDirection));
    vec3 skyDirection = normalize(refractedDirection + vec3(0.0001));
    float skyHeight = smoothstep(-0.10, 0.82, skyDirection.y);
    float skySunAlignment = max(dot(skyDirection, normalize(uSunDirection)), 0.0);
    float refractedSun = pow(skySunAlignment, 96.0);
    vec3 refractedSky = mix(vec3(0.065, 0.47, 0.73), vec3(0.68, 0.90, 0.97), skyHeight);
    refractedSky += uSunRadiance * refractedSun * 0.62;

    vec3 reflectedWater = mix(vec3(0.014, 0.23, 0.39), vec3(0.065, 0.40, 0.57), fresnel);
    vec3 color = mix(refractedSky, reflectedWater, fresnel);
    color = mix(color, reflectedWater, totalInternalReflection);

    vec2 facetSlope = sampleSurfaceSlope(worldXZ * 2.35 + vec2(2.7, -4.1), time * 0.83);
    vec2 facetAhead = sampleSurfaceSlope(
      worldXZ * 2.35 + vec2(2.82, -4.03),
      time * 0.83 + 0.018
    );
    float slopeMagnitude = length(slope);
    float focusing = length(facetAhead - facetSlope);
    float broadWaveShade = clamp(
      sampleSurfaceWave(worldXZ * 0.31, time * 0.46) * 0.42 + 0.58,
      0.0,
      1.0
    );
    float waveFacet = clamp(
      smoothstep(0.07, 0.42, slopeMagnitude) * 0.58
      + smoothstep(0.035, 0.19, focusing) * 0.42,
      0.0,
      1.0
    ) * detailFade;
    color *= 0.84 + broadWaveShade * 0.16;

    float sunGlint = pow(
      max(dot(reflect(-normalize(uSunDirection), normal), viewDirection), 0.0),
      72.0
    );
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
    color += uSunRadiance * waveFacet * (0.07 + sunFootprint * 0.19) * (1.0 - fresnel);
    float alpha = (
      0.10 + fresnel * 0.14
      + waveFacet * 0.44
      + totalInternalReflection * 0.10
      + refractedSun * 0.08
      + sunGlint * 0.14
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
  uniform sampler2D uCausticsMap;
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

  void main() {
    vec2 surfacePoint = vWorldPosition.xz;
    vec2 flowA = surfacePoint * 0.23 + vec2(time * 0.025, -time * 0.017);
    vec2 flowB = mat2(0.81, -0.59, 0.59, 0.81) * surfacePoint * 0.17
      + vec2(-time * 0.015, time * 0.021) + vec2(0.31, 0.17);
    float primaryCaustic = texture2D(uCausticsMap, flowA).r;
    float detailCaustic = texture2D(uCausticsMap, flowB).r;
    float combinedCaustic = primaryCaustic * 0.68 + detailCaustic * 0.46;
    float causticVeil = smoothstep(0.28, 0.47, combinedCaustic);
    float causticCore = smoothstep(0.48, 0.72, combinedCaustic);
    float caustic = causticVeil * 0.30 + causticCore * 1.34;
    float stageDistance = length((vWorldPosition.xz - uStageCenter.xz) * vec2(0.74, 0.50));
    float stage = 1.0 - smoothstep(4.0, 22.0, stageDistance);
    float broadPatch = 0.80 + 0.20 * sin(surfacePoint.x * 0.12 + surfacePoint.y * 0.09 + time * 0.11);
    float fogFactor = 1.0 - exp(-fogDensity * fogDensity * vViewDepth * vViewDepth);
    float causticRadiance = caustic * broadPatch * (0.24 + stage * 0.76) * uIntensity;
    float alpha = causticRadiance * mix(0.82, 0.095, fogFactor);
    vec3 color = mix(uSunRadiance * vec3(0.82, 0.88, 0.84), fogColor, fogFactor * 0.12);
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
