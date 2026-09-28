import * as THREE from 'three';
import { WATER_WAVES_GLSL, WATER_FIELD_GLSL, WAVE_PERIOD, WATER_INDEX } from './waves.js';

// Refract a regular grid through the same waves the viewer sees. Compressed
// patches concentrate sunlight; overlapping folds add, rather than sliding a picture.
export class WaterCaustics {
  constructor(uniforms) {
    this.waveTarget = new THREE.WebGLRenderTarget(512, 512, {
      type: THREE.HalfFloatType, depthBuffer: false, stencilBuffer: false,
      wrapS: THREE.RepeatWrapping, wrapT: THREE.RepeatWrapping,
      minFilter: THREE.LinearMipmapLinearFilter, magFilter: THREE.LinearFilter,
      generateMipmaps: true,
    });
    this.waveTarget.texture.name = 'Water height and slope';
    this.waveTarget.texture.anisotropy = 16;
    uniforms.uwWaves.value = this.waveTarget.texture;
    this.waveScene = new THREE.Scene();
    this.waveMaterial = new THREE.ShaderMaterial({
      uniforms: { uwTime: uniforms.uwWaveTime, uwWaveScale: { value: 1 }, uwWaveDetail: { value: 1 } },
      depthTest: false, depthWrite: false, toneMapped: false,
      vertexShader: `varying vec2 p; void main() { p = position.xy * ${(WAVE_PERIOD / 2).toFixed(1)}; gl_Position = vec4(position.xy, 0.0, 1.0); }`,
      fragmentShader: /* glsl */ `
        uniform float uwTime, uwWaveScale, uwWaveDetail;
        varying vec2 p;
        ${WATER_WAVES_GLSL}
        void main() { gl_FragColor = vec4(waterWave(p, uwTime) * uwWaveScale, 1.0); }
      `,
    });
    this.waveMesh = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.waveMaterial);
    this.waveMesh.frustumCulled = false;
    this.waveScene.add(this.waveMesh);
    this.target = new THREE.WebGLRenderTarget(1024, 1024, {
      type: THREE.HalfFloatType, depthBuffer: false, stencilBuffer: false,
      wrapS: THREE.RepeatWrapping, wrapT: THREE.RepeatWrapping,
      minFilter: THREE.LinearMipmapLinearFilter, magFilter: THREE.LinearFilter,
      generateMipmaps: true,
    });
    this.target.texture.name = 'Water light density';
    this.target.texture.anisotropy = 16;
    this.scene = new THREE.Scene();
    this.camera = new THREE.Camera();
    this.material = new THREE.ShaderMaterial({
      uniforms: {
        uwWaves: uniforms.uwWaves, uwSurfaceY: uniforms.uwSurfaceY,
        uwFloorY: uniforms.uwFloorY, uwSunDir: uniforms.uwSunDir,
        uwSunAirDir: uniforms.uwSunAirDir,
      },
      side: THREE.DoubleSide, depthTest: false, depthWrite: false, transparent: true,
      blending: THREE.CustomBlending, blendSrc: THREE.OneFactor,
      blendDst: THREE.OneFactor, blendEquation: THREE.AddEquation, toneMapped: false,
      vertexShader: /* glsl */ `
        uniform float uwSurfaceY, uwFloorY;
        uniform vec3 uwSunDir, uwSunAirDir;
        ${WATER_FIELD_GLSL}
        varying vec2 sourcePosition, floorPosition;
        void main() {
          float depth = uwSurfaceY - uwFloorY;
          // Center the incoming grid over the destination, with 2 m of margin on
          // each side for light refracted across a tile boundary.
          vec2 source = position.xy + uwSunDir.xz * depth / uwSunDir.y;
          vec3 wave = textureLod(uwWaves, waterUV(source), 0.0).xyz;
          vec3 ray = refract( -uwSunAirDir, waterNormal(wave), ${(1 / WATER_INDEX).toFixed(9)} );
          vec2 hit = source + ray.xz * ( depth + wave.x ) / max( -ray.y, 0.15 );
          sourcePosition = source;
          floorPosition = hit;
          gl_Position = vec4( hit * ${(2 / WAVE_PERIOD).toFixed(9)}, 0.0, 1.0 );
        }
      `,
      fragmentShader: /* glsl */ `
        varying vec2 sourcePosition, floorPosition;
        float area( vec2 p ) {
          vec2 dx = dFdx(p), dy = dFdy(p);
          return abs(dx.x * dy.y - dx.y * dy.x);
        }
        void main() {
          float density = min( area(sourcePosition) / max( area(floorPosition), 1e-7 ), 8.0 );
          // The second channel carries only concentrated light for volumetric shafts.
          gl_FragColor = vec4( density, max( density - 1.3, 0.0 ) * 0.18, 0.0, 1.0 );
        }
      `,
    });
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(WAVE_PERIOD + 4, WAVE_PERIOD + 4, 512, 512), this.material);
    this.mesh.frustumCulled = false;
    this.scene.add(this.mesh);
    this.clearColor = new THREE.Color();
  }

  render(renderer) {
    if (!renderer.extensions.has('EXT_color_buffer_float') && !renderer.extensions.has('EXT_color_buffer_half_float')) {
      throw new Error('この環境では海の光を描画できません。');
    }
    const previousTarget = renderer.getRenderTarget();
    const previousAlpha = renderer.getClearAlpha();
    renderer.getClearColor(this.clearColor);
    try {
      renderer.setRenderTarget(this.waveTarget);
      renderer.setClearColor(0x000000, 0);
      renderer.clear();
      renderer.render(this.waveScene, this.camera);
      renderer.setRenderTarget(this.target);
      renderer.setClearColor(0x000000, 0);
      renderer.clear();
      renderer.render(this.scene, this.camera);
    } finally {
      renderer.setRenderTarget(previousTarget);
      renderer.setClearColor(this.clearColor, previousAlpha);
    }
  }

  dispose() {
    this.waveMesh.geometry.dispose();
    this.waveMaterial.dispose();
    this.waveTarget.dispose();
    this.mesh.geometry.dispose();
    this.material.dispose();
    this.target.dispose();
  }
}
