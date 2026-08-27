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
