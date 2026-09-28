import * as THREE from 'three';
import { createWaterSurface } from './water-surface.js';
import { WaterCaustics } from './caustics.js';
import { SEA_DEFAULTS, normalizeSeaSettings } from './sea-debug.js';
import { createAmbientBubbleField } from './AmbientBubbleField.js';
import { createSuspendedParticleField } from './SuspendedParticleField.js';
import { createUnderwaterOptics, UNDERWATER_OPTICS_GLSL, UNDERWATER_SURFACE_GLSL } from './optics.js';

const FLOOR_VERTEX = /* glsl */ `
#include <common>
#include <shadowmap_pars_vertex>
varying vec3 vWorld;
varying vec3 vNormalWorld;
void main() {
  vec3 transformed = position;
  vec3 transformedNormal = normalMatrix * normal;
  vec4 worldPosition = modelMatrix * vec4( transformed, 1.0 );
  vWorld = worldPosition.xyz;
  vNormalWorld = normalize( mat3( modelMatrix ) * normal );
  gl_Position = projectionMatrix * viewMatrix * worldPosition;
  #include <shadowmap_vertex>
}`;

const FLOOR_FRAGMENT = /* glsl */ `
#include <common>
#include <packing>
#include <shadowmap_pars_fragment>
${UNDERWATER_OPTICS_GLSL}
${UNDERWATER_SURFACE_GLSL}
uniform sampler2D sand, relief;
uniform float uwShadowSpan;
uniform float uwShadowDepth;
varying vec3 vWorld;
varying vec3 vNormalWorld;

vec2 uwDisk( int i, int count ) {
  float angle = float( i ) * 2.3999632;
  return vec2( cos( angle ), sin( angle ) ) * sqrt( ( float( i ) + 0.5 ) / float( count ) );
}

#if defined( USE_SHADOWMAP ) && NUM_DIR_LIGHT_SHADOWS > 0
// Bilinear comparison, so a tap slides across texels instead of stepping between them.
float uwShadowTap( vec2 uv, float receiver, vec2 size ) {
  vec2 texel = uv * size - 0.5;
  vec2 base = ( floor( texel ) + 0.5 ) / size;
  vec2 f = fract( texel );
  vec2 next = 1.0 / size;
  return mix(
    mix( texture2DCompare( directionalShadowMap[ 0 ], base, receiver ), texture2DCompare( directionalShadowMap[ 0 ], base + vec2( next.x, 0.0 ), receiver ), f.x ),
    mix( texture2DCompare( directionalShadowMap[ 0 ], base + vec2( 0.0, next.y ), receiver ), texture2DCompare( directionalShadowMap[ 0 ], base + next, receiver ), f.x ),
    f.y );
}
#endif

// Light scattered by the water widens a penumbra with the occluder's height above the sand:
// crisp beside the feet, soft under the head. Nearby blockers in the map give that height.
float characterShadow() {
  float lit = 1.0;
  #if defined( USE_SHADOWMAP ) && NUM_DIR_LIGHT_SHADOWS > 0
    DirectionalLightShadow light = directionalLightShadows[ 0 ];
    vec3 coord = vDirectionalShadowCoord[ 0 ].xyz / vDirectionalShadowCoord[ 0 ].w;
    float receiver = coord.z + light.shadowBias;
    if ( all( greaterThan( coord, vec3( 0.0 ) ) ) && all( lessThan( coord, vec3( 1.0 ) ) ) ) {
      float found = 0.0, blocker = 0.0;
      for ( int i = 0; i < 16; i ++ ) {
        float depth = unpackRGBAToDepth( texture2D( directionalShadowMap[ 0 ], coord.xy + uwDisk( i, 16 ) * ( 0.16 / uwShadowSpan ) ) );
        if ( depth < receiver ) { found += 1.0; blocker += depth; }
      }
      if ( found > 0.0 ) {
        float gap = ( receiver - blocker / found ) * uwShadowDepth;
        float radius = ( 0.004 + gap * 0.055 ) / uwShadowSpan;
        lit = 0.0;
        for ( int i = 0; i < 16; i ++ ) lit += uwShadowTap( coord.xy + uwDisk( i, 16 ) * radius, receiver, light.shadowMapSize );
        lit = mix( 1.0, lit / 16.0, light.shadowIntensity );
      }
    }
  #endif
  return lit;
}

void main() {
  vec2 uv = vWorld.xz * 0.11;
  vec3 grain = texture2D( sand, uv ).rgb;
  vec3 bump = texture2D( relief, uv ).rgb * 2.0 - 1.0;
  vec3 n = normalize( vNormalWorld + vec3( bump.x, 0.0, -bump.y ) * 0.45 );
  float grey = dot( grain, vec3( 0.2126, 0.7152, 0.0722 ) );
  vec3 albedo = vec3( 0.78, 0.84, 0.88 ) * ( 0.76 + grey * 0.28 ) * ( 0.9 + bump.z * 0.12 );

  vec3 caustic = uwCausticLight( vWorld.xz, 0.0 );
  float shadow = characterShadow();
  vec3 sun = uwSunColor * uwSunAttenuation( vWorld.y ) * max( dot( n, uwSunDir ), 0.0 );
  vec3 skylight = mix( uwHorizonColor, uwZenithColor, 0.75 ) * ( 0.8 + 0.2 * shadow );
  // Part of the sunlight arrives already scattered, so a shadow in water is never black.
  vec3 color = albedo * ( sun * ( caustic * shadow * 0.27 + 0.10 ) + skylight * 0.7 );
  color = uwApplyMedium( color, vWorld, 12 );
  gl_FragColor = vec4( color, 1.0 );
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  gl_FragColor = uwDither( gl_FragColor, gl_FragCoord.xy );
}`;

const BACKDROP_VERTEX = /* glsl */ `
varying vec3 vWorld;
void main() {
  vec4 p = modelMatrix * vec4( position, 1.0 );
  vWorld = p.xyz;
  gl_Position = projectionMatrix * viewMatrix * p;
}`;

const BACKDROP_FRAGMENT = /* glsl */ `
${UNDERWATER_OPTICS_GLSL}
varying vec3 vWorld;
void main() {
  vec3 dir = normalize( vWorld - cameraPosition );
  gl_FragColor = vec4( uwInscatter( cameraPosition, dir, 400.0, 12 ), 1.0 );
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  gl_FragColor = uwDither( gl_FragColor, gl_FragCoord.xy );
}`;

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
    this.baseColors = Object.fromEntries(['uwHorizonColor', 'uwZenithColor', 'uwAbyssColor', 'uwGlowColor', 'uwFloorAverage'].map(key => [key, this.optics.uniforms[key].value.clone()]));
    this.caustics = new WaterCaustics(this.optics.uniforms);
    this.optics.uniforms.uwCaustics.value = this.caustics.target.texture;
    this.optics.uniforms.uwCausticTexels.value = this.caustics.target.width;
    this.water = createWaterSurface(this.optics.uniforms);
    this.group.add(this.water.mesh);
    this.bubbles = createAmbientBubbleField(150, 3);
    this.bubbles.material.uniforms.verticalDensity.value = 0.85;
    this.bubbles.material.uniforms.horizontalDensity.value = 2.8;
    this.group.add(this.bubbles);
    this.particles = createSuspendedParticleField(this.optics.uniforms, 4000);
    this.group.add(this.particles.points);
    // Characters are lit by the same sun that draws the shafts and caustics, plus the glow of
    // the water around them. The fill stands in for light scattered back toward the viewer.
    const sunDirection = this.optics.sunDirection;
    this.hemisphere = new THREE.HemisphereLight(0x9fe0f2, 0x8fa294, 1.55);
    // Characters attenuate this by depth like the sand does, so it starts above the light that reaches them.
    this.sun = new THREE.DirectionalLight(0xfff3df, 2.7);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(512, 512);
    this.sun.shadow.bias = -0.0006;
    this.sun.shadow.normalBias = 0.02;
    this.fill = new THREE.DirectionalLight(0xc6ecff, 0.75);
    this.fill.position.set(1.2, 2.2, 4.0);
    this.group.add(this.hemisphere, this.sun, this.sun.target, this.fill, this.fill.target);
    this.sunDirection = sunDirection;
    this.shadowUniforms = { uwShadowSpan: { value: 1 }, uwShadowDepth: { value: 1 } };
    this.fitShadow([]);
    this.configure(this.settings);
  }

  configure(settings) {
    this.settings = normalizeSeaSettings(settings);
    const { waveSize, waveDetail, surfaceClarity, shaftStrength, shaftRange, shaftDepth, blue, visibility, causticTransparency, particles, bubbles } = this.settings;
    const u = this.optics.uniforms;
    this.caustics.waveMaterial.uniforms.uwWaveScale.value = waveSize / 100;
    this.caustics.waveMaterial.uniforms.uwWaveDetail.value = waveDetail / 100;
    u.uwSurfaceClarity.value = surfaceClarity / 100;
    u.uwShaftStrength.value = 0.4 * shaftStrength / 100;
    u.uwShaftRange.value = shaftRange;
    u.uwShaftDepth.value = shaftDepth;
    u.uwCausticContrast.value = 1 - causticTransparency / 100;
    u.uwExtinction.value.set(1.7, 1.0, 0.62).multiplyScalar(-Math.log(0.05) / visibility);
    for (const [key, color] of Object.entries(this.baseColors)) {
      const hsl = color.getHSL({});
      u[key].value.setHSL(hsl.h + (blue - 50) / 500, hsl.s, hsl.l);
    }
    this.particles.points.geometry.setDrawRange(0, particles);
    this.particles.points.visible = particles > 0;
    this.bubbles.material.uniforms.verticalDensity.value = 5;
    this.bubbles.geometry.setDrawRange(0, bubbles);
    this.bubbles.visible = bubbles > 0;
  }

  // The shadow camera covers only the characters so the map keeps its resolution where it matters.
  fitShadow(avatars) {
    const center = new THREE.Vector3(0, 0, -0.55);
    if (avatars.length) {
      center.set(0, 0, 0);
      for (const avatar of avatars) center.add(avatar.root.position);
      center.divideScalar(avatars.length);
    }
    let radius = 1.1;
    for (const avatar of avatars) radius = Math.max(radius, avatar.root.position.distanceTo(center) + 1.1);
    center.y = 0.85;
    const camera = this.sun.shadow.camera;
    camera.left = camera.bottom = -radius;
    camera.right = camera.top = radius;
    camera.near = 1;
    camera.far = 16;
    camera.updateProjectionMatrix();
    this.shadowUniforms.uwShadowSpan.value = radius * 2;
    this.shadowUniforms.uwShadowDepth.value = camera.far - camera.near;
    this.sun.target.position.copy(center);
    this.sun.position.copy(center).addScaledVector(this.sunDirection, 8);
    this.sun.target.updateMatrixWorld();
    this.sun.updateMatrixWorld();
  }

  async load() {
    const loader = new THREE.TextureLoader();
    const load = async name => {
      const texture = await loader.loadAsync(new URL(`../resources/world/${name}`, import.meta.url).href);
      if (this.disposed) { texture.dispose(); throw new Error('World closed'); }
      this.textures.push(texture);
      texture.anisotropy = 4;
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
    this.floorMaterial = new THREE.ShaderMaterial({
      uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.lights, { sand: { value: null }, relief: { value: null } }]),
      vertexShader: FLOOR_VERTEX,
      fragmentShader: FLOOR_FRAGMENT,
      lights: true,
    });
    // Merged uniforms are copies; the shared optics must stay live references.
    Object.assign(this.floorMaterial.uniforms, this.optics.uniforms, this.shadowUniforms);
    this.floorMaterial.uniforms.sand.value = sand;
    this.floorMaterial.uniforms.relief.value = normal;
    // The sand stays level so it never shows an edge: distance alone dissolves it into the water.
    const floor = new THREE.PlaneGeometry(400, 400, 128, 128);
    floor.rotateX(-Math.PI / 2);
    const positions = floor.attributes.position;
    for (let i = 0; i < positions.count; i++) {
      const x = positions.getX(i), z = positions.getZ(i);
      positions.setY(i, -0.05 + Math.sin(x * .12 + z * .07) * .025);
    }
    floor.computeVertexNormals();
    const floorMesh = new THREE.Mesh(floor, this.floorMaterial);
    floorMesh.name = 'Environment:seaFloor';
    floorMesh.receiveShadow = true;
    this.group.add(floorMesh);
    this.backdropMaterial = new THREE.ShaderMaterial({
      uniforms: this.optics.uniforms, vertexShader: BACKDROP_VERTEX, fragmentShader: BACKDROP_FRAGMENT,
      side: THREE.BackSide, depthWrite: false,
    });
    // Drawn after the floor and surface so only the uncovered band of open water is shaded.
    const dome = new THREE.Mesh(new THREE.SphereGeometry(190, 48, 24), this.backdropMaterial);
    dome.name = 'Environment:openWater';
    dome.renderOrder = 10;
    dome.frustumCulled = false;
    this.group.add(dome);
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
    this.bubbles.material.uniforms.time.value = this.time * 1.8;
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
