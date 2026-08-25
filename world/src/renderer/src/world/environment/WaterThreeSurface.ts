import * as THREE from 'three'
import type { UnderwaterOpticsState } from './UnderwaterOptics'

/*
 * Underwater-only adapter of WaterThreeJS Ocean.js/common.js.
 * Source: https://github.com/achrefelouafi/WaterThreeJS
 * License: MIT, copyright (c) 2026 mohamedachrefelouafi.
 *
 * The wave spectrum, analytic normal, detail-normal flow, atmosphere and
 * Snell-window shading below are kept from the upstream implementation. The
 * above-water, SSR, foam and buoyancy branches are intentionally omitted
 * because Nirai M0 never places the camera above the surface.
 */

const NOISE = /* glsl */ `
  float hash21(vec2 p){
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
  }

  vec3 noised(vec2 x){
    vec2 p = floor(x);
    vec2 f = fract(x);
    vec2 u  = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    vec2 du = 30.0 * f * f * (f * (f - 2.0) + 1.0);
    float a = hash21(p + vec2(0.0, 0.0));
    float b = hash21(p + vec2(1.0, 0.0));
    float c = hash21(p + vec2(0.0, 1.0));
    float d = hash21(p + vec2(1.0, 1.0));
    float k1 = b - a;
    float k2 = c - a;
    float k3 = a - b - c + d;
    float n  = a + k1 * u.x + k2 * u.y + k3 * u.x * u.y;
    vec2  g  = du * vec2(k1 + k3 * u.y, k2 + k3 * u.x);
    return vec3(n, g);
  }

  const mat2 FBM_M = mat2(1.6, 1.2, -1.2, 1.6);

  float fbm(vec2 p, int oct){
    float amp = 0.5, sum = 0.0;
    for (int i = 0; i < 8; i++){
      if (i >= oct) break;
      sum += amp * noised(p).x;
      p = FBM_M * p;
      amp *= 0.5;
    }
    return sum;
  }
`

const OCEAN_GERSTNER = /* glsl */ `
  #define MAX_WAVES 40

  uniform float uTime;
  uniform vec2  uWindDir;
  uniform float uWaveCount;
  uniform float uBaseFreq;
  uniform float uAmplitude;
  uniform float uChoppy;
  uniform float uDirSpread;
  uniform float uFreqMul;
  uniform float uAmpMul;
  uniform float uSpeed;

  struct WaveSample {
    vec3  displacement;
    vec3  normal;
    float fold;
    float height;
  };

  WaveSample sampleOcean(vec2 pos){
    vec3  disp = vec3(0.0);
    vec3  nrm  = vec3(0.0, 1.0, 0.0);
    float jxx = 1.0, jzz = 1.0, jxz = 0.0;
    float baseAngle = atan(uWindDir.y, uWindDir.x);
    float freq  = uBaseFreq;
    float amp   = uAmplitude;
    int   count = int(uWaveCount);

    for (int i = 0; i < MAX_WAVES; i++){
      if (i >= count) break;
      float fi = float(i);
      float r0 = hash21(vec2(fi, 1.7));
      float r1 = hash21(vec2(fi, 9.1));
      float angle = baseAngle + (r0 * 2.0 - 1.0) * uDirSpread;
      vec2  d = vec2(cos(angle), sin(angle));
      float w = freq;
      float A = amp;
      float phase = sqrt(9.81 * w) * uSpeed;
      float Q = uChoppy / max(w * A * uWaveCount, 1e-3);
      float arg = w * dot(d, pos) + uTime * phase + r1 * 6.2831853;
      float s = sin(arg);
      float c = cos(arg);
      float WA = w * A;

      disp.x += Q * A * d.x * c;
      disp.z += Q * A * d.y * c;
      disp.y += A * s;
      nrm.x -= d.x * WA * c;
      nrm.z -= d.y * WA * c;
      nrm.y -= Q * WA * s;
      jxx -= Q * d.x * d.x * WA * s;
      jzz -= Q * d.y * d.y * WA * s;
      jxz -= Q * d.x * d.y * WA * s;
      freq *= uFreqMul;
      amp  *= uAmpMul;
    }

    WaveSample o;
    o.displacement = disp;
    o.normal = normalize(nrm);
    o.height = disp.y;
    o.fold = jxx * jzz - jxz * jxz;
    return o;
  }
`

const DETAIL_NORMAL = /* glsl */ `
  vec3 detailNormal(vec2 p, float t, float strength){
    vec2 g = vec2(0.0);
    float amp = 1.0;
    mat2 m = mat2(1.7, 1.1, -1.1, 1.7);
    vec2 flow = uWindDir * t * 0.6;
    for (int i = 0; i < 6; i++){
      vec3 n = noised(p + flow);
      g += amp * n.yz;
      p = m * p;
      flow = -flow * 0.85;
      amp *= 0.55;
    }
    return normalize(vec3(-g.x, 1.0 / max(strength, 1e-3), -g.y));
  }
`

const ATMOSPHERE = /* glsl */ `
  vec3 atmosphere(vec3 dir, vec3 sunDir){
    dir = normalize(dir);
    float up = clamp(dir.y, -1.0, 1.0);
    float sunAmt = max(dot(dir, sunDir), 0.0);
    float sunElev = clamp(sunDir.y, 0.0, 1.0);
    vec3 zenith = mix(vec3(0.06, 0.19, 0.52), vec3(0.09, 0.28, 0.66), sunElev);
    vec3 horizon = mix(vec3(0.44, 0.56, 0.75), vec3(0.60, 0.74, 0.90), sunElev);
    float h = pow(clamp(1.0 - up, 0.0, 1.0), 2.6);
    vec3 col = mix(zenith, horizon, h);
    vec3 warm = vec3(1.0, 0.72, 0.45);
    col = mix(col, warm, h * pow(sunAmt, 2.5) * (0.75 - 0.5 * sunElev));
    col = mix(col, vec3(0.05, 0.10, 0.15), 1.0 - smoothstep(-0.22, 0.0, up));
    vec3 sunTint = mix(vec3(1.00, 0.52, 0.24), vec3(1.00, 0.96, 0.88), sunElev);
    float glow = pow(sunAmt, 8.0) * 0.35 + pow(sunAmt, 90.0) * 0.6;
    col += sunTint * glow * (0.6 + 0.4 * h);
    float disk = smoothstep(0.99955, 0.99978, sunAmt);
    col += sunTint * disk * 14.0;
    return max(col, vec3(0.0));
  }
`

export interface WaterThreeSurface {
  readonly mesh: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>
  readonly material: THREE.ShaderMaterial
  setCalmness(value: number): void
  resize(width: number, height: number): void
}

export function createWaterThreeSurface(optics: UnderwaterOpticsState): WaterThreeSurface {
  const windDirection = new THREE.Vector2(1, 0.55).normalize()
  const uniforms = {
    uTime: { value: 0 },
    uSunDir: optics.sunDirection,
    uSurfaceY: optics.surfaceY,
    uWindDir: { value: windDirection },
    uWaveCount: { value: 15 },
    uBaseFreq: { value: (2 * Math.PI) / 14 },
    uAmplitude: { value: 0.075 },
    uChoppy: { value: 0 },
    uDirSpread: { value: 1.08 },
    uFreqMul: { value: 1.22 },
    uAmpMul: { value: 0.73 },
    uSpeed: { value: 0.28 },
    uDetailScale: { value: 1.55 },
    uDetailStrength: { value: 0.26 },
    uShallowColor: { value: new THREE.Color(0.08, 0.46, 0.74) },
    uResolution: { value: new THREE.Vector2(1280, 720) }
  }

  const material = new THREE.ShaderMaterial({
    side: THREE.DoubleSide,
    transparent: true,
    depthWrite: false,
    toneMapped: false,
    uniforms,
    vertexShader: /* glsl */ `
      precision highp float;
      ${NOISE}
      ${OCEAN_GERSTNER}
      uniform float uSurfaceY;
      varying vec3 vWorldPos;
      varying vec3 vNormal;

      void main(){
        vec3 worldPos = (modelMatrix * vec4(position, 1.0)).xyz;
        worldPos.y = uSurfaceY;
        WaveSample w = sampleOcean(worldPos.xz);
        vec3 displaced = worldPos + w.displacement;
        vWorldPos = displaced;
        vNormal = w.normal;
        gl_Position = projectionMatrix * viewMatrix * vec4(displaced, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;
      uniform float uTime;
      uniform vec3 uSunDir;
      uniform vec2 uWindDir;
      uniform float uDetailScale;
      uniform float uDetailStrength;
      uniform vec3 uShallowColor;
      ${NOISE}
      ${DETAIL_NORMAL}
      ${ATMOSPHERE}
      varying vec3 vWorldPos;
      varying vec3 vNormal;

      float fresnelF(float c, float f0){
        return f0 + (1.0 - f0) * pow(clamp(1.0 - c, 0.0, 1.0), 5.0);
      }

      void main(){
        vec3 sunDir = normalize(uSunDir);
        vec3 V = normalize(cameraPosition - vWorldPos);
        float sunElev = clamp(sunDir.y, 0.0, 1.0);
        float dist = length(cameraPosition - vWorldPos);
        float calmTime = uTime * 0.26;
        vec3 N = normalize(vNormal);
        float detFade = exp(-dist * 0.012);
        vec3 dN1 = detailNormal(vWorldPos.xz * uDetailScale, calmTime, 1.0);
        vec3 dN2 = detailNormal(vWorldPos.xz * uDetailScale * 3.7 + 11.0, calmTime * 1.35, 1.0);
        vec3 dN3 = detailNormal(vWorldPos.xz * uDetailScale * 11.0 + 31.0, calmTime * 1.9, 1.0);
        vec2 dsum = dN1.xz * uDetailStrength
          + dN2.xz * uDetailStrength * 0.5 * mix(0.35, 1.0, detFade)
          + dN3.xz * uDetailStrength * 0.28 * detFade;
        N = normalize(vec3(N.x + dsum.x, N.y, N.z + dsum.y));
        vec3 Ns = N.y >= 0.0 ? N : -N;

        vec3 I = normalize(vWorldPos - cameraPosition);
        vec3 refr = refract(I, -Ns, 1.333);
        float ci = abs(dot(Ns, I));
        float fres = fresnelF(ci, 0.02);
        vec3 waterGlow = mix(uShallowColor, vec3(0.72, 0.92, 0.96), 0.35)
          * (0.46 + 0.68 * sunElev);
        vec3 color;
        if (dot(refr, refr) < 1e-4){
          color = waterGlow;
        } else {
          vec3 sky = atmosphere(refr, sunDir);
          float lum = max(sky.r, max(sky.g, sky.b));
          sky = mix(sky, vec3(0.80, 0.9, 1.0) * lum, 0.4);
          sky *= 0.92;
          color = mix(waterGlow, sky, 1.0 - fres);
        }
        float shimmer = fbm(vWorldPos.xz * 1.18 + uWindDir * calmTime * 0.5, 4);
        shimmer = smoothstep(0.52, 0.92, shimmer);
        mat2 rippleRotation = mat2(0.78, -0.63, 0.63, 0.78);
        float rippleA = fbm(
          vWorldPos.xz * 1.65 + uWindDir * calmTime * 0.31,
          5
        );
        float rippleB = fbm(
          rippleRotation * vWorldPos.xz * 2.15 - uWindDir.yx * calmTime * 0.23 + 7.4,
          5
        );
        float movingRipple = smoothstep(0.08, 0.25, abs(rippleA - rippleB));
        float slopeHighlight = smoothstep(0.08, 0.42, length(N.xz));
        color *= 0.86 + rippleA * 0.18;
        color += vec3(0.30, 0.58, 0.68)
          * movingRipple * (0.10 + slopeHighlight * 0.16) * (1.0 - fres);
        float microGlint = pow(max(dot(reflect(-sunDir, N), V), 0.0), 96.0);
        color += vec3(0.92, 0.99, 1.0) * microGlint * 0.78;
        color += vec3(0.9, 0.98, 1.0) * shimmer * (1.0 - fres) * 0.35;
        color += vec3(0.85, 0.95, 1.0) * (1.0 - fres) * 0.06;
        float surfaceVisibility = 1.0 - smoothstep(5.5, 16.0, dist);
        color = mix(color, uShallowColor * 0.86, smoothstep(7.0, 16.0, dist) * 0.34);
        gl_FragColor = vec4(color, surfaceVisibility);
      }
    `
  })

  const geometry = new THREE.PlaneGeometry(180, 180, 320, 320)
  geometry.rotateX(-Math.PI / 2)
  const mesh = new THREE.Mesh(geometry, material)
  mesh.name = 'Environment:waterSurface'
  mesh.position.z = -4.2
  mesh.frustumCulled = false
  mesh.renderOrder = 5
  mesh.userData.renderMode = 'water-three-js-snell-window'

  return {
    mesh,
    material,
    setCalmness(value) {
      const calmness = THREE.MathUtils.clamp(value, 0, 5)
      const amplitudeScale = Math.exp((0.7 - calmness) * 1.8)
      const detailScale = Math.exp((0.7 - calmness) * 0.92)
      uniforms.uAmplitude.value = 0.075 * amplitudeScale
      uniforms.uDetailStrength.value = 0.26 * detailScale
    },
    resize(width, height) {
      uniforms.uResolution.value.set(Math.max(1, width), Math.max(1, height))
    }
  }
}
