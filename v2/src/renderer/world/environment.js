import * as THREE from 'three';
import { createWaterSurface } from './water-surface.js';
import { WaterCaustics } from './caustics.js';
import { SEA_DEFAULTS, normalizeSeaSettings, seaControl } from './sea-settings.js';
import { seaRenderValues } from './sea-render-values.js';
import {
  createAmbientBubbleField,
  AMBIENT_BUBBLE_VERTICAL_DENSITY_MAX,
  AMBIENT_BUBBLE_STREAM_COUNT,
  AMBIENT_BUBBLE_TIME_SCALE,
} from './AmbientBubbleField.js';
import { createSuspendedParticleField } from './SuspendedParticleField.js';
import { createUnderwaterOptics } from './optics.js';
import { createSeaFloor } from './sea-floor.js';
import { createOpenWaterDome } from './sea-backdrop.js';

const SHADOW_MAP_SIZE = 512;
const SHADOW_BIAS = -0.0006;
const SHADOW_NORMAL_BIAS = 0.02;
const SHADOW_NEAR = 1;
const SHADOW_FAR = 16;
const SUN_DISTANCE = 8;
const CHARACTER_PADDING = 1.1;
const SHADOW_CENTER_Y = 0.85;
// Same stand the world display uses when one resident is placed alone.
const EMPTY_STAGE_Z = -0.55;
const SAND_ANISOTROPY = 4;
const SEA_COLOR_UNIFORMS = ['uwHorizonColor', 'uwZenithColor', 'uwAbyssColor', 'uwGlowColor', 'uwFloorAverage'];

export class UnderwaterEnvironment {
  constructor(scene) {
    this.scene = scene;
    this.group = new THREE.Group();
    this.textures = [];
    this.time = 0;
    this.waveTime = 0;
    this.settings = { ...SEA_DEFAULTS };
    scene.add(this.group);
    this.optics = createUnderwaterOptics();
    this.baseColors = Object.fromEntries(SEA_COLOR_UNIFORMS.map(key => [key, this.optics.uniforms[key].value.clone()]));
    this.caustics = new WaterCaustics(this.optics.uniforms);
    this.optics.uniforms.uwCaustics.value = this.caustics.target.texture;
    this.optics.uniforms.uwCausticTexels.value = this.caustics.target.width;
    this.group.add(createWaterSurface(this.optics.uniforms).mesh);
    this.bubbles = createAmbientBubbleField(seaControl('bubbles').max, AMBIENT_BUBBLE_STREAM_COUNT);
    this.group.add(this.bubbles);
    this.particles = createSuspendedParticleField(this.optics.uniforms, seaControl('particles').max);
    this.group.add(this.particles.points);
    // Characters are lit by the same sun that draws the shafts and caustics, plus the glow of
    // the water around them. The fill stands in for light scattered back toward the viewer.
    const sunDirection = this.optics.sunDirection;
    const hemisphere = new THREE.HemisphereLight(0x9fe0f2, 0x8fa294, 1.55);
    // Characters attenuate this by depth like the sand does, so it starts above the light that reaches them.
    this.sun = new THREE.DirectionalLight(0xfff3df, 2.7);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(SHADOW_MAP_SIZE, SHADOW_MAP_SIZE);
    this.sun.shadow.bias = SHADOW_BIAS;
    this.sun.shadow.normalBias = SHADOW_NORMAL_BIAS;
    this.fill = new THREE.DirectionalLight(0xc6ecff, 0.75);
    this.fill.position.set(1.2, 2.2, 4.0);
    this.group.add(hemisphere, this.sun, this.sun.target, this.fill, this.fill.target);
    this.sunDirection = sunDirection;
    this.shadowUniforms = { uwShadowSpan: { value: 1 }, uwShadowDepth: { value: 1 } };
    this.fitShadow([]);
    this.configure(this.settings);
  }

  configure(settings) {
    this.settings = normalizeSeaSettings(settings);
    const sea = seaRenderValues(this.settings);
    const uniforms = this.optics.uniforms;
    this.caustics.waveMaterial.uniforms.uwWaveScale.value = sea.waveScale;
    this.caustics.waveMaterial.uniforms.uwWaveDetail.value = sea.waveDetail;
    uniforms.uwSurfaceClarity.value = sea.surfaceClarity;
    uniforms.uwShaftStrength.value = sea.shaftStrength;
    uniforms.uwShaftRange.value = sea.shaftRange;
    uniforms.uwShaftDepth.value = sea.shaftDepth;
    uniforms.uwCausticContrast.value = sea.causticContrast;
    uniforms.uwExtinction.value.copy(sea.extinction);
    for (const [key, color] of Object.entries(this.baseColors)) {
      const hsl = color.getHSL({});
      uniforms[key].value.setHSL(hsl.h + sea.blueHueOffset, hsl.s, hsl.l);
    }
    this.particles.points.geometry.setDrawRange(0, sea.particles);
    this.particles.points.visible = sea.particles > 0;
    // The shader's density gate stays fully open; the bubble slider clips the draw range.
    this.bubbles.material.uniforms.verticalDensity.value = AMBIENT_BUBBLE_VERTICAL_DENSITY_MAX;
    this.bubbles.geometry.setDrawRange(0, sea.bubbles);
    this.bubbles.visible = sea.bubbles > 0;
  }

  // The shadow camera covers only the characters so the map keeps its resolution where it matters.
  fitShadow(avatars) {
    const center = new THREE.Vector3(0, 0, EMPTY_STAGE_Z);
    if (avatars.length) {
      center.set(0, 0, 0);
      for (const avatar of avatars) center.add(avatar.root.position);
      center.divideScalar(avatars.length);
    }
    let radius = CHARACTER_PADDING;
    for (const avatar of avatars) radius = Math.max(radius, avatar.root.position.distanceTo(center) + CHARACTER_PADDING);
    center.y = SHADOW_CENTER_Y;
    const camera = this.sun.shadow.camera;
    camera.left = camera.bottom = -radius;
    camera.right = camera.top = radius;
    camera.near = SHADOW_NEAR;
    camera.far = SHADOW_FAR;
    camera.updateProjectionMatrix();
    this.shadowUniforms.uwShadowSpan.value = radius * 2;
    this.shadowUniforms.uwShadowDepth.value = camera.far - camera.near;
    this.sun.target.position.copy(center);
    this.sun.position.copy(center).addScaledVector(this.sunDirection, SUN_DISTANCE);
    this.sun.target.updateMatrixWorld();
    this.sun.updateMatrixWorld();
  }

  async load() {
    const loader = new THREE.TextureLoader();
    const load = async name => {
      const texture = await loader.loadAsync(new URL(`../resources/world/${name}`, import.meta.url).href);
      if (this.disposed) { texture.dispose(); throw new Error('World closed'); }
      this.textures.push(texture);
      texture.anisotropy = SAND_ANISOTROPY;
      return texture;
    };
    // All requests settle before cleanup so a late load cannot retain a texture.
    const loaded = await Promise.allSettled([
      load('ground-sand-005-color-2k.webp'), load('ground-sand-005-normal-2k.webp'),
    ]);
    if (this.disposed || loaded.some(result => result.status === 'rejected')) throw new Error('海の素材を読み込めませんでした。');
    const [sand, normal] = loaded.map(result => result.value);
    for (const texture of [sand, normal]) texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    sand.colorSpace = THREE.SRGBColorSpace;
    this.group.add(createSeaFloor(sand, normal, this.optics.uniforms, this.shadowUniforms));
    this.group.add(createOpenWaterDome(this.optics.uniforms));
  }

  resize(heightPixels, fovDegrees) {
    this.particles.resize(heightPixels, fovDegrees);
  }

  renderCaustics(renderer) { this.caustics.render(renderer); }

  update(delta) {
    this.time += delta;
    this.waveTime += delta * this.settings.waveSpeed;
    this.optics.update(this.time);
    this.optics.uniforms.uwWaveTime.value = this.waveTime;
    this.bubbles.material.uniforms.time.value = this.time * AMBIENT_BUBBLE_TIME_SCALE;
  }

  dispose() {
    this.disposed = true;
    this.group.removeFromParent();
    this.group.traverse(object => { object.geometry?.dispose(); object.material?.dispose(); });
    this.sun.dispose(); this.fill.dispose();
    this.caustics.dispose();
    this.textures.forEach(texture => texture.dispose());
    this.textures.length = 0;
  }
}
