import * as THREE from 'three'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js'
import type { EnvironmentEffectName, EnvironmentQuality } from './EnvironmentController'
import { CAUSTIC_FIELD_GLSL, type UnderwaterOpticsState } from './UnderwaterOptics'
import {
  UnderwaterDepthAwareCompositePass,
  UnderwaterIlluminationPass
} from './UnderwaterVolumetricPasses'

export {
  UNDERWATER_BILATERAL_SHADER,
  UNDERWATER_DEPTH_AWARE_COMPOSITOR_SHADER,
  UNDERWATER_ILLUMINATION_SHADER,
  UnderwaterDepthAwareCompositePass,
  UnderwaterIlluminationPass
} from './UnderwaterVolumetricPasses'

export const UNDERWATER_SHADER = {
  uniforms: {
    tDiffuse: { value: null as THREE.Texture | null },
    tDepth: { value: null as THREE.DepthTexture | null },
    time: { value: 0 },
    uInverseProjectionView: { value: new THREE.Matrix4() },
    uCameraPosition: { value: new THREE.Vector3() },
    uSunDirection: { value: new THREE.Vector3(0, 1, 0) },
    uSunRadiance: { value: new THREE.Color(1.18, 1.48, 1.62) },
    uSurfaceY: { value: 4.05 },
    uStageCenter: { value: new THREE.Vector3() },
    uDeepColor: { value: new THREE.Color(0x062f57) },
    uAbsorption: { value: new THREE.Color(0.075, 0.025, 0.012) },
    uScatteringColor: { value: new THREE.Color(0.012, 0.19, 0.42) },
    uScatteringStrength: { value: 0.46 },
    uNear: { value: 0.1 },
    uFar: { value: 100 },
    waterSurfaceStrength: { value: 1 },
    causticsStrength: { value: 1 }
  },
  vertexShader: /* glsl */ `
    varying vec2 vUv;

    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform sampler2D tDepth;
    uniform float time;
    uniform mat4 uInverseProjectionView;
    uniform vec3 uCameraPosition;
    uniform vec3 uSunDirection;
    uniform vec3 uSunRadiance;
    uniform float uSurfaceY;
    uniform vec3 uStageCenter;
    uniform vec3 uDeepColor;
    uniform vec3 uAbsorption;
    uniform vec3 uScatteringColor;
    uniform float uScatteringStrength;
    uniform float uNear;
    uniform float uFar;
    uniform float waterSurfaceStrength;
    uniform float causticsStrength;
    varying vec2 vUv;

    ${CAUSTIC_FIELD_GLSL}

    vec3 reconstructWorldPosition(vec2 uv, float depth) {
      vec4 clipPosition = vec4(uv * 2.0 - 1.0, depth * 2.0 - 1.0, 1.0);
      vec4 worldPosition = uInverseProjectionView * clipPosition;
      return worldPosition.xyz / max(worldPosition.w, 0.00001);
    }

    vec3 beerLambert(vec3 extinction, float distanceThroughWater) {
      return exp(-extinction * max(distanceThroughWater, 0.0));
    }

    float linearizeDepth(float depth) {
      float viewZ = depth * 2.0 - 1.0;
      return (2.0 * uNear * uFar)
        / max(uFar + uNear - viewZ * (uFar - uNear), 0.00001);
    }

    float stageMask(vec3 worldPosition) {
      vec2 delta = (worldPosition.xz - uStageCenter.xz) * vec2(0.72, 0.47);
      return 1.0 - smoothstep(1.8, 7.5, length(delta));
    }

    void main() {
      float depth = texture2D(tDepth, vUv).x;
      vec3 farWorld = reconstructWorldPosition(vUv, min(depth, 0.99998));
      vec3 viewVector = farWorld - uCameraPosition;
      float reconstructedDistance = length(viewVector);
      vec3 rayDirection = viewVector / max(reconstructedDistance, 0.0001);
      // Keep the water path continuous across the geometry/background depth boundary.
      // The old 38m-for-geometry / 28m-for-background split created a visible
      // horizontal seam exactly where the seabed depth buffer ended.
      float waterDistance = min(reconstructedDistance, 28.0);

      float waveDistortion = sampleSurfaceWave(
        rayDirection.xz * 3.2 + vec2(rayDirection.y * 1.7),
        time
      );
      float distanceFactor = smoothstep(2.0, 24.0, waterDistance);
      vec2 refraction = vec2(
        waveDistortion,
        sampleSurfaceWave(rayDirection.zx * 2.7 + 4.3, time + 1.7)
      ) * 0.00018 * distanceFactor * waterSurfaceStrength;
      vec4 source = texture2D(tDiffuse, clamp(vUv + refraction, 0.001, 0.999));

      vec3 transmittance = beerLambert(uAbsorption, waterDistance);
      float verticalLight = smoothstep(-0.25, 0.72, rayDirection.y);
      vec3 scatteredWater = uScatteringColor
        * (0.72 + verticalLight * 0.28) * uScatteringStrength;
      float farBlend = smoothstep(18.0, 34.0, waterDistance) * 0.18;
      scatteredWater = mix(scatteredWater, uDeepColor, farBlend);
      vec3 color = source.rgb * transmittance + scatteredWater * (1.0 - transmittance);

      float reachesSurface = step(0.02, rayDirection.y);
      float distanceToSurface = max(uSurfaceY - uCameraPosition.y, 0.0)
        / max(rayDirection.y, 0.02);
      float directSun = pow(max(dot(rayDirection, normalize(uSunDirection)), 0.0), 32.0)
        * reachesSurface * smoothstep(0.24, 0.58, rayDirection.y)
        * waterSurfaceStrength;
      vec3 surfaceIntersection = uCameraPosition + rayDirection * distanceToSurface;
      float surfaceWave = sampleSurfaceWave(surfaceIntersection.xz * 0.62, time);
      float sunWave = 0.94 + surfaceWave * 0.10;
      vec3 sunTransmittance = beerLambert(uAbsorption * 0.32, distanceToSurface);
      vec3 directSunRadiance = uSunRadiance
        * directSun * sunWave * sunTransmittance * 0.68;
      color += directSunRadiance;

      float worldStage = stageMask(farWorld);
      float floorVisibility = (1.0 - smoothstep(0.15, 1.8, abs(farWorld.y)))
        * (1.0 - step(0.9999, depth));
      float floorCaustic = sampleCausticField(farWorld.xz * 2.8, time)
        * floorVisibility * worldStage * causticsStrength;
      vec3 causticRadiance = uSunRadiance * vec3(0.36, 0.48, 0.58)
        * floorCaustic * (0.08 + worldStage * 0.20);
      color += causticRadiance;

      float edge = length((vUv - 0.5) * vec2(0.88, 1.0));
      color *= mix(1.0, 0.92, smoothstep(0.34, 0.76, edge));
      float linearDepth = linearizeDepth(depth);
      gl_FragColor = vec4(color, linearDepth);
    }
  `
}

export class UnderwaterPostProcessing {
  private readonly composer: EffectComposer
  private readonly renderPass: RenderPass
  private readonly illuminationPass: UnderwaterIlluminationPass
  private readonly underwaterPass: ShaderPass
  private readonly compositePass: UnderwaterDepthAwareCompositePass
  private readonly outputPass: OutputPass
  private readonly camera: THREE.Camera
  private lightShaftSpeed = 1
  private readonly inverseProjectionView = new THREE.Matrix4()

  constructor(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.Camera,
    quality: EnvironmentQuality,
    optics: UnderwaterOpticsState
  ) {
    this.camera = camera
    const target = new THREE.WebGLRenderTarget(1, 1, {
      type: THREE.HalfFloatType,
      depthBuffer: true,
      stencilBuffer: false
    })
    target.depthTexture = new THREE.DepthTexture(1, 1, THREE.UnsignedIntType)
    target.depthTexture.format = THREE.DepthFormat

    this.composer = new EffectComposer(renderer, target)
    this.composer.setPixelRatio(renderer.getPixelRatio())
    this.composer.renderTarget1.samples = quality === 'high' ? 4 : quality === 'medium' ? 2 : 0
    this.composer.renderTarget2.samples = quality === 'high' ? 4 : quality === 'medium' ? 2 : 0
    this.renderPass = new RenderPass(scene, camera)
    this.illuminationPass = new UnderwaterIlluminationPass(camera, quality, optics)
    this.underwaterPass = new ShaderPass(UNDERWATER_SHADER)
    this.underwaterPass.uniforms.tDepth.value = this.composer.readBuffer.depthTexture
    this.underwaterPass.uniforms.uSunDirection = optics.sunDirection
    this.underwaterPass.uniforms.uSunRadiance = optics.sunRadiance
    this.underwaterPass.uniforms.uSurfaceY = optics.surfaceY
    this.underwaterPass.uniforms.uStageCenter = optics.stageCenter
    this.underwaterPass.uniforms.uDeepColor = optics.deepColor
    this.underwaterPass.uniforms.uAbsorption = optics.absorption
    this.underwaterPass.uniforms.uScatteringColor = optics.scatteringColor
    this.underwaterPass.uniforms.uScatteringStrength = optics.scatteringStrength
    const perspectiveCamera = camera as THREE.PerspectiveCamera
    this.underwaterPass.uniforms.uNear.value = perspectiveCamera.near ?? 0.1
    this.underwaterPass.uniforms.uFar.value = perspectiveCamera.far ?? 100
    this.compositePass = new UnderwaterDepthAwareCompositePass(
      this.illuminationPass,
      quality
    )
    this.outputPass = new OutputPass()
    this.composer.addPass(this.renderPass)
    this.composer.addPass(this.illuminationPass)
    this.composer.addPass(this.underwaterPass)
    this.composer.addPass(this.compositePass)
    this.composer.addPass(this.outputPass)
  }

  render(delta: number): void {
    this.camera.updateMatrixWorld()
    this.inverseProjectionView.multiplyMatrices(
      this.camera.matrixWorld,
      this.camera.projectionMatrixInverse
    )
    this.underwaterPass.uniforms.time.value += Math.max(0, delta)
    this.underwaterPass.uniforms.tDepth.value = this.composer.readBuffer.depthTexture
    this.underwaterPass.uniforms.uInverseProjectionView.value.copy(this.inverseProjectionView)
    this.underwaterPass.uniforms.uCameraPosition.value.setFromMatrixPosition(this.camera.matrixWorld)
    const perspectiveCamera = this.camera as THREE.PerspectiveCamera
    this.underwaterPass.uniforms.uNear.value = perspectiveCamera.near ?? 0.1
    this.underwaterPass.uniforms.uFar.value = perspectiveCamera.far ?? 100
    this.illuminationPass.update(
      delta * this.lightShaftSpeed,
      this.inverseProjectionView,
      this.camera
    )
    this.composer.render(delta)
  }

  setEffectEnabled(name: EnvironmentEffectName, enabled: boolean): void {
    if (name === 'lightShafts') {
      this.illuminationPass.setEffectEnabled(enabled)
      return
    }
    const uniformName = name === 'waterSurface'
      ? 'waterSurfaceStrength'
      : name === 'caustics'
        ? 'causticsStrength'
        : null
    if (uniformName) {
      this.underwaterPass.uniforms[uniformName].value = enabled ? 1 : 0
    }
  }

  setWaterSurfaceStrength(value: number): void {
    this.underwaterPass.uniforms.waterSurfaceStrength.value = THREE.MathUtils.clamp(
      Number.isFinite(value) ? value : 1,
      0,
      2.5
    )
  }

  setLightShaftSpeed(value: number): void {
    this.lightShaftSpeed = THREE.MathUtils.clamp(
      Number.isFinite(value) ? value : 1,
      0,
      10
    )
  }

  getLightShaftSpeed(): number {
    return this.lightShaftSpeed
  }

  setSize(width: number, height: number): void {
    this.composer.setSize(width, height)
  }

  dispose(): void {
    this.renderPass.dispose()
    this.illuminationPass.dispose()
    this.underwaterPass.dispose()
    this.compositePass.dispose()
    this.outputPass.dispose()
    this.composer.dispose()
  }
}
