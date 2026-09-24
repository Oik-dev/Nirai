import * as THREE from 'three'
import { FullScreenQuad, Pass } from 'three/addons/postprocessing/Pass.js'
import type { EnvironmentQuality } from './EnvironmentController'
import { CAUSTIC_FIELD_GLSL, type UnderwaterOpticsState } from './UnderwaterOptics'

const RAY_STEPS: Readonly<Record<EnvironmentQuality, number>> = {
  low: 12,
  medium: 24,
  high: 60
}

const SHAFT_DENSITY: Readonly<Record<EnvironmentQuality, number>> = {
  low: 0.42,
  medium: 0.52,
  high: 0.60
}

const SHAFT_MAX_DENSITY: Readonly<Record<EnvironmentQuality, number>> = {
  low: 0.32,
  medium: 0.40,
  high: 0.46
}

const RESOLUTION_SCALE = 0.5

const FULLSCREEN_VERTEX_SHADER = /* glsl */ `
  varying vec2 vUv;

  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`

export const UNDERWATER_ILLUMINATION_SHADER = {
  vertexShader: FULLSCREEN_VERTEX_SHADER,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDepth;
    uniform float time;
    uniform mat4 uInverseProjectionView;
    uniform vec3 uCameraPosition;
    uniform vec3 uSunDirection;
    uniform vec3 uSunSurfaceAnchor;
    uniform vec3 uSunRadiance;
    uniform vec3 uAbsorption;
    uniform float uSurfaceY;
    uniform vec3 uStageCenter;
    uniform float uNear;
    uniform float uFar;
    uniform int uRaySteps;
    uniform float uShaftDensity;
    uniform float uShaftMaxDensity;
    uniform float uShaftAnisotropy;
    uniform float uShaftDistanceAttenuation;
    uniform float lightShaftStrength;
    varying vec2 vUv;

    const int MAX_RAY_STEPS = 64;

    ${CAUSTIC_FIELD_GLSL}

    vec3 reconstructWorldPosition(vec2 uv, float depth) {
      vec4 clipPosition = vec4(uv * 2.0 - 1.0, depth * 2.0 - 1.0, 1.0);
      vec4 worldPosition = uInverseProjectionView * clipPosition;
      return worldPosition.xyz / max(worldPosition.w, 0.00001);
    }

    float linearizeDepth(float depth) {
      float viewZ = depth * 2.0 - 1.0;
      return (2.0 * uNear * uFar)
        / max(uFar + uNear - viewZ * (uFar - uNear), 0.00001);
    }

    vec3 beerLambert(vec3 extinction, float distanceThroughWater) {
      return exp(-extinction * max(distanceThroughWater, 0.0));
    }

    float henyeyGreenstein(float cosineTheta, float asymmetry) {
      float g2 = asymmetry * asymmetry;
      return (1.0 - g2) / max(
        0.001,
        12.56637 * pow(1.0 + g2 - 2.0 * asymmetry * cosineTheta, 1.5)
      );
    }

    float stageMask(vec3 worldPosition) {
      vec2 delta = (worldPosition.xz - uStageCenter.xz) * vec2(0.72, 0.47);
      return 1.0 - smoothstep(1.8, 7.5, length(delta));
    }

    float interleavedGradientNoise(vec2 pixel) {
      return fract(52.9829189 * fract(
        0.06711056 * pixel.x + 0.00583715 * pixel.y
      ));
    }

    vec2 projectSampleToSurface(vec3 samplePosition) {
      float waterDepth = max(uSurfaceY - samplePosition.y, 0.0);
      float distanceToSurface = waterDepth / max(uSunDirection.y, 0.15);
      return (samplePosition + uSunDirection * distanceToSurface).xz;
    }

    float volumetricScatterDensity(vec3 worldPosition) {
      float waterDepth = max(uSurfaceY - worldPosition.y, 0.0);
      vec2 surfacePoint = projectSampleToSurface(worldPosition);
      float caustic = sampleCausticField(surfacePoint * 0.62, time * 0.58);
      float refractedCore = pow(max(caustic, 0.0), 2.4);
      float refractedVeil = smoothstep(0.22, 0.66, caustic) * 0.20;
      float stage = stageMask(worldPosition);
      float sourceCoherence = 1.0 - smoothstep(
        7.0,
        23.0,
        length(surfacePoint - uSunSurfaceAnchor.xz)
      );
      float depthFade = exp(-waterDepth * 0.075);
      float centralStageBias = mix(0.42, 1.0, stage);
      return (refractedCore + refractedVeil)
        * depthFade * centralStageBias * mix(0.62, 1.0, sourceCoherence);
    }

    vec3 marchRefractedSun(vec3 rayDirection, float rayLength) {
      float stepLength = min(rayLength, 28.0) / float(max(uRaySteps, 1));
      float phase = henyeyGreenstein(
        clamp(dot(rayDirection, normalize(uSunDirection)), -1.0, 1.0),
        uShaftAnisotropy
      );
      float dither = interleavedGradientNoise(gl_FragCoord.xy);
      float illumination = 0.0;

      for (int index = 0; index < MAX_RAY_STEPS; index++) {
        if (index >= uRaySteps) break;
        float travel = (float(index) + 0.39 + dither) * stepLength;
        vec3 samplePosition = uCameraPosition + rayDirection * travel;
        if (samplePosition.y > uSurfaceY) continue;

        float density = volumetricScatterDensity(samplePosition);
        float distanceAttenuation = exp(-travel * uShaftDistanceAttenuation);
        illumination += density * distanceAttenuation * stepLength;
      }

      illumination *= uShaftDensity * phase;
      float saturated = clamp(
        1.0 - exp(-illumination),
        0.0,
        uShaftMaxDensity
      );
      vec3 transmittance = beerLambert(uAbsorption * 0.42, rayLength * 0.5);
      return uSunRadiance * transmittance * saturated;
    }

    void main() {
      float depth = texture2D(tDepth, vUv).x;
      vec3 farWorld = reconstructWorldPosition(vUv, min(depth, 0.99998));
      vec3 viewVector = farWorld - uCameraPosition;
      float reconstructedDistance = length(viewVector);
      vec3 rayDirection = viewVector / max(reconstructedDistance, 0.0001);
      float waterDistance = depth > 0.9999 ? 28.0 : min(reconstructedDistance, 38.0);
      vec3 volumetricLight = marchRefractedSun(rayDirection, waterDistance)
        * lightShaftStrength;
      float linearDepth = linearizeDepth(depth);
      gl_FragColor = vec4(volumetricLight, linearDepth);
    }
  `
}

export const UNDERWATER_BILATERAL_SHADER = {
  vertexShader: FULLSCREEN_VERTEX_SHADER,
  fragmentShader: /* glsl */ `
    uniform sampler2D tInput;
    uniform vec2 uResolution;
    uniform float uSigma;
    varying vec2 vUv;

    float niraiLuminance(vec3 color) {
      return dot(color, vec3(0.2126, 0.7152, 0.0722));
    }

    void main() {
      vec4 centerSample = texture2D(tInput, vUv);
      float centerLuminance = niraiLuminance(centerSample.rgb);
      vec3 weightedIllumination = vec3(0.0);
      float totalWeight = 0.0;
      vec2 texelSize = 1.0 / uResolution;

      for (int y = -2; y <= 2; y++) {
        for (int x = -2; x <= 2; x++) {
          vec2 offsetInTexels = vec2(float(x), float(y));
          vec2 sampleUv = clamp(
            vUv + offsetInTexels * texelSize,
            texelSize * 0.5,
            1.0 - texelSize * 0.5
          );
          vec3 sampleIllumination = texture2D(tInput, sampleUv).rgb;
          float illuminationDifference = niraiLuminance(sampleIllumination) - centerLuminance;
          float spatialWeight = exp(
            -0.5 * dot(offsetInTexels, offsetInTexels) / (1.15 * 1.15)
          );
          float rangeWeight = exp(
            -0.5 * illuminationDifference * illuminationDifference
            / max(uSigma * uSigma, 0.000001)
          );
          float weight = spatialWeight * rangeWeight;
          weightedIllumination += sampleIllumination * weight;
          totalWeight += weight;
        }
      }

      gl_FragColor = vec4(
        weightedIllumination / max(totalWeight, 0.00001),
        centerSample.a
      );
    }
  `
}

export const UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER = {
  vertexShader: FULLSCREEN_VERTEX_SHADER,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform sampler2D tGodrays;
    uniform vec2 uGodraysResolution;
    varying vec2 vUv;

    void main() {
      vec4 source = texture2D(tDiffuse, vUv);
      float fullResolutionDepth = source.a;
      vec2 texelSize = 1.0 / uGodraysResolution;
      vec2 lowResolutionPosition = vUv * uGodraysResolution - 0.5;
      vec2 baseTexel = floor(lowResolutionPosition);
      vec2 fractionalPosition = lowResolutionPosition - baseTexel;
      vec3 weightedIllumination = vec3(0.0);
      float totalWeight = 0.0;

      for (int y = -JBU_EXTENT; y <= 1 + JBU_EXTENT; y++) {
        for (int x = -JBU_EXTENT; x <= 1 + JBU_EXTENT; x++) {
          vec2 sampleUv = (baseTexel + vec2(float(x), float(y)) + 0.5) * texelSize;
          vec4 godraySample = texture2D(
            tGodrays,
            clamp(sampleUv, texelSize * 0.5, 1.0 - texelSize * 0.5)
          );
          vec2 texelDelta = vec2(float(x), float(y)) - fractionalPosition;
          float spatialWeight = exp(
            -dot(texelDelta, texelDelta)
            / (2.0 * JBU_SPATIAL_SIGMA * JBU_SPATIAL_SIGMA)
          );
          float relativeDepthDifference = (godraySample.a - fullResolutionDepth)
            / max(fullResolutionDepth, 0.001);
          float depthWeight = exp(
            -0.5 * relativeDepthDifference * relativeDepthDifference
            / (JBU_DEPTH_SIGMA * JBU_DEPTH_SIGMA)
          );
          float weight = spatialWeight * depthWeight;
          weightedIllumination += godraySample.rgb * weight;
          totalWeight += weight;
        }
      }

      vec3 volumetricLight = weightedIllumination / max(totalWeight, 0.00001);
      gl_FragColor = vec4(source.rgb + volumetricLight, 1.0);
    }
  `
}

export class UnderwaterIlluminationPass extends Pass {
  readonly lowResolution = new THREE.Vector2(1, 1)
  sceneDepthTexture: THREE.DepthTexture | null = null

  private readonly illuminationTarget: THREE.WebGLRenderTarget
  private readonly filteredTarget: THREE.WebGLRenderTarget
  private readonly illuminationMaterial: THREE.ShaderMaterial
  private readonly bilateralMaterial: THREE.ShaderMaterial
  private readonly illuminationQuad: FullScreenQuad
  private readonly bilateralQuad: FullScreenQuad

  constructor(
    camera: THREE.Camera,
    quality: EnvironmentQuality,
    optics: UnderwaterOpticsState
  ) {
    super()
    this.needsSwap = false
    const perspectiveCamera = camera as THREE.PerspectiveCamera
    const targetOptions: THREE.RenderTargetOptions = {
      type: THREE.HalfFloatType,
      format: THREE.RGBAFormat,
      minFilter: THREE.NearestFilter,
      magFilter: THREE.NearestFilter,
      depthBuffer: false,
      stencilBuffer: false,
      generateMipmaps: false
    }
    this.illuminationTarget = new THREE.WebGLRenderTarget(1, 1, targetOptions)
    this.illuminationTarget.texture.name = 'Nirai.UnderwaterIllumination'
    this.filteredTarget = new THREE.WebGLRenderTarget(1, 1, targetOptions)
    this.filteredTarget.texture.name = 'Nirai.UnderwaterIlluminationFiltered'

    this.illuminationMaterial = new THREE.ShaderMaterial({
      name: 'Nirai.UnderwaterIllumination',
      uniforms: {
        tDepth: { value: null },
        time: { value: 0 },
        uInverseProjectionView: { value: new THREE.Matrix4() },
        uCameraPosition: { value: new THREE.Vector3() },
        uSunDirection: optics.sunDirection,
        uSunSurfaceAnchor: optics.sunSurfaceAnchor,
        uSunRadiance: optics.sunRadiance,
        uAbsorption: optics.absorption,
        uSurfaceY: optics.surfaceY,
        uStageCenter: optics.stageCenter,
        uNear: { value: perspectiveCamera.near ?? 0.1 },
        uFar: { value: perspectiveCamera.far ?? 100 },
        uRaySteps: { value: RAY_STEPS[quality] },
        uShaftDensity: { value: SHAFT_DENSITY[quality] },
        uShaftMaxDensity: { value: SHAFT_MAX_DENSITY[quality] },
        uShaftAnisotropy: { value: 0.72 },
        uShaftDistanceAttenuation: { value: 0.045 },
        lightShaftStrength: { value: 1 }
      },
      vertexShader: UNDERWATER_ILLUMINATION_SHADER.vertexShader,
      fragmentShader: UNDERWATER_ILLUMINATION_SHADER.fragmentShader,
      depthTest: false,
      depthWrite: false
    })

    this.bilateralMaterial = new THREE.ShaderMaterial({
      name: 'Nirai.UnderwaterIlluminationBilateral',
      uniforms: {
        tInput: { value: this.illuminationTarget.texture },
        uResolution: { value: this.lowResolution },
        uSigma: { value: 0.10 }
      },
      vertexShader: UNDERWATER_BILATERAL_SHADER.vertexShader,
      fragmentShader: UNDERWATER_BILATERAL_SHADER.fragmentShader,
      depthTest: false,
      depthWrite: false
    })

    this.illuminationQuad = new FullScreenQuad(this.illuminationMaterial)
    this.bilateralQuad = new FullScreenQuad(this.bilateralMaterial)
  }

  get outputTexture(): THREE.Texture {
    return this.filteredTarget.texture
  }

  get effectStrength(): number {
    return this.illuminationMaterial.uniforms.lightShaftStrength.value as number
  }

  get raySteps(): number {
    return this.illuminationMaterial.uniforms.uRaySteps.value as number
  }

  get sunSurfaceAnchor(): THREE.Vector3 {
    return this.illuminationMaterial.uniforms.uSunSurfaceAnchor.value as THREE.Vector3
  }

  get sunRadiance(): THREE.Color {
    return this.illuminationMaterial.uniforms.uSunRadiance.value as THREE.Color
  }

  setEffectEnabled(enabled: boolean): void {
    this.illuminationMaterial.uniforms.lightShaftStrength.value = enabled ? 1 : 0
  }

  update(delta: number, inverseProjectionView: THREE.Matrix4, camera: THREE.Camera): void {
    const perspectiveCamera = camera as THREE.PerspectiveCamera
    this.illuminationMaterial.uniforms.time.value += Math.max(0, delta)
    this.illuminationMaterial.uniforms.uInverseProjectionView.value.copy(inverseProjectionView)
    this.illuminationMaterial.uniforms.uCameraPosition.value.setFromMatrixPosition(camera.matrixWorld)
    this.illuminationMaterial.uniforms.uNear.value = perspectiveCamera.near ?? 0.1
    this.illuminationMaterial.uniforms.uFar.value = perspectiveCamera.far ?? 100
  }

  override render(
    renderer: THREE.WebGLRenderer,
    _writeBuffer: THREE.WebGLRenderTarget,
    readBuffer: THREE.WebGLRenderTarget
  ): void {
    this.sceneDepthTexture = readBuffer.depthTexture
    this.illuminationMaterial.uniforms.tDepth.value = this.sceneDepthTexture

    renderer.setRenderTarget(this.illuminationTarget)
    renderer.clear()
    this.illuminationQuad.render(renderer)

    renderer.setRenderTarget(this.filteredTarget)
    renderer.clear()
    this.bilateralQuad.render(renderer)

  }

  override setSize(width: number, height: number): void {
    const lowWidth = Math.max(1, Math.ceil(width * RESOLUTION_SCALE))
    const lowHeight = Math.max(1, Math.ceil(height * RESOLUTION_SCALE))
    this.lowResolution.set(lowWidth, lowHeight)
    this.illuminationTarget.setSize(lowWidth, lowHeight)
    this.filteredTarget.setSize(lowWidth, lowHeight)
  }

  override dispose(): void {
    this.illuminationTarget.dispose()
    this.filteredTarget.dispose()
    this.illuminationMaterial.dispose()
    this.bilateralMaterial.dispose()
    this.illuminationQuad.dispose()
    this.bilateralQuad.dispose()
  }
}

export class UnderwaterDepthAwareCompositePass extends Pass {
  private readonly material: THREE.ShaderMaterial
  private readonly quad: FullScreenQuad

  constructor(
    private readonly illuminationPass: UnderwaterIlluminationPass,
    quality: EnvironmentQuality
  ) {
    super()
    const extent = quality === 'low' ? 0 : 1
    this.material = new THREE.ShaderMaterial({
      name: 'Nirai.UnderwaterDepthAwareComposite',
      uniforms: {
        tDiffuse: { value: null },
        tGodrays: { value: illuminationPass.outputTexture },
        uGodraysResolution: { value: illuminationPass.lowResolution }
      },
      defines: {
        JBU_EXTENT: String(extent),
        JBU_SPATIAL_SIGMA: extent === 1 ? '1.0' : '0.5',
        JBU_DEPTH_SIGMA: '0.02'
      },
      vertexShader: UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER.vertexShader,
      fragmentShader: UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER.fragmentShader,
      depthTest: false,
      depthWrite: false
    })
    this.quad = new FullScreenQuad(this.material)
  }

  override render(
    renderer: THREE.WebGLRenderer,
    writeBuffer: THREE.WebGLRenderTarget,
    readBuffer: THREE.WebGLRenderTarget
  ): void {
    this.material.uniforms.tDiffuse.value = readBuffer.texture
    renderer.setRenderTarget(this.renderToScreen ? null : writeBuffer)
    if (this.clear) renderer.clear()
    this.quad.render(renderer)
  }

  override dispose(): void {
    this.material.dispose()
    this.quad.dispose()
  }
}
